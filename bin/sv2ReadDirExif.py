#!/usr/bin/env python3

# Scan the files in a directory, and keep selected EXIF properties in a CSV file.
#
# The files are specified in "svReadDir.ini" file, 
# currently stored in INI_LOCATION relative to program code.
#

import argparse
import configparser
import csv
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
import exiftool
import logging

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from ghexif import COLUMN_NAMES, SELECTED_FIELDS, SVException
import ghexif.projectlib
from ghexif.handling import deriveUniquePictureName
from ghexif.exif_writer import exiftool_write
import ghexif.exceptions
import ghexif.statemachine

IGNOREEXTENSIONS = ['txt', 'ico', 'html']
logger = logging.getLogger("speciesviewer")

def main(args, config):
    starttime = time.time()
    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))  
    
    logger.info(f'Will read files recursively from {MyProject.getProjectPath()}.')
    # logging.basicConfig(level=logging.DEBUG)
    # exiftool.ExifTool.__init__(None, common_args=['-G'])
    # ExifToolHelper(check_execute=True) raises on any non-zero exit
    # status — including the routine status=1 that exiftool emits when
    # there are non-fatal warnings (very common at large file counts:
    # unreadable thumbnails, ignorable extensions, etc.). The JSON
    # payload is still on stdout in that case, so we'd rather see the
    # data than discard it.
    # -charset utf8 / filename=utf8 makes exiftool interpret file contents and
    # filenames as UTF-8 (instead of the Windows local code page). encoding="utf-8"
    # is also required: without it, pyexiftool decodes the subprocess pipe via
    # locale.getpreferredencoding() (cp1252 on Windows), so even when exiftool
    # outputs UTF-8 we'd see mojibake (e.g. ü → Ã¼). Both are needed.
    with exiftool.ExifToolHelper(
        logger=logging.getLogger("speciesviewer"),
        common_args=["-G", "-charset", "utf8", "-charset", "filename=utf8"],
        encoding="utf-8",
        check_execute=False,
    ) as et:
        if not os.path.isdir(MyProject.getProjectPath()):
            raise SVException(f"Directory {MyProject.getProjectPath()} cannot be found, or refers to a file.")

        logger.info(f"Reading directories (takes about 1 minute for every 4000 files)...")
        file_extension_count = {}        
         
        try:
            metadata = et.get_metadata(
                MyProject.getProjectPath(), 
                params=SELECTED_FIELDS)
        except Exception as e:
            raise SVException(f"No files found in the directory {MyProject.getProjectPath()}\nError {e}: {e.stderr}{e.stdout}.")
