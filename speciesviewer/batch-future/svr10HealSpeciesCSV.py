#!/usr/bin/env python3

# Heal stale JPGmap entries in species.csv files for a trip.
#
# Background: when a UPN's species is reassigned (via the Species Info dialog
# or by editing XMP keywords in Lightroom), the OLD species's species.csv row
# can keep references to that UPN's JPGs in JPGmap, BestJPG, BestUPN, and may
# even have its TargetPath blanked by a downstream bug. Subsequent blog/website
# generation then tries to render the orphan picture under the wrong species
# directory and fails with FileNotFoundError.
#
# This utility, for each species.csv in a trip:
#   1. Loads the corresponding data_files.csv + data_pictures.csv to learn each
#      JPG's CURRENT UPN and the UPN's CURRENT WetSoortNaam.
#   2. For each Level.SPECIES row, drops JPGmap entries whose UPN's current
#      species != this row's ScientificName, and dedups the survivors.
#   3. Recomputes BestJPG / BestUPN / BestJPGPathName / TargetPath /
#      currentHighestRating from the survivors (highest Rate wins).
#   4. Writes the file back, leaving a .preheal backup of the original.
#
# Run with --dry-run first to see the impact without modifying anything.

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    sys.path.append(modpath)

logger = logging.getLogger("speciesviewer")


def parse_list_field(raw):
    """Parse a CSV cell like ['a', 'b'] into ['a', 'b']. Tolerates messy quoting."""
    if not raw or raw == "[]":
        return []
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    out = []
    for part in s.split(","):
        cleaned = part.strip().strip("'\"")
        if cleaned:
            out.append(cleaned)
    return out


def format_list_field(items):
    """Inverse of parse_list_field. Empty list serializes as []."""
    if not items:
        return "[]"
    return "[" + ", ".join(f"'{item}'" for item in items) + "]"


def load_csv_dict(path, key_column):
    """Read a UTF-8 ;-delimited CSV into {key: row_dict}."""
    rows = {}
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            key = row.get(key_column, "")
            if key:
                rows[key] = row
    return rows


def build_jpg_index(*data_dirs):
    """Build {jpg_basename: (upn, wetsoortnaam, picture_row)} from one or more
    metadata dirs. Later dirs override earlier ones for the same JPG name —
    so callers should pass active first, project last."""
    jpg_to_picture = {}
    for d in data_dirs:
        files_csv = Path(d) / "data_files.csv"
        pics_csv = Path(d) / "data_pictures.csv"
        if not files_csv.exists() or not pics_csv.exists():
            continue
        files = load_csv_dict(files_csv, "File:FileName")
        pictures = load_csv_dict(pics_csv, "UniquePictureName")
        for filename, file_row in files.items():
            upn = file_row.get("UniquePictureName", "")
            if not upn or upn not in pictures:
                continue
            pic = pictures[upn]
            jpg_to_picture[filename] = (upn, pic.get("WetSoortNaam", ""), pic)
    return jpg_to_picture


