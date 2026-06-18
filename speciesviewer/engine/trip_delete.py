# trip_delete.py: UPN-driven retirement delete.
#
# Replaces the freeze-time deletelist as the source of truth for removing a
# trip's local files once they're safely in the master. A file is deletable iff
# its UniquePictureName is present in the master (which stores the full RAW +
# XMP + JPG of every committed picture, so the master IS the backup). Pictures
# whose UPN is NOT in the master are KEPT — unidentified leftovers, not-yet-
# integrated days, and any unique new species. Name-independent and immune to
# the stale/empty deletelist problems.

import os
import csv
import logging

logger = logging.getLogger("speciesviewer")


def _master_upns(master_repo):
    """Set of every UniquePictureName in the master (or None on failure).
    Reuses the registry's single-pass master reader."""
    import engine.trip_registry as tr
    _real, _plac, upns, _species = tr.load_master_index(master_repo)
    return upns


def _identified_upns(review):
    """UPNs that carry a species name in the trip's data_pictures.csv. Only
    these are delete candidates — unidentified pictures are always kept on the
    trip for later identification (even though some may have leaked into the
    master root at freeze time)."""
    identified = set()
    for day in sorted(os.listdir(review)):
        fp = os.path.join(review, day, "data_pictures.csv")
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh, delimiter=";"):
                    upn = (row.get("UniquePictureName") or "").strip()
                    if upn and (row.get("WetSoortNaam") or "").strip():
                        identified.add(upn)
        except Exception as e:
            logger.warning(f"trip_delete: failed reading {fp}: {e}")
    return identified


def plan_trip_delete(trip_db, master_repo):
    """Plan the deletion: a trip file is deletable iff its picture is IDENTIFIED
    (has a species) AND its UPN is in the master. Unidentified pictures are kept
    for later identification; identified pictures not yet in the master are kept
    too. Returns {ok, deletable {upn:[paths]}, n_pictures, n_files,
    kept_unidentified, kept_not_in_master} or {ok: False, error}. Non-destructive."""
    upns = _master_upns(master_repo)
    if upns is None:
        return {"ok": False, "error": "Could not read the master picture list."}
    review = os.path.join(trip_db, "metadata", "review")
    if not os.path.isdir(review):
        return {"ok": False, "error": f"No metadata/review found in {trip_db}."}

    identified = _identified_upns(review)
    deletable = {}
    kept_unident = set()
    kept_not_master = set()
    for day in sorted(os.listdir(review)):
        fp = os.path.join(review, day, "data_files.csv")
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh, delimiter=";"):
                    upn = (row.get("UniquePictureName") or "").strip()
                    path = (row.get("WindowsFileName") or "").strip()
                    if not upn or not path:
                        continue
                    on_disk = os.path.exists(path)
                    if upn not in identified:
                        if on_disk:
                            kept_unident.add(upn)        # leftover — keep for later ID
                        continue
                    if upn in upns:
                        if on_disk:
                            deletable.setdefault(upn, []).append(path)
                    elif on_disk:
                        kept_not_master.add(upn)         # identified but not backed up — keep
        except Exception as e:
            logger.warning(f"trip_delete: failed reading {fp}: {e}")

    n_files = sum(len(v) for v in deletable.values())
    return {"ok": True, "deletable": deletable, "n_pictures": len(deletable),
            "n_files": n_files, "kept_unidentified": len(kept_unident),
            "kept_not_in_master": len(kept_not_master)}


def execute_trip_delete(plan):
    """Delete the planned files (the master is the backup). Removes day folders
    that become empty. Returns (deleted_files, deleted_pictures, errors)."""
    deleted_files = deleted_pics = 0
    errors = []
    touched_dirs = set()
    for _upn, paths in plan.get("deletable", {}).items():
        removed_one = False
        for p in paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
                    deleted_files += 1
                    removed_one = True
                    touched_dirs.add(os.path.dirname(p))
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")
        if removed_one:
            deleted_pics += 1

    # Drop now-empty day folders (deepest first); leaves folders that still
    # hold kept pictures or a blog.ini untouched.
    for d in sorted(touched_dirs, key=len, reverse=True):
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        except Exception:
            pass
    return deleted_files, deleted_pics, errors
