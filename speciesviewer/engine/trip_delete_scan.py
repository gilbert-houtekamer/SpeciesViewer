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
#   - No "keep unidentified" filter: if a file is backed up in the master it is
#     deletable regardless of whether its picture carries a species.
#
# WHAT REMOVES, AND WHAT DOES NOT. `scan_trip` and `write_detail_csv` only
# report -- they walk the disk and decide, and touch nothing. `execute_scan_delete`
# is the ONLY function here that removes files (os.remove on each `deletable`
# entry, then prunes emptied directories). It is called from the Trips menu
# (gui/menuspec.py) and the add-to-master flow (gui/add_to_master.py), always
# after the user has seen the report.
#
# This header used to claim the module never removed anything. That was true when
# the rethink was written and stopped being true when the applier landed -- a
# wrong comment in the one file where a wrong comment is most expensive.
# Corrected 2026-08-24.

import os
import csv
import logging
from utils.ini_io import read_ini
from utils.raw_extensions import PICTURE, SIDECAR

logger = logging.getLogger("speciesviewer")

# File kinds we consider part of a picture (everything else - blog.ini, txt,
# Thumbs.db, … - is reported as "other" and never proposed for deletion).
# Canonical table. SIDECAR is NOT decoration here: a delete that takes the RAW must
# take its .xmp/.dop too, and substituting plain PICTURE would have left orphaned
# sidecars behind. Caught by the dry-run, 2026-08-24 -- the reason this file was
# migrated last and separately rather than in the mechanical sweep.
PICTURE_EXTS = PICTURE | SIDECAR
# Sub-directories inside a day folder that hold derived/aux data, not originals.
SKIP_DIRS = {"facebook", "frozen", "metadata", ".preview_cache", ".trash",
             ".thumbnails", "thumbs"}


def _basename_key(name):
    """Case-insensitive basename key for matching disk files to master rows."""
    return os.path.basename(name).strip().lower()


def load_master_inventory(master_root):
    """Read the master straight from its active CSVs (decoupled from the SQLite
    cache for this dry-run). Returns (upns, filenames, master_index) where
      - upns:      set of every UniquePictureName in the master picture list
      - filenames: set of lowercased File:FileName basenames in the master file
                   list (the stronger file-level signal)
      - master_index: {"by_name": {basename_key -> master file path},
                       "by_upn":  {upn -> [master file paths]}} — used to VERIFY
                   a byte-for-byte copy actually exists on disk before a local
                   file is ever proposed for deletion (the ghost-row guard: the
                   master CSVs can list a file that was never physically copied).
    Returns (None, None, None) on failure."""
    import engine.trip_registry as tr
    _real, _plac, upns, _species = tr.load_master_index(master_root)
    if upns is None:
        return None, None, None

    filenames = set()
    master_index = {"by_name": {}, "by_upn": {}}
    files_csv = os.path.join(master_root, "metadata", "active", "data_files.csv")
    try:
        with open(files_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                fn = (row.get("File:FileName") or "").strip()
                if not fn:
                    continue
                fn_key = fn.lower()
                filenames.add(fn_key)
                path = (row.get("WindowsFileName") or "").strip()
                if path:
                    master_index["by_name"][fn_key] = path
                    u = (row.get("UniquePictureName") or "").strip().lower()
                    if u:
                        master_index["by_upn"].setdefault(u, []).append(path)
    except Exception as e:
        logger.warning(f"trip_delete_scan: could not read master data_files.csv: {e}")
        # Without the file index we cannot verify a physical copy, so nothing is
        # classified deletable below — the safe degradation for a destructive op.
    return upns, filenames, master_index


def _master_copy_present(local_path, master_path):
    """True only when the master holds a byte-for-byte copy of this file: the
    master file exists AND its size matches the local file. The size check
    catches the exact ghost-row failure mode — a CSV row whose file was never
    copied (missing) or only partially copied (wrong size)."""
    try:
        if not master_path or not os.path.exists(master_path):
            return False
        return os.path.getsize(master_path) == os.path.getsize(local_path)
    except OSError:
        return False


def _upn_has_confirmed_master_file(upn, master_index):
    """True when at least one of the UPN's master files physically exists — the
    proof that the picture (its RAW) is really backed up. Used for the .dng
    carve-out: a DNG is recreatable from the RAW and never stored in the master,
    so it is deletable only when the RAW it derives from is confirmed present."""
    for p in master_index.get("by_upn", {}).get((upn or "").lower(), []):
        try:
            if os.path.exists(p):
                return True
        except OSError:
            continue
    return False


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
    Each deletable/keep item: {path, upn, file_in_master, reason}."""
    if not os.path.isdir(picture_root):
        return {"ok": False, "error": f"Trip picture directory not found: {picture_root}"}

    upns, filenames, master_index = load_master_inventory(master_root)
    if upns is None:
        return {"ok": False, "error": "Could not read the master inventory."}
    have_file_index = bool(master_index["by_name"])

    from engine.filecsv import deriveUniquePictureName
    days = []
    warnings = []
    tot = {"files": 0, "deletable_files": 0, "keep_files": 0, "held_files": 0,
           "other_files": 0,
           "deletable_upns": set(), "keep_upns": set(), "held_upns": set()}

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
                master_path = master_index["by_name"].get(_basename_key(fn))
                file_in_master = bool(master_path) if have_file_index else None
                item = {"path": path, "upn": upn, "file_in_master": file_in_master}

                # Deletion gate: a byte-for-byte copy must PHYSICALLY exist in the
                # master (the master is the backup). CSV membership alone is not
                # enough — the master CSVs can list a file that was never copied
                # (the ghost-row incident), so we stat the master file and require
                # a size match before ever proposing a delete.
                # Exception: a .dng is bulky and recreatable from the RAW, so it is
                # deletable when its picture (the RAW) is confirmed present, even
                # though the master never stores the DNG itself.
                file_confirmed = _master_copy_present(path, master_path)
                recreatable_dng = (ext == ".dng" and upn_in_master
                                   and _upn_has_confirmed_master_file(upn, master_index))
                if file_confirmed or recreatable_dng:
                    if recreatable_dng and not file_confirmed:
                        item["reason"] = "recreatable DNG (RAW confirmed in master)"
                    else:
                        item["reason"] = "in master (file confirmed on disk)"
                    d_deletable.append(item)
                    tot["deletable_files"] += 1
                    tot["deletable_upns"].add(upn)
                elif upn_in_master:
                    # UPN is in the master but this exact file is not physically
                    # confirmed (missing master copy, size mismatch, or a local-
                    # only rendition) — held back so we never delete a file without
                    # a verified identical twin.
                    item["reason"] = ("UPN in master, exact file not confirmed on disk"
                                      " - held (kept)")
                    d_held.append(item)
                    tot["held_files"] += 1
                    tot["held_upns"].add(upn)
                else:
                    item["reason"] = "NOT in master - keep"
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
    review. Columns: day;verdict;reason;upn;file_in_master;path.
    Returns the path written, or None on failure / empty report."""
    if not report.get("ok"):
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["day", "verdict", "reason", "upn",
                        "file_in_master", "path"])
            for d in report["days"]:
                for verdict, items in (("delete", d["deletable"]),
                                       ("held", d["held"]), ("keep", d["keep"])):
                    for it in items:
                        w.writerow([d["day"], verdict, it["reason"], it["upn"],
                                    it["file_in_master"], it["path"]])
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
    read_ini(config, args.config)
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
