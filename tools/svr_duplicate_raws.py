"""Duplicate RAWs in the master: the same camera file twice in one species folder.

ONE JOB: find RAW/HEIC files in the SAME species folder whose bytes are
identical (`N4261484.ORF` + `2024 april Spanje-N4261484.ORF`, `NA122724.ORF` +
`NA122724-2.ORF`, `P7117396.ORF` + `P7117396A.ORF`, ...). Each copy is a whole
picture of its own (its own XMP, JPGs, stars, row in the lists), so the species
counters count that click twice. Keep the richest picture of each group and
delete the others with the master's own delete (`db.master_mutate.delete_picture`):
files to the master trash, rows out of the lists, the species' website-picture
list and best picks re-derived, undoable from the master trash.

INVARIANT: a picture is only deleted when its RAW is byte-identical to the kept
picture's RAW (checked again right before the delete), both are in the same
folder, and nothing of it is lost that the kept one does not carry — a
different name/qualifier, a place name or GPS only on the copy, or more stars
on the copy puts the group on HOLD. The same RAW under two DIFFERENT species
(two species in one photo) is never touched.

Which picture is kept, in order: the species' best picture > has JPGs on the
species page > more stars > more JPGs > has a PhotoLab .dop > more files >
the shortest (plain camera) name.

Usage (from the repository root; close SpeciesViewer before --apply):
  python tools/svr_duplicate_raws.py                    # dry run: report only
  python tools/svr_duplicate_raws.py --apply            # delete the copies
  python tools/svr_duplicate_raws.py --apply --pages    # ... and rebuild + upload
                                                        #     the species pages
  python tools/svr_duplicate_raws.py --pages-only       # only the pages (after --apply)
"""
from __future__ import annotations

import argparse
import csv
import filecmp
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))

JPG_EXT = (".jpg", ".jpeg")


# ── find ─────────────────────────────────────────────────────────────────────

def identical_groups(biota: Path) -> list[list[Path]]:
    """Groups (2+) of byte-identical RAW/HEIC files, per folder."""
    from utils.raw_extensions import RAW, HEIC
    exts = RAW | HEIC
    groups = []
    for d, _dirs, files in os.walk(biota):
        by_size = defaultdict(list)
        for f in files:
            if os.path.splitext(f)[1].lower() in exts:
                p = Path(d) / f
                try:
                    by_size[p.stat().st_size].append(p)
                except OSError:
                    pass
        for size, paths in by_size.items():
            if size == 0 or len(paths) < 2:
                continue
            todo = sorted(paths, key=lambda p: (len(p.name), p.name.lower()))
            while todo:
                head, rest = todo[0], todo[1:]
                same = [p for p in rest if filecmp.cmp(head, p, shallow=False)]
                if same:
                    groups.append([head] + same)
                todo = [p for p in rest if p not in same]
    return groups


def two_species_pairs(biota: Path) -> int:
    """How many RAW names occur in more than one folder (left alone; reported)."""
    from utils.raw_extensions import RAW, HEIC
    seen = defaultdict(set)
    for d, _dirs, files in os.walk(biota):
        for f in files:
            if os.path.splitext(f)[1].lower() in RAW | HEIC:
                seen[f.lower()].add(d)
    return sum(1 for v in seen.values() if len(v) > 1)


# ── decide ───────────────────────────────────────────────────────────────────

def _file_index(db) -> dict[str, list[str]]:
    """lower-case file name -> [UPN, ...] for every file row in the master."""
    idx = defaultdict(list)
    for r in db._connect().execute(
            'SELECT "File:FileName" f, "UniquePictureName" u FROM files'):
        idx[(r["f"] or "").lower()].append(r["u"])
    return idx


def _species_refs(db) -> tuple[set, set]:
    """(best UPNs, JPG file names on a species page), lower-case."""
    import ast
    import json
    best, onpage = set(), set()
    for s in db._connect().execute('SELECT "BestUPN" u, "JPGmap" m, "BestJPG" j FROM species'):
        if s["u"]:
            best.add(str(s["u"]).lower())
        jm = s["m"] or ""
        try:
            jm = json.loads(jm) if jm.strip() else []
        except ValueError:
            try:
                jm = ast.literal_eval(jm)
            except (ValueError, SyntaxError):
                jm = []
        onpage.update(os.path.basename(str(x)).lower() for x in (jm or []))
        if s["j"]:
            onpage.add(os.path.basename(str(s["j"])).lower())
    return best, onpage


