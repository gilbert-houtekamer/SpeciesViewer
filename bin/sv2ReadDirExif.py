#!/usr/bin/env python3

# Scan the files in a directory, and keep selected EXIF properties in a CSV file.
#
# The files are specified in "svReadDir.ini" file, 
# currently stored in INI_LOCATION relative to program code.
#

import argparse
import configparser
import csv
import os
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

        # GPS backfill: fill missing GPS from the first later picture that has GPS,
        # within a 30-second window. This compensates for Bluetooth GPS connection
        # delay at the start of a shooting session.
        GPS_BACKFILL_SECONDS = 30
        GPS_FIELDS = ["EXIF:GPSLatitude", "EXIF:GPSLongitude", "Composite:GPSPosition", "EXIF:GPSAltitude"]
        LOCATION_FIELDS = ["XMP:Location", "XMP:City", "XMP:State", "XMP:Country"]

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

        # Build a lookup from UniquePictureName to rows by extension
        upn_index = {}
        for row in all_rows:
            upn = row.get("UniquePictureName", "")
            ext = row.get("WindowsExtension", "")
            if upn:
                upn_index.setdefault(upn, {})[ext] = row

        # Sort by DateTimeOriginal for chronological processing
        all_rows.sort(key=lambda r: r.get("EXIF:DateTimeOriginal", ""))

        gps_backfill_count = 0
        backfilled_pairs = []  # (target_upn, donor_upn)
        for i, row in enumerate(all_rows):
            if _has_gps(row) or _parse_dt(row) is None:
                continue
            row_dt = _parse_dt(row)
            # Look forward for the first picture with GPS within the window
            for j in range(i + 1, len(all_rows)):
                candidate = all_rows[j]
                cand_dt = _parse_dt(candidate)
                if cand_dt is None:
                    continue
                if (cand_dt - row_dt) > timedelta(seconds=GPS_BACKFILL_SECONDS):
                    break
                if _has_gps(candidate):
                    for field in GPS_FIELDS:
                        if field in candidate:
                            row[field] = candidate[field]
                    gps_backfill_count += 1
                    backfilled_pairs.append((
                        row.get("UniquePictureName", ""),
                        candidate.get("UniquePictureName", "")
                    ))
                    logger.info(
                        f"GPS backfill: {row.get('File:FileName', '?')} "
                        f"<- {candidate.get('File:FileName', '?')} "
                        f"({(cand_dt - row_dt).seconds}s later)"
                    )
                    break

        # Second pass: backfill XMP location fields from donor's XMP sibling
        # and write the updated fields back to the XMP file on disk
        xmp_backfill_count = 0
        for target_upn, donor_upn in backfilled_pairs:
            target_xmp = upn_index.get(target_upn, {}).get("xmp")
            donor_xmp = upn_index.get(donor_upn, {}).get("xmp")
            if target_xmp and donor_xmp:
                has_location = any(donor_xmp.get(f) for f in LOCATION_FIELDS)
                target_already_has_location = any(target_xmp.get(f) for f in LOCATION_FIELDS)
                if has_location:
                    exif_args = []
                    for field in LOCATION_FIELDS + GPS_FIELDS:
                        if donor_xmp.get(field):
                            target_xmp[field] = donor_xmp[field]
                            exif_args.append(f"-{field}={donor_xmp[field]}")
                    xmp_backfill_count += 1
                    # Only write to disk if the XMP didn't already have location data
                    if not target_already_has_location:
                        xmp_filepath = target_xmp.get("WindowsFileName", "")
                        if xmp_filepath and exif_args:
                            try:
                                exiftool_write(xmp_filepath, exif_args)
                                logger.info(f"GPS backfill: wrote location to {target_xmp.get('File:FileName', '?')}")
                            except subprocess.CalledProcessError as e:
                                logger.warning(f"GPS backfill: failed to write {target_xmp.get('File:FileName', '?')}: {e.stderr}")

        if gps_backfill_count:
            logger.info(f"GPS backfill: filled {gps_backfill_count} RAW file(s) from later pictures.")
        if xmp_backfill_count:
            logger.info(f"GPS backfill: wrote location data to {xmp_backfill_count} XMP file(s) on disk.")

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

