# trip_delete_scan.py: GROUND-TRUTH dry-run for trip-local delete (no deletes).
#
# Rethink of trip_delete.py: instead of trusting the trip's own data_files.csv
# (which can be as stale/ghosted as the master used to be), this walks the ACTUAL
# files on disk in the trip's picture directory and decides each one against the
# now-reliable master inventory.
#
# Per the design decisions (2026-06-20):
#   - Identity: filename -> UniquePictureName (deriveUniquePictureName). No
#     exiftool - the UPN is purely filename-derived, so a directory walk is
#     enough and instant.
#   - Authority: the MASTER. A file is deletable iff its UPN is in the master
#     (the master stores the full RAW+XMP+JPG of every committed picture, so it
#     IS the backup). We also report a stronger file-level signal: whether this
#     exact basename is in the master's data_files list.
#   - deletelist.csv: ADVISORY only. We cross-check it and report divergence,
#     but it never vetoes a delete (it's the stale freeze-time artifact we're
#     moving away from). A file that is in a deletelist but NOT in the master is
#     flagged loudly - that is exactly the unsafe delete this rethink prevents.
#   - No "keep unidentified" filter: if a file is backed up in the master it is
#     deletable regardless of whether its picture carries a species.
#
# This module ONLY reports. It never removes anything.

import os
import csv
import logging

logger = logging.getLogger("speciesviewer")

# File kinds we consider part of a picture (everything else - blog.ini, txt,
# Thumbs.db, … - is reported as "other" and never proposed for deletion).
PICTURE_EXTS = {
    ".orf", ".nef", ".raw", ".cr2", ".cr3", ".arw", ".rw2", ".dng", ".raf",
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff",
    ".xmp", ".dop",
}
# Sub-directories inside a day folder that hold derived/aux data, not originals.
SKIP_DIRS = {"facebook", "frozen", "metadata", ".preview_cache", ".trash",
             ".thumbnails", "thumbs"}


def _basename_key(name):
    """Case-insensitive basename key for matching disk files to master rows."""
    return os.path.basename(name).strip().lower()


def load_master_inventory(master_root):
    """Read the master straight from its active CSVs (decoupled from the SQLite
    cache for this dry-run). Returns (upns, filenames) where
      - upns:      set of every UniquePictureName in the master picture list
      - filenames: set of lowercased File:FileName basenames in the master file
                   list (the stronger file-level signal)
    Returns (None, None) on failure."""
    import engine.trip_registry as tr
    _real, _plac, upns, _species = tr.load_master_index(master_root)
    if upns is None:
        return None, None

    filenames = set()
    files_csv = os.path.join(master_root, "metadata", "active", "data_files.csv")
    try:
        with open(files_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                fn = (row.get("File:FileName") or "").strip()
                if fn:
                    filenames.add(fn.lower())
    except Exception as e:
        logger.warning(f"trip_delete_scan: could not read master data_files.csv: {e}")
        # UPN-level check still works without the file-level signal.
    return upns, filenames


def load_trip_deletelist(trip_db):
    """Collect every path mentioned in the trip's frozen deletelist.csv files
    (advisory cross-check). Handles BOTH formats:
      - legacy single-column quoted paths
      - newer 'source_path;target_path' two-column form
    Returns a set of lowercased basenames (we match on basename so the check is
    robust to path drift)."""
    basenames = set()
    review = os.path.join(trip_db, "metadata", "review")
    if not os.path.isdir(review):
        return basenames
    for root, _dirs, files in os.walk(review):
        if "deletelist.csv" not in files:
            continue
        fp = os.path.join(root, "deletelist.csv")
        try:
            with open(fp, "r", encoding="utf-8", errors="replace", newline="") as fh:
                sample = fh.read(200)
                fh.seek(0)
                has_header = "source_path" in sample
                reader = csv.reader(fh, delimiter=";")
                for i, row in enumerate(reader):
                    if not row:
                        continue
                    if has_header and i == 0:
                        continue  # header line
                    src = row[0].strip().strip('"')
                    if src:
                        basenames.add(_basename_key(src))
        except Exception as e:
            logger.warning(f"trip_delete_scan: could not read {fp}: {e}")
    return basenames


def _trip_species_by_upn(trip_db):
    """UPN (lowercased) -> species from the trip's per-day data_pictures.csv.
    Lets the keep set be split into identified vs unidentified. Empty if the
    trip metadata isn't available."""
    out = {}
    review = os.path.join(trip_db, "metadata", "review") if trip_db else ""
    if not review or not os.path.isdir(review):
        return out
    for day in sorted(os.listdir(review)):
        fp = os.path.join(review, day, "data_pictures.csv")
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh, delimiter=";"):
                    u = (row.get("UniquePictureName") or "").strip().lower()
                    if u and u not in out:
                        out[u] = (row.get("WetSoortNaam") or "").strip()
        except Exception as e:
            logger.warning(f"trip_delete_scan: failed reading {fp}: {e}")
    return out