def heal_species_csv(species_csv_path, jpg_to_picture, dry_run):
    """Filter and rebuild the JPGmap / Best* fields for every Level.SPECIES row.

    Returns (rows_changed, jpgs_dropped, jpgs_kept)."""
    if not species_csv_path.exists():
        return 0, 0, 0

    with open(species_csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        fieldnames = reader.fieldnames
        rows = list(reader)

    rows_changed = 0
    jpgs_dropped = 0
    jpgs_kept = 0

    for row in rows:
        # Only species-level rows have authoritative JPGmap. Family/Order/etc.
        # entries are summarized at runtime by summarizeSpeciesCounters.
        if row.get("Level") != "Level.SPECIES":
            continue

        scientific_name = row.get("ScientificName", "")
        old_jpgmap = parse_list_field(row.get("JPGmap", ""))
        new_jpgmap = []
        for jpg in old_jpgmap:
            tup = jpg_to_picture.get(jpg)
            if tup is None:
                jpgs_dropped += 1
                continue
            _upn, current_species, _pic = tup
            if current_species == scientific_name:
                if jpg not in new_jpgmap:
                    new_jpgmap.append(jpg)
                    jpgs_kept += 1
                else:
                    jpgs_dropped += 1  # dup
            else:
                jpgs_dropped += 1

        if old_jpgmap == new_jpgmap:
            continue

        rows_changed += 1
        row["JPGmap"] = format_list_field(new_jpgmap)

        # Recompute Best* / TargetPath from the survivors. Highest Rate wins.
        best_rating = -1
        best_jpg = ""
        best_upn = ""
        best_target_path = ""
        for jpg in new_jpgmap:
            upn, _, pic = jpg_to_picture[jpg]
            try:
                r = int(pic.get("Rate", 0) or 0)
            except (ValueError, TypeError):
                r = 0
            if r > best_rating:
                best_rating = r
                best_jpg = jpg
                best_upn = upn
                best_target_path = pic.get("TargetPath", "")

        if best_jpg:
            row["BestJPG"] = best_jpg
            row["BestUPN"] = best_upn
            row["BestJPGPathName"] = best_target_path
            row["TargetPath"] = best_target_path
            row["currentHighestRating"] = str(max(best_rating, 0))
        else:
            # JPGmap empty after heal — clear Best* but leave TargetPath.
            # TargetPath without classification rebuild is risky to clear: it
            # would orphan the species's own page. summarizeSpeciesCounters
            # repopulates non-species TargetPaths; leave species TargetPath as
            # whatever was last good.
            row["BestJPG"] = ""
            row["BestUPN"] = ""
            row["BestJPGPathName"] = ""
            row["currentHighestRating"] = "0"

    if rows_changed and not dry_run:
        backup = species_csv_path.with_suffix(species_csv_path.suffix + ".preheal")
        if backup.exists():
            backup.unlink()
        species_csv_path.rename(backup)
        with open(species_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL, delimiter=";"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    return rows_changed, jpgs_dropped, jpgs_kept


def heal_trip(trip_dir, dry_run):
    metadata = Path(trip_dir) / "metadata"
    if not metadata.is_dir():
        print(f"ERROR: {metadata} is not a directory", file=sys.stderr)
        return 1

    active = metadata / "active"
    review = metadata / "review"

    # JPGmap entries in any project's species.csv can reference UPNs from
    # ANY project in the trip (the species.csv is cumulative across days).
    # Build one global index from active + every review/<date>/. UPNs are
    # unique within a trip, so order doesn't matter for correctness — but
    # if a UPN somehow appears in multiple data_pictures.csv files, we let
    # the latest (by sorted dir name) win, since that reflects the most
    # recent reassignment.
    project_dirs = []
    if active.is_dir():
        project_dirs.append(active)
    if review.is_dir():
        project_dirs.extend(sorted(d for d in review.iterdir() if d.is_dir()))
    global_index = build_jpg_index(*project_dirs)
    print(f"Global JPG index covers {len(global_index)} JPGs across {len(project_dirs)} project dir(s)")

    summary = []  # rows of (scope, rows_changed, dropped, kept)

    if (active / "species.csv").exists():
        rows, dropped, kept = heal_species_csv(active / "species.csv", global_index, dry_run)
        summary.append(("active", rows, dropped, kept))

    if review.is_dir():
        for project_dir in sorted(review.iterdir()):
            if not project_dir.is_dir():
                continue
            sp_csv = project_dir / "species.csv"
            if not sp_csv.exists():
                continue
            rows, dropped, kept = heal_species_csv(sp_csv, global_index, dry_run)
            summary.append((project_dir.name, rows, dropped, kept))

    print()
    print(f"{'Scope':<24}{'Rows changed':>15}{'Dropped':>10}{'Kept':>10}")
    print("-" * 59)
    total_rows = total_dropped = total_kept = 0
    for scope, rows, dropped, kept in summary:
        print(f"{scope:<24}{rows:>15}{dropped:>10}{kept:>10}")
        total_rows += rows
        total_dropped += dropped
        total_kept += kept
    print("-" * 59)
    print(f"{'TOTAL':<24}{total_rows:>15}{total_dropped:>10}{total_kept:>10}")
    if dry_run:
        print()
        print("DRY RUN — no files modified. Re-run without --dry-run to apply.")
    elif total_rows:
        print()
        print(f"Original files saved as <name>.csv.preheal next to each modified species.csv.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Heal stale JPGmap entries in species.csv files for a trip"
    )
    parser.add_argument(
        "trip_dir",
        help="Trip directory (containing metadata/active and metadata/review)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report changes without writing",
    )
    args = parser.parse_args()
    sys.exit(heal_trip(args.trip_dir, args.dry_run))
