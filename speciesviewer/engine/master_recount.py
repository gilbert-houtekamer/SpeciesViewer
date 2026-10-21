# master_recount.py: recount the master's species.csv counters from its pictures.
#
# ONE JOB: make species.csv say what data_pictures.csv holds. Picture counters
# are ADDED UP at each commit and never recounted, so deletes, trims and moves
# left them too high (2026-10-17: 116 species, ~1,800 pictures — Maghreb Magpie
# 569 stored, 94 real). The website uses them for its counts and to decide
# whether a species gets a page. This is the apply step that
# species_counter_audit (report only) was written to be followed by.
#
#   species   pictures, pictures with a JPG (counted as the audit counts them),
#             species count 1 when it has pictures
#   best pick redone when it points at a picture that no longer exists, or is
#             empty while pictures with a JPG exist (a pick that exists stays)
#   groups    counts summed from their children (by Up); a group's best pick
#             redone only when its picture no longer exists
#   added     a species its pictures name but species.csv lacks (a picture
#             re-filed by hand into a species new to the master — Refresh does
#             the pictures, this the species row), with any missing group above
#             it, from the full species list; the parent's Leaves get it too
#   JPGmap    a JPG whose picture now belongs to ANOTHER species is dropped;
#             an added species gets its pictures' JPGs (both 2026-10-21)
#   untouched the "this run" counters (they belong to the pipeline), names, paths
#
# plan() changes nothing; apply() backs species.csv up first and writes it with
# the Dropbox-safe replace.

import ast
import csv
import os
import time
import logging

from engine.species_counter_audit import _actual_from_pictures
from engine.subspecies import fold_species_flags, read_subspecies
from utils.atomic_replace import replace_with_retry

logger = logging.getLogger("speciesviewer")

COUNTS = ("iSpeciesCollectionCount", "iPictureCollectionCount", "iPicturesWithJPGsCollection")
PICK = ("currentHighestRating", "BestJPG", "BestUPN", "BestJPGPathName")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _paths(master_root):
    active = os.path.join(master_root, "metadata", "active")
    return active, os.path.join(active, "species.csv"), os.path.join(active, "data_pictures.csv")


