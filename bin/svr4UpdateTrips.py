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

logger = logging.getLogger(__name__)

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    return config



    # # make summary report
    # print(f"Summary of projects and progress for each report")     
    # print(f"{'Project':15}")   
    # for statemachine in days_dict:
    #     line = f"{statemachine:15}"
    #     for task in days_dict[statemachine]:
    #         if task != "MakeRepository":
    #             if days_dict[statemachine][task][0]:
    #                 line += f" {task:15}"
    #             else:
    #                 line += f" {'':15}"
    #     print(line)
    return trips_dict


def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))
    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    trip = ghexif.triplib.Trip(MyProject)
    MyProject.registerTrip(trip)    
    
    # Collect states
    directory_to_scan = 'C:/laptop/trips'
    trip.getCollectTrips(directory_to_scan, 'c:/laptop/')
    # generate website
    trip.updateTripIndexPage('nl')
    trip.updateTripIndexPage('en')

    # sFTP index files, assume all picture are there
    pwd = config.get("websettings","Sftppwd").strip("'")
    sftp = trip.openSFTP(pwd)
    sftp.uploadTrip(sftp)
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
