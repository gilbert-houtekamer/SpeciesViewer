# species_counter_audit.py: QA dry-run - reconcile master species.csv counters
# against the actual pictures in data_pictures.csv.
#
# The Admin Heal cleaned ghost rows out of data_files/data_pictures, but
# species.csv (per-species counts + best-pick) was never recomputed, so some
# species still claim pictures that no longer exist. This module REPORTS the
# drift; it does not modify anything. Three classes:
#   - ghost        : claims pictures (count>0) but zero exist  -> the log spam
#                    "no identified JPGs to show" / "stale counters … BestJPG empty"
#   - count drift  : has pictures, but the claimed count or JPG-count is wrong
#   - orphan pick  : BestUPN points at a picture that no longer exists
#
# Reporting only (review over auto-fix). A later apply would recompute the
# counters + best-picks and re-roll the ORDER/FAMILY aggregates.

import os
import csv
import logging

logger = logging.getLogger("speciesviewer")

_JPG_EXTS = (".jpg", ".jpeg")


def _parse_files(raw):
    """The CSV stores Files as a python-repr list, e.g. ['foo.orf', 'foo.jpg']."""
    if not raw or raw == "[]":
        return []
    try:
        return [v.strip().strip("'").strip('"') for v in raw[1:-1].split(",") if v.strip()]
    except Exception:
        return []


def _actual_from_pictures(active_dir):
    """One pass over data_pictures.csv. Returns
       (per_species, upn_set) where per_species[name] = {'pics', 'jpgs'}."""
    per = {}
    upns = set()
    path = os.path.join(active_dir, "data_pictures.csv")
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            upn = (r.get("UniquePictureName") or "").strip()
            if upn:
                upns.add(upn)
            sp = (r.get("WetSoortNaam") or "").strip().lower()
            if not sp:
                continue
            rec = per.setdefault(sp, {"pics": 0, "jpgs": 0})
            rec["pics"] += 1
            files = _parse_files(r.get("Files", ""))
            if any(f.lower().endswith(_JPG_EXTS) for f in files):
                rec["jpgs"] += 1
    return per, upns


def audit(master_root):
    """Compare species.csv (Level.SPECIES rows) against the real pictures.
    Returns {ok, ghosts, drift, orphan_pick, totals} or {ok: False, error}.
    Non-destructive."""
    active = os.path.join(master_root, "metadata", "active")
    sp_csv = os.path.join(active, "species.csv")
    pic_csv = os.path.join(active, "data_pictures.csv")
    if not os.path.isfile(sp_csv) or not os.path.isfile(pic_csv):
        return {"ok": False, "error": f"master CSVs not found under {active}"}

    try:
        per_species, upn_set = _actual_from_pictures(active)
    except Exception as e:
        return {"ok": False, "error": f"failed reading data_pictures.csv: {e}"}

    ghosts, drift, orphan = [], [], []
    n_species = 0
    try:
        with open(sp_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                if r.get("Level") != "Level.SPECIES":
                    continue
                n_species += 1
                name = (r.get("ScientificName") or "").strip()
                key = name.lower()
                try:
                    claim_pic = int(r.get("iPictureCollectionCount") or 0)
                except ValueError:
                    claim_pic = 0
                try:
                    claim_jpg = int(r.get("iPicturesWithJPGsCollection") or 0)
                except ValueError:
                    claim_jpg = 0
                act = per_species.get(key, {"pics": 0, "jpgs": 0})
                best_upn = (r.get("BestUPN") or "").strip()

                if claim_pic > 0 and act["pics"] == 0:
                    ghosts.append({"name": name, "claim_pic": claim_pic,
                                   "claim_jpg": claim_jpg, "best_upn": best_upn})
                elif act["pics"] > 0 and (claim_pic != act["pics"] or claim_jpg != act["jpgs"]):
                    drift.append({"name": name, "claim_pic": claim_pic, "act_pic": act["pics"],
                                  "claim_jpg": claim_jpg, "act_jpg": act["jpgs"]})
                # Orphaned best-pick: points at a UPN that no longer exists
                # (only meaningful when the species still has pictures - a ghost
                # is already reported above).
                if best_upn and best_upn not in upn_set and act["pics"] > 0:
                    orphan.append({"name": name, "best_upn": best_upn, "act_pic": act["pics"]})
    except Exception as e:
        return {"ok": False, "error": f"failed reading species.csv: {e}"}

    return {"ok": True, "master_root": master_root,
            "ghosts": ghosts, "drift": drift, "orphan_pick": orphan,
            "totals": {"species_rows": n_species, "real_species": len(per_species),
                       "ghosts": len(ghosts), "drift": len(drift),
                       "orphan_pick": len(orphan)}}


def format_report(rep):
    if not rep.get("ok"):
        return f"Species-counter audit failed: {rep.get('error', 'unknown error')}"
    t = rep["totals"]
    lines = [
        "Species-counter audit (master species.csv vs actual pictures - no writes)",
        f"  master       : {rep['master_root']}",
        "",
        f"  Level.SPECIES rows        : {t['species_rows']}",
        f"  species with real pictures: {t['real_species']}",
        f"  GHOSTS (claim pics, 0 real): {t['ghosts']}",
        f"  count drift               : {t['drift']}",
        f"  orphaned best-pick        : {t['orphan_pick']}",
    ]
    if rep["ghosts"]:
        lines.append("")
        lines.append("  Ghosts (species claiming pictures that do not exist):")
        for g in sorted(rep["ghosts"], key=lambda x: -x["claim_pic"]):
            tag = f"  best_upn={g['best_upn']}" if g["best_upn"] else ""
            lines.append(f"    claims {g['claim_pic']:>3} pic / {g['claim_jpg']:>3} jpg   "
                         f"{g['name']}{tag}")
    if rep["drift"]:
        lines.append("")
        lines.append("  Count drift (claimed vs actual):")
        for d in sorted(rep["drift"], key=lambda x: x["name"]):
            lines.append(f"    pics {d['claim_pic']:>4}->{d['act_pic']:<4} "
                         f"jpg {d['claim_jpg']:>4}->{d['act_jpg']:<4}  {d['name']}")
    if rep["orphan_pick"]:
        lines.append("")
        lines.append("  Orphaned best-pick (BestUPN no longer exists, species still has pics):")
        for o in sorted(rep["orphan_pick"], key=lambda x: x["name"]):
            lines.append(f"    {o['name']}  ({o['act_pic']} pics)  best_upn={o['best_upn']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import argparse
    import configparser

    scriptpath = os.path.dirname(__file__)
    reporoot = os.path.abspath(os.path.join(scriptpath, os.pardir, os.pardir))
    sys.path.append(os.path.join(reporoot, "speciesviewer"))

    parser = argparse.ArgumentParser(description="species-counter audit (no writes)")
    parser.add_argument("--config", default=os.environ.get("SVCONFIG", "svconfig.ini"))
    parser.add_argument("--master", default=None)
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    def _clean(s):
        return (s or "").replace('"', "").replace("'", "").strip()

    master = args.master or _clean(config.get("master", "masterbiotadatabase", fallback=""))
    print(format_report(audit(master)))
