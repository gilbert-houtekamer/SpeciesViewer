#!/usr/bin/env python3
"""Zip a trip's admin for transfer to another machine.

Writes <trip folder name>.zip into the target directory. It holds trip.ini, the
trip's two images, its species collection, its lifer baseline, its own
translation cache and a manifest — kilobytes, not gigabytes. The picture data
never travels: copy CurrentPictures with the OS, and Read & Summarize rebuilds
the per-day CSVs on the other machine from the sidecars.

What is in and out, and why, lives in engine/trip_transfer.py.
Running twice is safe: the existing ZIP is overwritten.
"""

import argparse
import configparser
import os
import sys
import time
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from engine.exceptions import SVException
import engine.trip_transfer as trip_transfer
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")


def main(args, config):
    starttime = time.time()

    source_root = config.get("repository", "BiotaDatabase", fallback="")
    target_dir = config.get("utilities", "trip_export_target", fallback="")
    master_root = config.get("master", "MasterBiotaDatabase", fallback="")
    master_web = config.get("master", "MasterBiotaWebRoot", fallback="")

    try:
        info = trip_transfer.export_trip(source_root, target_dir, master_root=master_root,
                                         master_web_root=master_web)
    except ValueError as e:
        raise SVException(str(e))

    if info["missing"]:
        # INFO, not a warning: a trip that has no translations yet, or no lifer
        # baseline, is a perfectly normal trip — flagging it yellow would send
        # the user hunting for a problem that isn't there. Still logged, so an
        # import that finds them absent is not a surprise.
        logger.info("Trip export: not present on this machine, so not included: "
                    + ", ".join(info["missing"]))

    logger.info("Trip export: " + trip_transfer.copy_instruction(info))

    elapsed = time.time() - starttime
    published = {"yes": "published", "no": "not published",
                 "unknown": "publication unknown"}[info["published"]]
    msg = (f"Trip export done in {elapsed:.1f}s: {len(info['included'])} files, "
           f"{info['size_kb']:.0f} kB → {info['zip_path']} ({published}).")
    logger.info(msg)
    return msg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svr6ExportTrip — zip a trip's admin for transfer")
    parser.add_argument(
        "--config",
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Configuration file",
    )
    parser.add_argument("--target", required=True, help="Target directory to write the ZIP into")
    args = parser.parse_args()
    config = configparser.ConfigParser()
    read_ini(config, args.config)
    if not config.has_section("utilities"):
        config.add_section("utilities")
    config["utilities"]["trip_export_target"] = args.target
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"svr6ExportTrip failed: {e}")
    else:
        logger.info("svr6ExportTrip succeeded")
