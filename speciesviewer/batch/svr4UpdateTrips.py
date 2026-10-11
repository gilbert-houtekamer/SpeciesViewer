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

import engine
import engine.projectlib
import engine.exceptions
import engine.publish_index
import engine.triplib
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    return config

def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))
    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"),
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # if trip, initialze master — pass inittrip=False so init does not
    # auto-call readAndWriteTripAdmin; we run it explicitly below to keep
    # this script self-contained (and to avoid the duplicate CSV write
    # that the auto-call produces in combination with our own).
    MyProject.initMasterAndTrip(config, inittrip=False)
    trip = MyProject.Trip

    # Update the master trip admin CSV with a fresh row for the *active*
    # trip (only). All other trips already in the index are kept untouched,
    # so siblings still in draft on disk do not get published.
    #
    # The base is the LIVE index, not this machine's local copy (the same rule
    # svv5AddToMaster follows for albums). Trips are machine-specific: a laptop
    # can hold an early copy of a trip that the big machine has since carried
    # further, and its local master can be behind. Since the whole index is
    # regenerated from whatever base is loaded, a stale local base would drop
    # entries published elsewhere or resurrect ones that were removed.
    engine.publish_index.load_admin_base(trip, config)
    tripinifile = os.path.join(config.get("repository", "BiotaDatabase"), "trip.ini")
    trip_row = trip.readTrip(tripinifile)
    if not trip_row:
        raise engine.exceptions.SVException(
            f"Could not read trip information from {tripinifile}.")
    trip.addTrip(trip_row)
    trip.write_TripAdmin()
    logger.info(f"Master trip_admin.csv updated with "
                f"'{trip_row.get('trip_name', '?')}'.")
    # Regenerate the master tripindex pages from the now-current CSV.
    trip.updateTripIndexPage('nl')
    trip.updateTripIndexPage('en')

    # Upload index pages and the trip admin CSV to the website. The CSV
    # mirror lets a different machine pick up the latest published state
    # even without Dropbox sync of the master.
    pwd = config.get("websettings","Sftppwd").strip("'")
    sftp = trip.openSFTP(pwd)
    # The trip index lives on the MASTER's part of the site — make sure that part
    # exists (intro, stylesheet) before listing the trip there.
    from batch.sv7WriteCommit import ensureSiteSkeletons
    ensureSiteSkeletons(trip.mp, sftp)
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
        default=os.environ.get("SVCONFIG") or str(svconfig_path()),
        help="Module Configuration File",
    )
    parser.add_argument(
        "--dir", type=str, default=os.path.join(modpath, "data"), help="foto directory"
    )
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = args.config
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    # init the logging subsystem
    logging.basicConfig(level=logging.INFO)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
