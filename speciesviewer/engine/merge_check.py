"""Is there anything of this trip that the master does not have yet?

ONE JOB: count the files of the trip's published pictures WITH a species (what
Merge Pictures into Master brings; untagged ones stay out of the master) and
how many of them the master already holds, by file name. Read-only.

Why: merging a trip a second time (also after Reset Trip + Publish, which makes
the days look new) ran three steps and logged 46 duplicate warnings before the
commit step asked (0.5.0 release test, 2026-10-22). Asking this first lets the
dialog say "nothing to add" before anything runs.
"""
import ast
import csv
import logging
from pathlib import Path

logger = logging.getLogger("speciesviewer")


def trip_species_files(trip_db) -> set[str]:
    """File names of every published picture with a species, all days."""
    names: set[str] = set()
    for csv_path in Path(trip_db, "metadata", "review").glob("*/frozen/frozen_data_pictures.csv"):
        try:
            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f, delimiter=";"):
                    if not (row.get("WetSoortNaam") or "").strip():
                        continue
                    try:
                        files = ast.literal_eval(row.get("Files") or "[]")
                    except (ValueError, SyntaxError):
                        continue
                    names.update(str(n) for n in files if n)
        except OSError as e:
            logger.debug(f"merge_check: cannot read {csv_path}: {e}")
    return names


def files_already_in_master(names, master_root) -> int:
    """How many of `names` the master's file list already has."""
    if not names:
        return 0
    from db.master_db import MasterDB
    db = MasterDB(master_root)
    db.ensure_fresh()
    con = db._connect()
    have = 0
    names = list(names)
    for i in range(0, len(names), 500):           # SQLite's variable limit
        chunk = names[i:i + 500]
        marks = ",".join("?" * len(chunk))
        have += con.execute(
            f'SELECT COUNT(*) FROM files WHERE "File:FileName" IN ({marks})',
            chunk).fetchone()[0]
    return have


def nothing_new(trip_db, master_root) -> tuple[bool, int]:
    """(True, n) when all n species files of the trip are already in the
    master; (False, n) otherwise — also when the trip has none (nothing to
    judge, let the steps say so)."""
    names = trip_species_files(trip_db)
    if not names:
        return False, 0
    return files_already_in_master(names, master_root) == len(names), len(names)
