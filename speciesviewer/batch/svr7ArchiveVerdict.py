#!/usr/bin/env python3

# Archive the trip's Count Species result into the master, where it outlives
# the trip repository. Runs immediately after svr2CountSpecies as step 1 of
# Merge Pictures into Master — before anything touches the master, so the
# verdict is taken while the master is still pre-trip.
#
# See engine/trip_verdict.py for what gets written and why it goes to the
# master rather than staying in the trip.

import argparse
import configparser
import logging
import os
import sys
import time

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine.trip_verdict
from engine import SVException
from utils.trip_identity import resolve
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")


def main(args, config):
    starttime = time.time()

    trip_repo = config.get("repository", "BiotaDatabase")
    master_repo = config.get("master", "MasterBiotaDatabase", fallback="")
    if not master_repo:
        raise SVException(
            "Archive Verdict runs on a trip, not on the master itself: no "
            "master repository is configured for this run.")
    if os.path.normcase(os.path.normpath(trip_repo)) == \
            os.path.normcase(os.path.normpath(master_repo)):
        raise SVException(
            "Archive Verdict runs on a trip, but the trip and master "
            "repositories are the same directory.")

    ident = resolve(trip_repo, kind="trip")
    trip_ini = os.path.join(trip_repo, "trip.ini")
    fromdate = todate = ""
    if os.path.isfile(trip_ini):
        from utils.trip_config import read_trip_ini
        data = read_trip_ini(trip_repo) or {}
        fromdate = data.get("fromdate", "")
        todate = data.get("todate", "")

    row = engine.trip_verdict.archive(
        trip_repo, master_repo,
        trip_id=ident.trip_id,
        trip_name=ident.title_nl or ident.title_en or ident.web_slug,
        fromdate=fromdate, todate=todate,
    )

    logger.info(f"Archived species verdict for {row['trip_id']}: "
                f"{row['new_count']} new of {row['species_count']} species "
                f"in {time.time() - starttime:.1f}s.")
    return row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svr7ArchiveVerdict")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    args = parser.parse_args()
    config = configparser.ConfigParser()
    read_ini(config, args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
