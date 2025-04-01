#!/usr/bin/env python3

import re
import csv
import os
import sys
import configparser
import time
import logging
import argparse
from pathlib import Path

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

logger = logging.getLogger(__name__)

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
    # if trip, find master
    MyProject.initMasterAndTrip(config, inittrip=False)
    trip = MyProject.Trip
    # read project directory from master website (eg /nature/includes web/), 
    # such that projects can be added from different computers

    # get existing trips, and put them in dictionary
    url = "https://houtekamer.com/nature/includes web/trip_admin.csv"
    trip.read_TripAdmin_from_web(url)

    # Read the trip file for the current project
    tripfilename = os.path.join(MyProject.Repository, 'trip.ini')
    triprow = trip.readTrip(tripfilename)
    logger.info(f"Read trip information from {MyProject.Trip.tripname} refreshed or added.")
    trip.trip_dict[MyProject.Trip.tripname] = triprow

    # save the csv file with all projects to disk and on the web
    trip.write_TripAdmin()

    # generate website
    trip.updateTripIndexPage('nl')
    trip.updateTripIndexPage('en')

    # sFTP index files, assume all picture are there
    pwd = config.get("websettings","Sftppwd").strip("'")
    sftp = trip.openSFTP(pwd)
    trip.uploadTrip(sftp)
    path = "/nature/includes web/trip_admin.csv"
    trip.uploadTripAdmin(sftp, path)
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
