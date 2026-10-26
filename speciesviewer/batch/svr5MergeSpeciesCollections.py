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

import engine
import engine.projectlib
import engine.exceptions
import engine.triplib
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    return config

def trip_picture_names(metadatadir):
    """Every species name the trip's pictures carry (WetSoortNaam and subject,
    lowercase) — active and every review day. The merge keeps a "?" placeholder
    only when one of these uses it."""
    import csv, glob, ast
    names = set()
    paths = [os.path.join(metadatadir, "active", "data_pictures.csv")]
    paths += glob.glob(os.path.join(metadatadir, "review", "*", "data_pictures.csv"))
    for path in paths:
        try:
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter=";"):
                    names.add((row.get("WetSoortNaam") or "").strip().lower())
                    subject = (row.get("subject") or "").strip()
                    try:
                        parts = ast.literal_eval(subject) if subject.startswith("[") else [subject]
                    except Exception:
                        parts = [subject]
                    names.update(str(p).strip().lower() for p in parts)
        except OSError:
            continue
    names.discard("")
    return names


def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))
    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # get repository-wide sc
    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)
    trip = MyProject.Trip

    # Read the trip's species collection first (this is the source of new
    # entries to fold into master).
    from engine.conversion_guard import refuse_if_behind
    refuse_if_behind(MyProject.getRepositoryPath(), MyProject.MasterRepository,
                     "Merging this trip's species list into the master")
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)
    newSC = sc.scCopy()
    sc.clearDict()
    # Read master, merge trip entries in, and write the result back over
    # master. updateSpeciesCollection renames the existing master file to
    # speciescollection.csvold first, giving the GUI a transient backup it
    # can use to drive the Keep / Restore review flow.
    masterSpeciesCollection = os.path.join(MyProject.MasterRepository, 'metadata', 'active', 'speciescollection.csv')
    sc.readSpeciesCollection(masterSpeciesCollection, True)
    sc.mergeSpeciesCollections(newSC, used_names=trip_picture_names(metadatadir))
    sc.updateSpeciesCollection(masterSpeciesCollection)
        
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="svr5MergeSpeciesCollection")
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
