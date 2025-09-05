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
    # get repository-wide sc
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)
    trip = MyProject.Trip

    # test set up is to use master, and merge collection on hardcoded place
    # read the list with all pre-defined species
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(metadatadir + "active/speciescollection.csv", False)    
    newSC = sc.scCopy()     # keep the old version to merge
    sc.clearDict()
    # now read the master we want to merge more in 
    tomergeSpeciesCollection = MyProject.MasterRepository + '/metadata/active/speciescollection.csv'
    sc.readSpeciesCollection(tomergeSpeciesCollection, True)
    # merge (brute force is not too bad)
    sc.mergeSpeciesCollections(newSC)
    # write to new location
    sc.updateSpeciesCollection(metadatadir + "active/speciescollection-merged.csv")  
        
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv5MergeSpeciesCollection")
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