def _iter_day_dirs(picture_root):
    """Yield (day_name, day_path) for each immediate sub-directory of the trip
    picture root. Falls back to treating picture_root itself as a single day if
    it directly contains picture files."""
    try:
        entries = sorted(os.listdir(picture_root))
    except OSError as e:
        logger.warning(f"trip_delete_scan: cannot list {picture_root}: {e}")
        return
    subdirs = [(e, os.path.join(picture_root, e)) for e in entries
               if os.path.isdir(os.path.join(picture_root, e))
               and e.lower() not in SKIP_DIRS and not e.startswith(".")]
    if subdirs:
        yield from subdirs
    else:
        yield os.path.basename(picture_root.rstrip("/\\")) or picture_root, picture_root


def scan_trip(picture_root, master_root, trip_db):
    """Ground-truth dry-run. Walks the on-disk trip picture directory and
    classifies every real file against the master. NEVER deletes.

    Returns a report dict:
      {ok, picture_root, master_root, days:[...], totals:{...}, warnings:[...]}
    or {ok: False, error}.

    Per-day entry: {day, deletable:[...], keep:[...], other:[...], counts:{...}}.
    Each deletable/keep item: {path, upn, file_in_master, in_deletelist, reason}."""
    if not os.path.isdir(picture_root):
        return {"ok": False, "error": f"Trip picture directory not found: {picture_root}"}

    upns, filenames = load_master_inventory(master_root)
    if upns is None:
        return {"ok": False, "error": "Could not read the master inventory."}
    deletelist = load_trip_deletelist(trip_db)
    have_file_index = bool(filenames)

    from engine.filecsv import deriveUniquePictureName
    days = []
    warnings = []
    tot = {"files": 0, "deletable_files": 0, "keep_files": 0, "held_files": 0,
           "other_files": 0,
           "deletable_upns": set(), "keep_upns": set(), "held_upns": set(),
           "unsafe_in_deletelist_not_master": 0,
           "deletable_not_in_deletelist": 0}

    for day, day_path in _iter_day_dirs(picture_root):
        d_deletable, d_keep, d_held, d_other = [], [], [], []
        for root, dirs, files in os.walk(day_path):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                path = os.path.join(root, fn)
                ext = os.path.splitext(fn)[1].lower()
                if ext not in PICTURE_EXTS:
                    d_other.append(path)
                    continue
                tot["files"] += 1
                upn = (deriveUniquePictureName(fn) or "").strip()
                upn_in_master = upn in upns
                file_in_master = (_basename_key(fn) in filenames) if have_file_index else None
                in_deletelist = _basename_key(fn) in deletelist
                item = {"path": path, "upn": upn, "file_in_master": file_in_master,
                        "in_deletelist": in_deletelist}

                # Deletion gate: a byte-identical copy must exist in the master.
                # That means UPN in master AND (this exact file is in the master
                # file list, or we have no file index to check against).
                file_backed = file_in_master is True or (file_in_master is None and upn_in_master)
                if upn_in_master and file_backed:
                    item["reason"] = "in master (file confirmed)" if have_file_index else "in master (UPN)"
                    if not in_deletelist:
                        tot["deletable_not_in_deletelist"] += 1
                    d_deletable.append(item)
                    tot["deletable_files"] += 1
                    tot["deletable_upns"].add(upn)
                elif upn_in_master:
                    # UPN backed but this exact rendition isn't in the master -
                    # held back so we never delete a file without an identical twin.
                    item["reason"] = "UPN in master, exact file not - held (kept)"
                    d_held.append(item)
                    tot["held_files"] += 1
                    tot["held_upns"].add(upn)
                else:
                    item["reason"] = "NOT in master - keep"
                    if in_deletelist:
                        item["reason"] = "in deletelist but NOT in master - UNSAFE, kept"
                        tot["unsafe_in_deletelist_not_master"] += 1
                        warnings.append(f"{path}: listed for delete but its UPN is "
                                        f"absent from the master - kept.")
                    d_keep.append(item)
                    tot["keep_files"] += 1
                    tot["keep_upns"].add(upn)
        tot["other_files"] += len(d_other)
        days.append({"day": day, "deletable": d_deletable, "keep": d_keep,
                     "held": d_held, "other": d_other,
                     "counts": {"deletable": len(d_deletable), "keep": len(d_keep),
                                "held": len(d_held), "other": len(d_other)}})

    # Split the kept (not-in-master) pictures into identified vs unidentified —
    # the identified ones are the "would be lost if the whole trip is deleted"
    # set (rescue via Add-to-Master before fully retiring the trip).
    species_by_upn = _trip_species_by_upn(trip_db)
    keep_with_species = sum(1 for u in tot["keep_upns"] if species_by_upn.get(u))
    totals = {
        "files_scanned": tot["files"],
        "deletable_files": tot["deletable_files"],
        "deletable_pictures": len(tot["deletable_upns"]),
        "keep_files": tot["keep_files"],
        "keep_pictures": len(tot["keep_upns"]),
        "keep_with_species": keep_with_species,
        "keep_no_species": len(tot["keep_upns"]) - keep_with_species,
        "held_files": tot["held_files"],
        "held_pictures": len(tot["held_upns"]),
        "other_files": tot["other_files"],
        "unsafe_in_deletelist_not_master": tot["unsafe_in_deletelist_not_master"],
        "deletable_not_in_deletelist": tot["deletable_not_in_deletelist"],
        "have_master_file_index": have_file_index,
    }
    return {"ok": True, "picture_root": picture_root, "master_root": master_root,
            "days": days, "totals": totals, "warnings": warnings}