def _best_pictures(pictures_csv):
    """{species: (rate, BestJPG, UPN, BestJPGPathName)} — its best picture WITH a JPG."""
    best = {}
    with open(pictures_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            sp = (r.get("WetSoortNaam") or "").strip().lower()
            if not sp or not (r.get("BestJPG") or "").strip():
                continue
            cand = (_int(r.get("Rate")), r["BestJPG"], (r.get("UniquePictureName") or "").strip(),
                    r.get("BestJPGPathName") or "")
            if sp not in best or cand[0] > best[sp][0]:
                best[sp] = cand
    return best


def _pictures_by_upn(pictures_csv):
    """{UPN: (species lower, [JPG file names])} — the JPGs a species page may show."""
    out = {}
    with open(pictures_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            upn = (r.get("UniquePictureName") or "").strip()
            if not upn:
                continue
            try:
                files = ast.literal_eval(r.get("Files") or "[]")
            except (ValueError, SyntaxError):
                files = []
            jpgs = [f for f in files if f.lower().endswith(".jpg")
                    and not f.lower().endswith("-small.jpg")]
            out[upn.lower()] = ((r.get("WetSoortNaam") or "").strip().lower(), jpgs)
    return out


def _as_list(v):
    try:
        got = ast.literal_eval(v or "[]")
        return list(got) if isinstance(got, (list, tuple)) else []
    except (ValueError, SyntaxError):
        return []


def _add_missing_species(active, header, rows, by_name, actual):
    """Rows for species that pictures name but species.csv lacks — and any group
    above one that is missing too — from speciescollection.csv. Returns the names
    added and the ones that could not be (not in the full list either)."""
    lower = {n.lower(): n for n in by_name}
    wanted = [s for s, a in actual.items() if a.get("pics") and s not in lower]
    if not wanted:
        return [], []
    coll = {}
    with open(os.path.join(active, "speciescollection.csv"), "r",
              encoding="utf-8-sig", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            coll[(r.get("ScientificName") or "").strip().lower()] = r
    added, missing = [], []
    for name in wanted:
        chain, cur = [], name
        while cur and cur not in lower and cur in coll:
            chain.append(coll[cur])
            cur = (coll[cur].get("Up") or "").strip().lower()
        if not chain or (cur and cur not in lower):
            missing.append(name)          # not in the full list, or no way up to the tree
            continue
        for src in reversed(chain):       # top group first, so each parent exists
            row = {h: "" for h in header}
            for h in ("Level", "ScientificName", "DutchName", "EnglishName", "Up"):
                row[h] = (src.get(h) or "").strip()
            row["Leaves"], row["JPGmap"] = "[]", "[]"
            for h in header:
                if h.startswith("i") or h == "currentHighestRating":
                    row[h] = "0"
            parent = by_name[lower[row["Up"].lower()]]
            row["TargetPath"] = (parent.get("TargetPath") or "").rstrip("/") \
                + "/" + row["ScientificName"] + "/" if parent.get("TargetPath") else ""
            leaves = _as_list(parent.get("Leaves"))
            if row["ScientificName"] not in leaves:
                leaves.append(row["ScientificName"])
                parent["Leaves"] = str(leaves)
            rows.append(row)
            by_name[row["ScientificName"]] = row
            lower[row["ScientificName"].lower()] = row["ScientificName"]
            added.append(row["ScientificName"])
    return added, missing


def _recount(master_root):
    """(header, rows, changes) — rows recounted in memory, changes listed."""
    active, species_csv, pictures_csv = _paths(master_root)
    actual, upns = _actual_from_pictures(active)
    best = _best_pictures(pictures_csv)
    with open(species_csv, "r", encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh, delimiter=";")
        header, rows = rd.fieldnames, list(rd)
    before = {r["ScientificName"]: {c: r.get(c, "") for c in COUNTS + PICK + ("JPGmap", "Leaves")}
              for r in rows}
    by_name = {r["ScientificName"]: r for r in rows}
    added, missing = _add_missing_species(active, header, rows, by_name, actual)
    if missing:
        logger.warning(f"Recount: pictures name {len(missing)} species that are in no "
                       f"species list, so they get no counters: {', '.join(sorted(missing))}")

    # JPGmap: a JPG whose picture is now another species' MOVES to that species
    # (the intermediate egret split left two in Ardea intermedia, 2026-10-21);
    # an added species gets its pictures' JPGs. Nothing else in it is touched.
    pics = _pictures_by_upn(pictures_csv)
    from engine.handling import deriveUniquePictureName
    species_rows = {r["ScientificName"].lower(): r for r in rows if r["Level"] == "Level.SPECIES"}
    moving = {}                                   # species (lower) -> [JPG]
    for name, r in species_rows.items():
        jm = _as_list(r.get("JPGmap"))
        if r["ScientificName"] in added:
            kept = sorted({f for sp, jpgs in pics.values() if sp == name for f in jpgs})
        else:
            kept = []
            for f in jm:
                owner = pics.get(deriveUniquePictureName(f), (name, None))[0]
                if owner in (name, "") or owner not in species_rows:
                    kept.append(f)
                else:
                    moving.setdefault(owner, []).append(f)
        if kept != jm:
            r["JPGmap"] = str(kept)
    for owner, files in moving.items():
        r = species_rows[owner]
        jm = _as_list(r.get("JPGmap"))
        new = jm + [f for f in files if f not in jm]
        if new != jm:
            r["JPGmap"] = str(new)
    subspecies = read_subspecies(os.path.join(active, "speciescollection.csv"))
    kids = {}
    for r in rows:
        kids.setdefault(r["Up"], []).append(r["ScientificName"])

    def gone(r):
        return bool((r.get("BestUPN") or "").strip()) and r["BestUPN"].strip() not in upns

    def empty(r):
        return not (r.get("BestJPG") or "").strip()

    def set_pick(r, rate, jpg, upn, path):
        r["currentHighestRating"], r["BestJPG"], r["BestUPN"], r["BestJPGPathName"] = \
            str(rate), jpg, upn, path

    done = set()

    def walk(name):
        if name in done or name not in by_name:
            return
        done.add(name)
        r = by_name[name]
        children = [k for k in kids.get(name, []) if k in by_name and k != name]
        if r["Level"] == "Level.SPECIES" and not children:
            a = actual.get(name.lower(), {"pics": 0, "jpgs": 0})
            r["iPictureCollectionCount"] = str(a["pics"])
            r["iPicturesWithJPGsCollection"] = str(a["jpgs"])
            r["iSpeciesCollectionCount"] = "1" if a["pics"] else "0"
            # no best picture, or one whose picture is gone: the best picture
            # with a JPG (Nemosia pileata: 3 with a JPG, none picked — the
            # website left the species off the All Species page). A picture
            # re-filed to another species is gone from THIS one too.
            owner = pics.get((r.get("BestUPN") or "").strip().lower(), (None,))[0]
            elsewhere = owner not in (None, "", name.lower())
            if gone(r) or elsewhere or (empty(r) and best.get(name.lower())):
                b = best.get(name.lower())
                set_pick(r, *(b if b else (0, "", "", "")))
            return
        for k in children:
            walk(k)
        for c in COUNTS:
            r[c] = str(sum(_int(by_name[k][c]) for k in children))
        # a species and its subspecies count once (engine/subspecies.py)
        r["iSpeciesCollectionCount"] = str(fold_species_flags(
            subspecies, [(k, by_name[k]["iSpeciesCollectionCount"], 0) for k in children])[0])
        if gone(r) or empty(r):
            top = max((by_name[k] for k in children if (by_name[k].get("BestUPN") or "").strip()
                       and not gone(by_name[k]) and not empty(by_name[k])),
                      key=lambda k: _int(k["currentHighestRating"]), default=None)
            if top is not None:
                set_pick(r, top["currentHighestRating"], top["BestJPG"], top["BestUPN"],
                         top["BestJPGPathName"])
            elif gone(r):
                set_pick(r, 0, "", "", "")

    for r in rows:
        if not r["Up"] or r["Up"] not in by_name:      # roots: biota, topics
            walk(r["ScientificName"])
    for r in rows:                                     # anything not under a root
        walk(r["ScientificName"])

    changes = []
    for r in rows:
        if r["ScientificName"] not in before:
            changes.append((r["Level"], r["ScientificName"], "added", "", "new row"))
            continue
        old = before[r["ScientificName"]]
        for c in COUNTS + PICK + ("JPGmap", "Leaves"):
            if (old.get(c) or "") != (r.get(c) or ""):
                changes.append((r["Level"], r["ScientificName"], c, old.get(c) or "", r.get(c) or ""))
    return header, rows, changes


def plan(master_root):
    """What a recount would change — nothing is written."""
    _h, rows, changes = _recount(master_root)
    species = {n for lvl, n, c, o, v in changes if lvl == "Level.SPECIES"}
    picks = {n for lvl, n, c, o, v in changes if c == "BestUPN"}
    biota = next((r for r in rows if r["Level"] == "Level.BIOTA"), None)
    return {"changes": changes, "species": len(species), "picks": len(picks),
            "biota_pictures": biota["iPictureCollectionCount"] if biota else ""}


def apply(master_root):
    """Recount and write species.csv (backup first). Returns plan()'s summary."""
    header, rows, changes = _recount(master_root)
    active, species_csv, _p = _paths(master_root)
    if not changes:
        return {"changes": [], "species": 0, "picks": 0, "backup": ""}
    backup_dir = os.path.join(master_root, "admin", "status", "backup")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, time.strftime("%Y%m%d-%H%M%S") + "_species.csv")
    with open(species_csv, "rb") as src, open(backup, "wb") as dst:
        dst.write(src.read())
    tmp = species_csv + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    replace_with_retry(tmp, species_csv)
    species = {n for lvl, n, c, o, v in changes if lvl == "Level.SPECIES"}
    picks = {n for lvl, n, c, o, v in changes if c == "BestUPN"}
    logger.info(f"Recounted the master's species table: {len(changes)} values in {len(species)} species changed, "
                f"{len(picks)} best picks redone; backup {backup}.")
    return {"changes": changes, "species": len(species), "picks": len(picks), "backup": backup}
