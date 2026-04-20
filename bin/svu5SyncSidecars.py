#!/usr/bin/env python3

# Synchronize crop and exposure edits between DxO PhotoLab (.dop) and Lightroom (.xmp) sidecar files.

import argparse
import configparser
import os
import sys
import time
import logging

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from ghexif.sidecarlib import sync_sidecars
import ghexif.projectlib

RAW_EXTENSIONS = {'.orf', '.nef', '.nrw', '.heic'}
logger = logging.getLogger("speciesviewer")


def main(args, config):
    starttime = time.time()
    MyProject = ghexif.projectlib.Project(
        config.get("repository", "BiotaDatabase"),
        config.get("project", "PictureDirectory"),
        config.get("repository", "BiotaWebRoot"),
    )

    picture_dir = MyProject.getProjectPath()
    if not os.path.isdir(picture_dir):
        logger.warning(f"Directory {picture_dir} cannot be found.")
        return

    logger.info(f"Scanning for sidecars in {picture_dir} ...")

    dop_to_xmp = 0
    xmp_to_dop = 0
    no_dop = []
    skipped = 0

    for root, dirs, files in os.walk(picture_dir):
        for filename in files:
            _, ext = os.path.splitext(filename)
            if ext.lower() in RAW_EXTENSIONS:
                raw_path = os.path.join(root, filename)
                result = sync_sidecars(raw_path)
                if result == "NO_DOP":
                    no_dop.append(filename)
                elif result:
                    logger.info(f"  {filename}: {result}")
                    if result.startswith("DOP->XMP"):
                        dop_to_xmp += 1
                    else:
                        xmp_to_dop += 1
                else:
                    skipped += 1

    total_synced = dop_to_xmp + xmp_to_dop
    totaltime = time.time() - starttime

    if dop_to_xmp:
        logger.info(f"Synced DOP\u2192XMP: {dop_to_xmp} file(s)  \u2192 reload metadata in Lightroom.")
    if xmp_to_dop:
        logger.info(f"Synced XMP\u2192DOP: {xmp_to_dop} file(s)  \u2192 read sidecars in PhotoLab.")
    if not total_synced:
        logger.info(f"No sidecar changes detected ({skipped} file(s) checked, {totaltime:.1f}s).")
    if no_dop:
        logger.warning(f"No DOP found for {len(no_dop)} file(s) with XMP edits"
                       f" \u2192 add to PhotoLab first, then re-run sync:")
        for filename in no_dop:
            logger.warning(f"    {filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svu5SyncSidecars")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