def format_report(report):
    """Human-readable summary of a scan_trip() report."""
    if not report.get("ok"):
        return f"Scan failed: {report.get('error', 'unknown error')}"
    t = report["totals"]
    lines = []
    lines.append(f"Trip delete dry-run (no files removed)")
    lines.append(f"  picture root : {report['picture_root']}")
    lines.append(f"  master       : {report['master_root']}")
    lines.append("")
    for d in report["days"]:
        c = d["counts"]
        lines.append(f"  {d['day']:<14}  delete {c['deletable']:>5}   keep {c['keep']:>5}"
                     f"   held {c['held']:>4}   other {c['other']:>4}")
    lines.append("")
    lines.append(f"  TOTAL  deletable : {t['deletable_files']} files "
                 f"({t['deletable_pictures']} pictures) - confirmed in master")
    lines.append(f"         keep      : {t['keep_files']} files "
                 f"({t['keep_pictures']} pictures, not in master "
                 f"- {t.get('keep_with_species', 0)} with a species, "
                 f"{t.get('keep_no_species', 0)} unidentified)")
    lines.append(f"         held      : {t['held_files']} files "
                 f"({t['held_pictures']} pictures) - UPN in master but this exact "
                 f"file is not (likely a local render); kept")
    lines.append(f"         other     : {t['other_files']} non-picture files (ignored)")
    if not t["have_master_file_index"]:
        lines.append("         (master file-level index unavailable - UPN-level check only)")
    if t["deletable_not_in_deletelist"]:
        lines.append(f"         note: {t['deletable_not_in_deletelist']} deletable files "
                     f"are not in any freeze deletelist (advisory)")
    if t["unsafe_in_deletelist_not_master"]:
        lines.append(f"  WARNING: {t['unsafe_in_deletelist_not_master']} files are listed "
                     f"in a deletelist but are NOT in the master - kept (would have been "
                     f"an unsafe delete).")
    return "\n".join(lines)


