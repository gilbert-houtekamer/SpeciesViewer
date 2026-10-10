#!/usr/bin/env python3

# Clear all trip review data and reset active CSV files to empty templates.

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

import engine.projectlib
from engine.tripresetlib import clear_review_directories, reset_active_data
from engine import SVException
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")


def main(args, config):
    starttime = time.time()
    MyProject = engine.projectlib.Project(
        config.get("repository", "BiotaDatabase"),
        config.get("project", "PictureDirectory"),
        config.get("repository", "BiotaWebRoot"),
    )
    MyProject.initMasterAndTrip(config, inittrip=False)

    if MyProject.MasterRepository == "":
        raise SVException("Empty Trip Data can only work on trips, not on the master repository.")

    repo = MyProject.getRepositoryPath()
    removed = clear_review_directories(repo)
    reset_active_data(repo)

    totaltime = time.time() - starttime
    msg = f"Cleared {removed} day directories and reset active data in {totaltime:.1f}s."
    logger.info(msg)
    return msg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svu6EmptyTripData")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG") or str(svconfig_path()),
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
