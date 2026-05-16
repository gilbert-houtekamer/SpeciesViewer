#!/usr/bin/env python3

import re
import csv
import os
import sys
import configparser
import time
import logging
import argparse

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import ghexif
import ghexif.projectlib
import ghexif.exceptions
import ghexif.triplib

logger = logging.getLogger("speciesviewer")

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    return config

def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))
    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatabase"),
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # if trip, initialze master — pass inittrip=False so init does not
    # auto-call readAndWriteTripAdmin; we run it explicitly below to keep
    # this script self-contained (and to avoid the duplicate CSV write
    # that the auto-call produces in combination with our own).
    MyProject.initMasterAndTrip(config, inittrip=False)
    trip = MyProject.Trip

    # Update the master trip admin CSV with a fresh row for the *active*
    # trip (only). All other historic trips already in the CSV are kept
    # untouched, so siblings still in draft on disk do not get published.
    trip.readAndWriteTripAdmin()
    # Regenerate the master tripindex pages from the now-current CSV.
    trip.updateTripIndexPage('nl')
    trip.updateTripIndexPage('en')

    # Upload index pages and the trip admin CSV to the website. The CSV
    # mirror lets a different machine pick up the latest published state
    # even without Dropbox sync of the master.
    pwd = config.get("websettings","Sftppwd").strip("'")
    sftp = trip.openSFTP(pwd)
    trip.uploadTrip(sftp)
    trip.uploadTripAdmin(sftp, trip.getTripAdminWeb())
    sftp.disconnect()

        
    # print(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv4UpdateTrips")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    parser.add_argument(
        "--dir", type=str, default=os.path.join(modpath, "data"), help="foto directory"
    )
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = args.config
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    # init the logging subsystem
    logging.basicConfig(level=logging.INFO)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