def _facts(db, path: Path, idx, best, onpage) -> dict | None:
    upns = idx.get(path.name.lower(), [])
    if len(upns) != 1:
        return {"path": path, "error": f"{path.name}: in {len(upns)} pictures of the lists"}
    upn = upns[0]
    p = db.picture(upn)
    if p is None:
        return {"path": path, "error": f"{path.name}: picture {upn} not found"}
    files = list(db.files_for_upns([upn]))
    jpgs = [f for f in files if f.lower().endswith(JPG_EXT) and "-small" not in f.lower()]
    return {
        "path": path, "upn": upn, "files": files, "jpgs": jpgs,
        "stars": int(float(p.get("Rating") or 0)), "label": p.get("Label") or "",
        "subject": str(p.get("subject") or ""), "species": p.get("WetSoortNaam") or "",
        "gps": p.get("GPSPosition") or "", "place": p.get("Location") or "",
        "best": upn.lower() in best,
        "on_page": [f for f in jpgs if f.lower() in onpage],
        "dop": any(f.lower().endswith(".dop") for f in files),
    }


def _rank(f):
    return (f["best"], len(f["on_page"]), f["stars"], len(f["jpgs"]), f["dop"],
            len(f["files"]), -len(f["path"].name))


def plan(db, groups, *, allow_fewer_stars: bool = False) -> list[dict]:
    """One item per picture to delete: {keep, drop, size, hold: [...], notes: [...]}."""
    idx = _file_index(db)
    best, onpage = _species_refs(db)
    items = []
    for g in groups:
        facts = [_facts(db, p, idx, best, onpage) for p in g]
        bad = [f["error"] for f in facts if "error" in f]
        if bad or len({f["upn"] for f in facts}) != len(facts):
            items.append({"keep": None, "drop": None, "size": g[0].stat().st_size,
                          "paths": g, "hold": bad or ["the copies are one picture"],
                          "notes": []})
            continue
        facts.sort(key=_rank, reverse=True)
        keep = facts[0]
        for drop in facts[1:]:
            hold, notes = [], []
            if drop["species"] != keep["species"]:
                hold.append("a different species in the lists")
            if drop["subject"].strip().lower() != keep["subject"].strip().lower():
                hold.append(f"names differ: {drop['subject']} / kept {keep['subject']}")
            if drop["gps"] and not keep["gps"]:
                hold.append("only the copy has GPS")
            if drop["place"] and not keep["place"]:
                hold.append("only the copy has a place name")
            if drop["stars"] > keep["stars"]:
                (notes if allow_fewer_stars else hold).append(
                    f"the copy has more stars ({drop['stars']} vs {keep['stars']})")
            for fn in drop["files"]:
                n = len(idx.get(fn.lower(), []))
                if n != 1:
                    hold.append(f"{fn} is in {n} pictures (a delete by name would hit them all)")
            if drop["on_page"]:
                notes.append("its JPG is on the species page; the page is rebuilt")
            if drop["best"]:
                notes.append("it is the species' best picture; re-picked")
            items.append({"keep": keep, "drop": drop, "size": drop["path"].stat().st_size,
                          "paths": g, "hold": hold, "notes": notes})
    return items


# ── report / apply ───────────────────────────────────────────────────────────

def write_report(items, master: Path) -> Path:
    from utils.cache_paths import app_data_dir
    out = app_data_dir() / "reports" / f"duplicate_raws_{datetime.now():%Y%m%d-%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["action", "species", "folder", "keep picture", "delete picture", "MB",
                    "keep stars", "delete stars", "keep label", "delete label",
                    "keep JPGs", "delete JPGs", "hold because", "notes", "files deleted"])
        for it in sorted(items, key=lambda i: (bool(i["hold"]),
                                               i["keep"]["species"] if i["keep"] else "")):
            k, d = it["keep"], it["drop"]
            folder = os.path.relpath(it["paths"][0].parent, master / "biota")
            if k is None:
                w.writerow(["HOLD", "", folder, "", "", round(it["size"] / 1e6, 1)] +
                           [""] * 6 + [" / ".join(it["hold"]),
                                       " | ".join(p.name for p in it["paths"]), ""])
                continue
            w.writerow(["HOLD" if it["hold"] else "delete", k["species"], folder,
                        k["upn"], d["upn"], round(it["size"] / 1e6, 1),
                        k["stars"], d["stars"], k["label"], d["label"],
                        " | ".join(k["jpgs"]), " | ".join(d["jpgs"]),
                        " / ".join(it["hold"]), " / ".join(it["notes"]),
                        " | ".join(d["files"])])
    return out


