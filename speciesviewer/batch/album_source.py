"""Album publish data source — read a virtual trip's pictures LIVE from the master
by date range, the drop-in replacement for reading per-day review CSV snapshots.

An album is a window ON the master (pictures whose camera DateTimeOriginal falls in
its fromdate..todate), not a copied-out set of day folders. svv3WriteBlog and
svv4BuildWebsite used to iterate `<album>/metadata/review/<day>/data_*.csv`; they now
iterate day labels from this module and fill the project's in-memory dicts straight
from MasterDB — so there is exactly one copy of the truth and no extract step.

`load_day_from_master` mirrors engine.handling.read_file + read_pictures exactly:
it fills project.files_dict (keyed by File:FileName) and project.UniquePictureName_dict
(keyed by UPN, with Files as a list) and returns (from_time, last_time) = the min/max
EXIF:DateTimeOriginal across the day's file rows, which the blog's picture filter needs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("speciesviewer")


def open_master_db(master_root: str):
    """A MasterDB over the configured master root (ensure_fresh so a pipeline that
    just ran is reflected)."""
    from db.master_db import MasterDB
    db = MasterDB(master_root)
    db.ensure_fresh()
    return db


def day_labels_in_range(db, from_date: str, to_date: str) -> list[str]:
    """Sorted 'YYYY-MM-DD' labels in [from_date, to_date] that actually have master
    pictures — the day set that used to be the review/<day>/ folders."""
    return sorted(db.pictures_in_daterange(from_date, to_date).keys())


def day_pictures(db, day_label: str) -> list[dict]:
    """The day's master picture rows (Files already a list) — the query replacement
    for reading a day's data_pictures.csv (svv3's blog auto-text reads these)."""
    return db.pictures_in_daterange(day_label, day_label).get(day_label, [])


def load_day_from_master(project, db, day_label: str) -> tuple[str, str]:
    """Fill project.files_dict + UniquePictureName_dict for ONE day straight from the
    master — the drop-in for read_file(day/data_files.csv) + read_pictures(...). Returns
    (from_time, last_time) = min/max EXIF:DateTimeOriginal across the day's file rows.

    Paths in the master file rows are already absolute (MasterDB resolves the
    SmallMaster root-relative form on load), so no re-resolution is needed."""
    pics = day_pictures(db, day_label)
    files = db.files_for_upns([p["UniquePictureName"] for p in pics])
    from_time = last_time = ""
    for fname, row in files.items():
        if fname not in project.files_dict:
            project.files_dict[fname] = row
            dt = row.get("EXIF:DateTimeOriginal", "") or ""
            if dt:
                if from_time == "" or from_time > dt:
                    from_time = dt
                if last_time < dt:
                    last_time = dt
    for p in pics:
        upn = p["UniquePictureName"]
        if upn not in project.UniquePictureName_dict:
            project.UniquePictureName_dict[upn] = p   # Files already a list
    return from_time, last_time


def album_groups(db, from_date: str, to_date: str, sc_dict: dict) -> set[str]:
    """Every taxon (species and all the groups above it) with a JPG picture in
    the album's date range — the WHOLE album, for the site menu on blog pages,
    which are rendered one day at a time (ch.7, 2026-10-18)."""
    groups = set()
    for rows in db.pictures_in_daterange(from_date, to_date).values():
        for row in rows:
            if not row.get("BestJPG"):
                continue
            name = row.get("WetSoortNaam", "")
            while name and name in sc_dict and name not in groups:
                groups.add(name)
                name = sc_dict[name].get("Up", "")
    return groups