def execute_scan_delete(report):
    """Delete the files classified 'deletable' by scan_trip (each has a byte-
    identical copy confirmed in the master, which is the backup). Removes day
    folders that become empty. 'keep' and 'held' files are never touched.
    Returns (deleted_files, deleted_pictures, errors)."""
    if not report.get("ok"):
        return 0, 0, ["report not ok"]
    deleted_files = 0
    deleted_upns = set()
    errors = []
    touched_dirs = set()
    for d in report.get("days", []):
        for it in d.get("deletable", []):
            p = it["path"]
            try:
                if os.path.exists(p):
                    os.remove(p)
                    deleted_files += 1
                    deleted_upns.add(it.get("upn", ""))
                    touched_dirs.add(os.path.dirname(p))
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")
    # Drop now-empty directories (deepest first); folders that still hold kept/
    # held pictures, a blog.ini, or a Facebook/ subdir are left untouched.
    for dpath in sorted(touched_dirs, key=len, reverse=True):
        try:
            if os.path.isdir(dpath) and not os.listdir(dpath):
                os.rmdir(dpath)
        except Exception:
            pass
    return deleted_files, len(deleted_upns), errors


def write_detail_csv(report, path):
    """Write one row per scanned picture file (deletable + held + keep) for
    review. Columns: day;verdict;reason;upn;file_in_master;in_deletelist;path.
    Returns the path written, or None on failure / empty report."""
    if not report.get("ok"):
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["day", "verdict", "reason", "upn",
                        "file_in_master", "in_deletelist", "path"])
            for d in report["days"]:
                for verdict, items in (("delete", d["deletable"]),
                                       ("held", d["held"]), ("keep", d["keep"])):
                    for it in items:
                        w.writerow([d["day"], verdict, it["reason"], it["upn"],
                                    it["file_in_master"], it["in_deletelist"], it["path"]])
        return path
    except Exception as e:
        logger.warning(f"trip_delete_scan: could not write detail CSV {path}: {e}")
        return None


# ── standalone harness: run the dry-run against the configured trip ───────────
if __name__ == "__main__":
    import sys
    import argparse
    import configparser

    scriptpath = os.path.dirname(__file__)
    reporoot = os.path.abspath(os.path.join(scriptpath, os.pardir, os.pardir))
    sys.path.append(os.path.join(reporoot, "speciesviewer"))

    parser = argparse.ArgumentParser(description="trip delete dry-run (no deletes)")
    parser.add_argument("--config", default=os.environ.get("SVCONFIG", "svconfig.ini"))
    parser.add_argument("--picture-root", default=None,
                        help="trip picture root (default: parent of [project]picturedirectory)")
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    def _clean(s):
        return (s or "").replace('"', "").replace("'", "").strip()

    master_root = _clean(config.get("master", "masterbiotadatabase", fallback="")) \
        or _clean(config.get("repository", "masterbiotadatabase", fallback=""))
    trip_db = _clean(config.get("trip", "tripbiotadatabase", fallback="")) \
        or _clean(config.get("repository", "biotadatabase", fallback=""))
    picture_root = args.picture_root
    if not picture_root:
        pd = _clean(config.get("project", "picturedirectory", fallback=""))
        picture_root = os.path.dirname(pd.rstrip("/\\")) if pd else ""

    print(f"master  : {master_root}")
    print(f"trip db : {trip_db}")
    print(f"pictures: {picture_root}")
    print()
    rep = scan_trip(picture_root, master_root, trip_db)
    print(format_report(rep))