def apply(db, master: Path, items) -> tuple[list[str], list[str], int]:
    """Delete every non-HOLD copy. Returns (species touched, problems, bytes)."""
    from db.master_mutate import delete_picture, MasterMutateError
    species, problems, freed = set(), [], 0
    go = [i for i in items if not i["hold"]]
    for n, it in enumerate(go, 1):
        k, d = it["keep"], it["drop"]
        try:   # the bytes again, right before: nothing changed since the plan
            if not (k["path"].exists() and d["path"].exists()
                    and filecmp.cmp(k["path"], d["path"], shallow=False)):
                problems.append(f"{d['upn']}: no longer identical to {k['upn']}; skipped")
                continue
            delete_picture(db, master, d["upn"], allow_jpg=True)
        except (OSError, MasterMutateError) as e:
            problems.append(f"{d['upn']}: {e}")
            continue
        species.add(k["species"])
        freed += it["size"]
        if n % 50 == 0 or n == len(go):
            print(f"  {n}/{len(go)} deleted", flush=True)
    return sorted(species), problems, freed


def rebuild_pages(db, scis) -> list[str]:
    from engine.regenerate_species import regenerate_species_page
    failed = []
    for n, sci in enumerate(scis, 1):
        res = regenerate_species_page(sci, db, upload=True)
        print(f"  {n}/{len(scis)} {sci}: {'ok' if res.get('ok') else res.get('error')}",
              flush=True)
        if not res.get("ok"):
            failed.append(sci)
    return failed


def _species_file(master: Path) -> Path:
    return master / "admin" / "status" / "duplicate_raws_species.txt"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", help="master folder (default: the configured one)")
    ap.add_argument("--apply", action="store_true", help="delete the copies (to the master trash)")
    ap.add_argument("--pages", action="store_true",
                    help="after --apply: rebuild and upload the species pages it touched")
    ap.add_argument("--pages-only", action="store_true",
                    help="only rebuild the pages of the last --apply")
    ap.add_argument("--allow-fewer-stars", action="store_true",
                    help="also delete a copy that has more stars than the kept picture")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    t0 = time.time()

    master = a.master
    if not master:
        from utils.config import _config
        master = _config.get("master", "masterbiotadatabase", fallback="")
    master = Path(master.strip().strip('"'))
    if not (master / "biota").is_dir():
        print(f"Master folder not found: {master}")
        return 1

    from db.master_db import MasterDB, MasterCheckpointWorker
    db = MasterDB(master)
    db.ensure_fresh()

    if a.pages_only:
        sf = _species_file(master)
        scis = sf.read_text(encoding="utf-8").split("\n") if sf.exists() else []
        scis = [s for s in scis if s.strip()]
        print(f"Rebuilding {len(scis)} species pages ...")
        failed = rebuild_pages(db, scis)
        print(f"done, {len(failed)} failed  ({time.time() - t0:.0f} s)")
        return 1 if failed else 0

    print(f"Master: {master}\nLooking for identical RAWs in one folder ...", flush=True)
    groups = identical_groups(master / "biota")
    items = plan(db, groups, allow_fewer_stars=a.allow_fewer_stars)
    report = write_report(items, master)
    go = [i for i in items if not i["hold"]]
    held = [i for i in items if i["hold"]]
    print(f"\n{len(groups)} groups of identical RAWs; {len(items)} copies")
    print(f"  to delete: {len(go):>4}  ({sum(i['size'] for i in go) / 1e9:.2f} GB of RAW, "
          f"{sum(len(i['drop']['files']) for i in go)} files with their sidecars and JPGs)")
    print(f"  on hold:   {len(held):>4}  (see the report, column 'hold because')")
    print(f"  species pages to rebuild: {len({i['keep']['species'] for i in go})}")
    print(f"  the same RAW name under two different species (left alone): "
          f"{two_species_pairs(master / 'biota')}")
    print(f"Report: {report}")

    if not a.apply:
        print("\nDry run: nothing changed. Close SpeciesViewer, then run again with --apply.")
        return 0

    print(f"\nDeleting {len(go)} copies to the master trash ...", flush=True)
    scis, problems, freed = apply(db, master, items)
    MasterCheckpointWorker.get(master).flush()          # the CSVs, now
    sf = _species_file(master)
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text("\n".join(scis), encoding="utf-8")
    print(f"Deleted {len(go) - len(problems)}, {freed / 1e9:.2f} GB moved to the master trash "
          f"(Empty Master Trash makes it final).")
    for p in problems:
        print(f"  PROBLEM {p}")
    if a.pages:
        print(f"\nRebuilding {len(scis)} species pages ...", flush=True)
        failed = rebuild_pages(db, scis)
        print(f"{len(failed)} pages failed")
    else:
        print(f"Species pages to rebuild: {len(scis)} — run with --pages-only.")
    print(f"({time.time() - t0:.0f} s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
