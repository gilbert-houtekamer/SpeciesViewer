# master_add.py: Phase A — list the active trip's STRANDED identified pictures.
#
# A "stranded" picture is one that was identified on the trip (has a species in
# the trip's data_pictures.csv) and still has files on disk, but whose UPN never
# made it into the master. These are exactly the pictures the delete scan KEEPS
# (correctly — they're not backed up) and that an "Add to Master" flow should
# offer to commit. Pictures WITHOUT a species are never candidates.
#
# This module only reads — it lists candidates, it does not write anything.

import os
import csv
import logging

import engine.trip_delete_scan as tds

logger = logging.getLogger("speciesviewer")


def _disk_upn_index(picture_root):
    """Walk the trip picture root and map UPN -> [absolute file paths] for every
    picture file actually present on disk (ground truth). Reuses the delete
    scan's extension / skip-dir rules and UPN derivation."""
    from engine.filecsv import deriveUniquePictureName
    index = {}
    for _day, day_path in tds._iter_day_dirs(picture_root):
        for root, dirs, files in os.walk(day_path):
            dirs[:] = [d for d in dirs
                       if d.lower() not in tds.SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                if os.path.splitext(fn)[1].lower() not in tds.PICTURE_EXTS:
                    continue
                upn = (deriveUniquePictureName(fn) or "").strip()
                if upn:
                    index.setdefault(upn, []).append(os.path.join(root, fn))
    return index


def list_candidates(picture_root, master_root, trip_db):
    """Return the trip's stranded identified pictures as add-to-master candidates.

    A candidate = has a species in the trip data_pictures.csv AND has files on
    disk AND its UPN is NOT in the master. Returns
      {ok, picture_root, master_root, candidates:[...], counts:{...}}
    or {ok: False, error}. Each candidate:
      {upn, day, species, eng, gps_present, location, best_jpg, target_path,
       description, n_files, files:[paths]}.
    Non-destructive."""
    if not os.path.isdir(picture_root):
        return {"ok": False, "error": f"Trip picture directory not found: {picture_root}"}
    upns, _filenames, _master_index = tds.load_master_inventory(master_root)
    if upns is None:
        return {"ok": False, "error": "Could not read the master inventory."}

    review = os.path.join(trip_db, "metadata", "review")
    if not os.path.isdir(review):
        return {"ok": False, "error": f"No metadata/review found in {trip_db}."}

    disk = _disk_upn_index(picture_root)
    candidates = []
    seen = set()
    n_identified = n_no_species = n_already_master = n_no_disk = 0

    for day in sorted(os.listdir(review)):
        fp = os.path.join(review, day, "data_pictures.csv")
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh, delimiter=";"):
                    upn = (row.get("UniquePictureName") or "").strip()
                    species = (row.get("WetSoortNaam") or "").strip()
                    if not upn or upn in seen:
                        continue
                    seen.add(upn)
                    if not species:
                        n_no_species += 1
                        continue
                    n_identified += 1
                    if upn in upns:
                        n_already_master += 1
                        continue
                    files = disk.get(upn, [])
                    if not files:
                        n_no_disk += 1          # DB row but files already gone
                        continue
                    candidates.append({
                        "upn": upn, "day": day, "species": species,
                        "eng": (row.get("EngSoortNaam") or "").strip(),
                        "gps_present": bool((row.get("GPSPosition") or "").strip()),
                        "location": (row.get("Location") or "").strip(),
                        "best_jpg": (row.get("BestJPG") or "").strip(),
                        "target_path": (row.get("TargetPath") or "").strip(),
                        "description": (row.get("Description") or "").strip(),
                        "n_files": len(files), "files": files,
                    })
        except Exception as e:
            logger.warning(f"master_add: failed reading {fp}: {e}")

    candidates.sort(key=lambda c: (c["day"], c["species"], c["upn"]))
    counts = {
        "candidates": len(candidates),
        "identified": n_identified,
        "no_species_skipped": n_no_species,
        "already_in_master": n_already_master,
        "no_disk_files": n_no_disk,
        "without_gps": sum(1 for c in candidates if not c["gps_present"]),
    }
    return {"ok": True, "picture_root": picture_root, "master_root": master_root,
            "candidates": candidates, "counts": counts}


def format_candidates(report):
    """Human-readable summary of list_candidates()."""
    if not report.get("ok"):
        return f"Candidate scan failed: {report.get('error', 'unknown error')}"
    c = report["counts"]
    lines = [
        "Add-to-Master candidates (stranded identified pictures, no writes)",
        f"  picture root : {report['picture_root']}",
        f"  master       : {report['master_root']}",
        "",
        f"  candidates        : {c['candidates']}  (have species, on disk, not in master)",
        f"  without GPS       : {c['without_gps']}  (need GPS/location before commit)",
        f"  already in master : {c['already_in_master']}",
        f"  no species (skip) : {c['no_species_skipped']}",
        f"  db row, no files  : {c['no_disk_files']}",
        "",
    ]
    by_species = {}
    for cand in report["candidates"]:
        by_species.setdefault(cand["species"], 0)
        by_species[cand["species"]] += 1
    for sp in sorted(by_species):
        gpsless = sum(1 for cn in report["candidates"]
                      if cn["species"] == sp and not cn["gps_present"])
        tag = f"  ({gpsless} without GPS)" if gpsless else ""
        lines.append(f"    {by_species[sp]:>4}  {sp}{tag}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import argparse
    import configparser

    scriptpath = os.path.dirname(__file__)
    reporoot = os.path.abspath(os.path.join(scriptpath, os.pardir, os.pardir))
    sys.path.append(os.path.join(reporoot, "speciesviewer"))

    parser = argparse.ArgumentParser(description="list add-to-master candidates (no writes)")
    parser.add_argument("--config", default=os.environ.get("SVCONFIG", "svconfig.ini"))
    parser.add_argument("--picture-root", default=None)
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    def _clean(s):
        return (s or "").replace('"', "").replace("'", "").strip()

    master_root = _clean(config.get("master", "masterbiotadatabase", fallback=""))
    trip_db = _clean(config.get("trip", "tripbiotadatabase", fallback="")) \
        or _clean(config.get("repository", "biotadatabase", fallback=""))
    picture_root = args.picture_root
    if not picture_root:
        pd = _clean(config.get("project", "picturedirectory", fallback=""))
        picture_root = os.path.dirname(pd.rstrip("/\\")) if pd else ""

    rep = list_candidates(picture_root, master_root, trip_db)
    print(format_candidates(rep))