#            logger.warning(f"No files found in the directory {MyProject.getProjectPath()}\nError {e}: {e.stderr}{e.stdout}.")

 
        datafilelocation = MyProject.getMetaDirReview()
        os.makedirs(datafilelocation, exist_ok=True) 
        datafilelocation = os.path.join(datafilelocation, "data_files.csv")

        metadatareadertime = time.time() - starttime
        logger.info(
            f"Reading EXIF metadata took {metadatareadertime:.1f} seconds for {len(metadata)} files."
        )

        # Build all rows first (needed for GPS backfill)
        all_rows = []
        for d in metadata:
            row = {}
            for selected_field in SELECTED_FIELDS:
                if (
                    selected_field in COLUMN_NAMES
                    and COLUMN_NAMES[selected_field] in d
                ):
                    row[COLUMN_NAMES[selected_field]] = d[
                        COLUMN_NAMES[selected_field]
                    ]
                # some fields are merged
                elif selected_field == "-IFD0:Model" and "EXIF:Model" in d:
                    row["CameraModel"] = d["EXIF:Model"]
                    if row["CameraModel"] == "OM-1MarkII":
                        row["CameraModel"] = "OM-1 Mark II"
                elif selected_field == "-XMP-tiff:Model" and "XMP:Model" in d:
                    row["CameraModel"] = d["XMP:Model"]
                elif selected_field == "-IFD0:Make" and "EXIF:Make" in d:
                    row["CameraMake"] = d["EXIF:Make"]
                elif selected_field == "-XMP-tiff:Make" and "XMP:Make" in d:
                    row["CameraMake"] = d["XMP:Make"]
                elif (
                    selected_field == "-lensserialnumber"
                    and "MakerNotes:LensSerialNumber" in d
                ):
                    row["LensSerialNumber"] = d["MakerNotes:LensSerialNumber"]
                elif (
                    selected_field == "-exifIFD:lensserialnumber"
                    and "EXIF:LensSerialNumber" in d
                ):
                    row["LensSerialNumber"] = d["EXIF:LensSerialNumber"]
                elif selected_field == "-ImageSize" and "Composite:ImageSize" in d:
                    row["ImageSize"] = d["Composite:ImageSize"]
                elif (
                    selected_field == "-imagesize" and "Composite:ImageSize_1" in d
                ):
                    row["ImageSize"] = d["Composite:ImageSize_1"]

            # find unique identifier for all files based on a file click
            row["UniquePictureName"] = deriveUniquePictureName(
                row[COLUMN_NAMES["-filename"]]
            )
            # derived values for easy of use
            name, extension = os.path.splitext(row["File:FileName"])
            row["WindowsExtension"] = extension[1:].lower()
            row["WindowsFileName"] = (
                row["File:Directory"] + "/" + row["File:FileName"]
            )
            # do not introduce noise
            if row["WindowsExtension"] not in IGNOREEXTENSIONS:
                all_rows.append(row)
                # maintain counters per type
                file_extension_count[extension[1:].lower()] = (
                    file_extension_count.get(extension[1:].lower(), 0) + 1
                )

        # GPS backfill.
        #
        # Pass 1 (forward window): fill missing GPS from the first later picture
        # that has GPS, within GPS_BACKFILL_SECONDS. Compensates for Bluetooth GPS
        # connection delay at the start of a shooting session.
        #
        # Pass 2 (bracket fill): for rows still missing GPS, look at the nearest
        # GPS-bearing photo before AND after. If those two known positions are
        # within GPS_BRACKET_MAX_DISTANCE_M of each other, the subject barely moved
        # (typical slow birding), so copy the closer-in-time donor's position.
        # The distance gate naturally rejects bracketing across separate sessions.
        GPS_BACKFILL_SECONDS = 120
        GPS_BRACKET_MAX_DISTANCE_M = 100
        GPS_FIELDS = ["EXIF:GPSLatitude", "EXIF:GPSLongitude", "Composite:GPSPosition", "EXIF:GPSAltitude"]
        LOCATION_FIELDS = ["XMP:Location", "XMP:City", "XMP:State", "XMP:Country"]
        WARN_EXTENSIONS = {"orf", "arw", "cr2", "cr3", "nef", "rw2", "raf", "dng", "jpg", "jpeg"}

        def _parse_dt(row):
            dt_str = row.get("EXIF:DateTimeOriginal", "")
            if not dt_str:
                return None
            try:
                return datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                return None

        def _has_gps(row):
            return bool(row.get("EXIF:GPSLatitude") or row.get("Composite:GPSPosition"))

        _dms_re = re.compile(
            r"(\d+(?:\.\d+)?)\s*deg\s*(\d+(?:\.\d+)?)['’]\s*"
            r"(\d+(?:\.\d+)?)[\"”]\s*([NSEW])",
            re.IGNORECASE,
        )

        def _parse_position(pos_str):
            # exiftool's Composite:GPSPosition format: '41 deg 10\' 53.34" N, 23 deg 6\' 14.94" E'
            if not pos_str:
                return None, None
            matches = _dms_re.findall(pos_str)
            if len(matches) != 2:
                return None, None
            coords = []
            for deg, minutes, sec, hemi in matches:
                val = float(deg) + float(minutes) / 60.0 + float(sec) / 3600.0
                if hemi.upper() in ("S", "W"):
                    val = -val
                coords.append(val)
            return coords[0], coords[1]

        def _haversine_m(lat1, lon1, lat2, lon2):
            R = 6371000.0
            p1, p2 = math.radians(lat1), math.radians(lat2)
            dp = math.radians(lat2 - lat1)
            dl = math.radians(lon2 - lon1)
            a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            return 2 * R * math.asin(math.sqrt(a))

        # Build a lookup from UniquePictureName to rows by extension
        upn_index = {}
        for row in all_rows:
            upn = row.get("UniquePictureName", "")
            ext = row.get("WindowsExtension", "")
            if upn:
                upn_index.setdefault(upn, {})[ext] = row

        # Sort by DateTimeOriginal for chronological processing
        all_rows.sort(key=lambda r: r.get("EXIF:DateTimeOriginal", ""))

        backfilled_pairs = []  # (target_upn, donor_upn)

        def _record_backfill(target, donor, reason):
            for field in GPS_FIELDS:
                if donor.get(field):
                    target[field] = donor[field]
            backfilled_pairs.append((
                target.get("UniquePictureName", ""),
                donor.get("UniquePictureName", ""),
            ))
            logger.info(
                f"GPS backfill: {target.get('File:FileName', '?')} "
                f"<- {donor.get('File:FileName', '?')} ({reason})"
            )

        # Pass 1: forward window
        for i, row in enumerate(all_rows):
            if _has_gps(row) or _parse_dt(row) is None:
                continue
            row_dt = _parse_dt(row)
            for j in range(i + 1, len(all_rows)):
                candidate = all_rows[j]
                cand_dt = _parse_dt(candidate)
                if cand_dt is None:
                    continue
                if (cand_dt - row_dt) > timedelta(seconds=GPS_BACKFILL_SECONDS):
                    break
                if _has_gps(candidate):
                    _record_backfill(row, candidate, f"+{(cand_dt - row_dt).seconds}s forward")
                    break

        # Pass 2: bracket fill with distance gate
        for i, row in enumerate(all_rows):
            if _has_gps(row) or _parse_dt(row) is None:
                continue
            row_dt = _parse_dt(row)
            before = None
            for j in range(i - 1, -1, -1):
                c = all_rows[j]
                c_dt = _parse_dt(c)
                if c_dt is None:
                    continue
                if _has_gps(c):
                    before = (c, c_dt)
                    break
            after = None
            for j in range(i + 1, len(all_rows)):
                c = all_rows[j]
                c_dt = _parse_dt(c)
                if c_dt is None:
                    continue
                if _has_gps(c):
                    after = (c, c_dt)
                    break
            if before is None or after is None:
                continue
            b_lat, b_lon = _parse_position(before[0].get("Composite:GPSPosition", ""))
            a_lat, a_lon = _parse_position(after[0].get("Composite:GPSPosition", ""))
            if b_lat is None or a_lat is None:
                continue
            dist_m = _haversine_m(b_lat, b_lon, a_lat, a_lon)
            if dist_m > GPS_BRACKET_MAX_DISTANCE_M:
                continue
            b_gap = (row_dt - before[1]).total_seconds()
            a_gap = (after[1] - row_dt).total_seconds()
            donor = before[0] if b_gap <= a_gap else after[0]
            gap = int(min(b_gap, a_gap))
            _record_backfill(row, donor, f"bracket fill (donors {dist_m:.0f}m apart, {gap}s away)")

        gps_backfill_count = len(backfilled_pairs)

        # Second pass: propagate donor's GPS and (when available) location text
        # to the target's XMP sibling, and write the new fields to disk.
        # GPS and location are handled independently — a donor with only GPS
        # still gets GPS written, even if it has no city/location text yet.
        gps_xmp_written_count = 0
        location_xmp_written_count = 0
        xmp_already_complete_count = 0
        xmp_sibling_missing_count = 0
        for target_upn, donor_upn in backfilled_pairs:
            target_xmp = upn_index.get(target_upn, {}).get("xmp")
            donor_xmp = upn_index.get(donor_upn, {}).get("xmp")
            if not (target_xmp and donor_xmp):
                xmp_sibling_missing_count += 1
                continue

            target_has_gps = any(target_xmp.get(f) for f in GPS_FIELDS)
            target_has_location = any(target_xmp.get(f) for f in LOCATION_FIELDS)

            exif_args = []
            wrote_gps = False
            wrote_location = False
            if not target_has_gps:
                for field in GPS_FIELDS:
                    if donor_xmp.get(field):
                        target_xmp[field] = donor_xmp[field]
                        exif_args.append(f"-{field}={donor_xmp[field]}")
                        wrote_gps = True
            if not target_has_location:
                for field in LOCATION_FIELDS:
                    if donor_xmp.get(field):
                        target_xmp[field] = donor_xmp[field]
                        exif_args.append(f"-{field}={donor_xmp[field]}")
                        wrote_location = True

            if not exif_args:
                xmp_already_complete_count += 1
                continue

            xmp_filepath = target_xmp.get("WindowsFileName", "")
            if not xmp_filepath:
                continue
            parts = []
            if wrote_gps:
                parts.append("GPS")
            if wrote_location:
                parts.append("location")
            try:
                exiftool_write(xmp_filepath, exif_args)
                logger.info(
                    f"GPS backfill: wrote {' + '.join(parts)} to "
                    f"{target_xmp.get('File:FileName', '?')}"
                )
                if wrote_gps:
                    gps_xmp_written_count += 1
                if wrote_location:
                    location_xmp_written_count += 1
            except subprocess.CalledProcessError as e:
                logger.warning(
                    f"GPS backfill: failed to write "
                    f"{target_xmp.get('File:FileName', '?')}: {e.stderr}"
                )

        if gps_backfill_count:
            logger.info(
                f"GPS backfill: filled GPS in {gps_backfill_count} CSV row(s) "
                f"from neighbouring pictures."
            )
            xmp_summary = (
                f"GPS backfill: XMP siblings — wrote GPS to {gps_xmp_written_count}, "
                f"location to {location_xmp_written_count}; "
                f"{xmp_already_complete_count} already complete"
            )
            if xmp_sibling_missing_count:
                xmp_summary += f"; {xmp_sibling_missing_count} XMP sibling(s) not found"
            xmp_summary += "."
            logger.info(xmp_summary)

        # Warn loudly for any picture still lacking GPS after both passes.
        # Sidecar .xmp twins are skipped to avoid double-warning per UPN.
        unfilled = [
            r for r in all_rows
            if not _has_gps(r) and r.get("WindowsExtension", "") in WARN_EXTENSIONS
        ]
        for r in unfilled:
            logger.warning(f"GPS missing: {r.get('File:FileName', '?')}")

        # Write all rows to CSV
        with open(datafilelocation, "w", newline="", encoding='utf-8') as csvfile:
            writer = csv.DictWriter(
                csvfile, fieldnames=COLUMN_NAMES.values(), quoting=csv.QUOTE_MINIMAL, delimiter=';'
            )
            writer.writeheader()
            for row in all_rows:
                writer.writerow(row)

    # give the filetype report ome space
    logger.info("")
    logger.info(f"The files counted by filetype are:")
    for extent, count in file_extension_count.items():
        logger.info(f"   {extent.upper()} files: {count}")
    logger.info("")

    totaltime = time.time() - starttime
    logger.info(
        f'CSV file written to {datafilelocation}, total time {totaltime:.1f} seconds.'
    )


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="ReadDirExif")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = args.config
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    # init the logging subsystem
    logging.basicConfig(level=logging.INFO)
    # define state machine and see if we can run this step
    statemachine = ghexif.statemachine.StateMachine(config.get("repository","BiotaDatabase"), config.get("project","PictureDirectory"))
    # call the actual code
    try:
        if not statemachine.stateOKtorun(parser.description):
            statemachine.explainAbort(parser.description)        
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
        statemachine.updateState(parser.description, False, e.args[0])
    else:
        logger.info(f"{parser.description} succeeded")
        statemachine.updateState(parser.description, True, "Succesfully run.")
    # Store state in statemachine file for this project
    statemachine.writeState()

