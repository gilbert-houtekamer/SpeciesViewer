#!/usr/bin/env python3
"""
svv2ExtractDays — Virtual Trip: extract per-day CSVs from the master.

For each day in the virtual trip's fromdate–todate range, reads the master's
data_files.csv and data_pictures.csv, filters to that day's pictures, and
writes the results to:

  <virttrip>/metadata/review/<YYYY-MM-DD>/data_files.csv
  <virttrip>/metadata/review/<YYYY-MM-DD>/data_pictures.csv

Only days that actually have pictures in the master produce a directory.
"""
import os
import sys
import csv
import configparser
import logging
import argparse
from datetime import datetime, timedelta, date

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from utils.virttrip_config import read_virttrip_ini
from utils.runtime_files import svconfig_path
from engine.handling import resolve_path
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return fieldnames, rows


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(args, config):
    virttrip_root = config.get("repository", "BiotaDatabase")
    master_db     = config.get("master",     "MasterBiotaDatabase", fallback="")

    if not master_db or not os.path.isdir(master_db):
        logger.error(f"Master database not found: {master_db!r}")
        return

    trip_data = read_virttrip_ini(virttrip_root)
    if not trip_data:
        logger.error(f"virttrip.ini not found in {virttrip_root}")
        return

    fromdate_str = trip_data.get("fromdate", "")
    todate_str   = trip_data.get("todate",   "")
    if not fromdate_str or not todate_str:
        logger.error("virttrip.ini is missing fromdate or todate.")
        return

    try:
        from_date = datetime.strptime(fromdate_str, "%Y-%m-%d").date()
        to_date   = datetime.strptime(todate_str,   "%Y-%m-%d").date()
    except ValueError as e:
        logger.error(f"Invalid date in virttrip.ini: {e}")
        return

    # only_day (optional): re-extract just ONE day, for the overview's per-day
    # "Re-extract this day" action after adding a render. The whole date range is
    # still scanned (the master CSV read is the cost either way); only that day's
    # CSVs are rewritten, leaving the other days' extracts untouched.
    only_day = config.get("extractconfig", "only_day", fallback="").strip()
    if only_day:
        try:
            od = datetime.strptime(only_day, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"extractconfig only_day is not a valid date: {only_day!r}")
            return
        if not (from_date <= od <= to_date):
            logger.error(f"only_day {only_day} is outside the album range "
                         f"{fromdate_str}–{todate_str}.")
            return
        from_date = to_date = od
        logger.info(f"Single-day re-extract: {only_day}")

    master_files_path    = os.path.join(master_db, "metadata", "active", "data_files.csv")
    master_pictures_path = os.path.join(master_db, "metadata", "active", "data_pictures.csv")

    if not os.path.isfile(master_files_path):
        logger.error(f"Master data_files.csv not found: {master_files_path}")
        return
    if not os.path.isfile(master_pictures_path):
        logger.error(f"Master data_pictures.csv not found: {master_pictures_path}")
        return

    logger.info(f"Reading master data_files.csv from {master_files_path}")
    file_fields, all_file_rows = _read_csv(master_files_path)
    # SmallMaster stores root-relative paths; resolve them to absolute so the
    # virtual-trip day CSVs contain paths the image viewer can actually open.
    for row in all_file_rows:
        for col in ("File:Directory", "WindowsFileName"):
            if col in row:
                row[col] = resolve_path(row[col], master_db)

    logger.info(f"Reading master data_pictures.csv from {master_pictures_path}")
    pic_fields, all_pic_rows = _read_csv(master_pictures_path)

    # Index data_pictures by UniquePictureName for fast lookup
    pictures_by_name = {row["UniquePictureName"]: row for row in all_pic_rows
                        if "UniquePictureName" in row}

    # Build a UPN→XMP-rows index once, so we can add sidecar rows per day.
    # XMP files carry XMP:Rating which computeRating needs; they lack
    # EXIF:DateTimeOriginal so they would otherwise be excluded by the date filter.
    xmp_by_upn: dict = {}
    for r in all_file_rows:
        if r.get("WindowsExtension", "").lower() == "xmp":
            upn = r.get("UniquePictureName", "")
            if upn:
                xmp_by_upn.setdefault(upn, []).append(r)

    review_root = os.path.join(virttrip_root, "metadata", "review")
    days_written = 0
    dropped_files = 0          # index rows whose file is not on disk
    current = from_date
    while current <= to_date:
        day_label = current.strftime("%Y-%m-%d")   # directory name
        exif_day  = current.strftime("%Y:%m:%d")   # prefix in EXIF:DateTimeOriginal

        # Primary files: JPG/RAW that have a matching DateTimeOriginal.
        # GUARDRAIL: only rows whose file is ACTUALLY ON DISK. The master index
        # can carry ghosts — rows for pictures that were never filed under biota/
        # (no species, so no folder) and never copied in; their directory is the
        # bare master root. Copying those into the day CSVs gives the virtual trip
        # pictures it cannot show: inflated counts, blank thumbnails, and bubbles
        # on the map for photos that do not exist. Stat only the date range, not
        # the whole master.
        dated_files = []
        for r in all_file_rows:
            if not r.get("EXIF:DateTimeOriginal", "").startswith(exif_day):
                continue
            if not os.path.isfile(r.get("WindowsFileName", "")):
                dropped_files += 1
                continue
            dated_files.append(r)

        if dated_files:
            day_names = {r.get("UniquePictureName", "") for r in dated_files}
            day_names.discard("")

            # Also include XMP sidecars for those UPNs; they carry XMP:Rating
            # which computeRating reads when building the blog.
            day_xmps = []
            for upn in day_names:
                for xmp_row in xmp_by_upn.get(upn, []):
                    if os.path.isfile(xmp_row.get("WindowsFileName", "")):
                        day_xmps.append(xmp_row)
                    else:
                        dropped_files += 1
            day_files = dated_files + day_xmps

            day_pictures = [pictures_by_name[n] for n in day_names if n in pictures_by_name]

            day_dir = os.path.join(review_root, day_label)
            os.makedirs(day_dir, exist_ok=True)

            _write_csv(os.path.join(day_dir, "data_files.csv"),    file_fields, day_files)
            _write_csv(os.path.join(day_dir, "data_pictures.csv"), pic_fields,  day_pictures)

            logger.info(f"  {day_label}: {len(day_files):4d} files, "
                        f"{len(day_pictures):4d} pictures")
            days_written += 1
        current += timedelta(days=1)

    if dropped_files:
        logger.warning(f"Skipped {dropped_files} master index row(s) in this date "
                       f"range whose file is not on disk — they are not in the "
                       f"master's biota tree and cannot be shown.")
    logger.info(f"✅ ExtractDays complete: {days_written} day(s) written to {review_root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svv2ExtractDays.")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG") or str(svconfig_path()),
                        help="Extract days from the master database based on the virttrip.ini date range. ")
    args = parser.parse_args()
    config = configparser.ConfigParser()
    read_ini(config, args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"svv2ExtractDays failed: {e}")
    else:
        logger.info("svv2ExtractDays succeeded.")
