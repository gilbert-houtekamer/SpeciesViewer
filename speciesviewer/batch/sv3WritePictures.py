#!/usr/bin/env python3

# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import argparse
import csv
import os
import sys
import configparser
import time
from pygbif import species
from collections import OrderedDict
from enum import Enum, auto
from engine import SVException
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine
import engine.projectlib
import engine.specieslib
import engine.speciesclassification
from engine import COLUMN_NAMES, SELECTED_FIELDS
from engine.csv_columns import PICTURE_FIELDS
#from engine.specieslib import readSpeciesInfo, writeSpeciesInfo, computeRating
from engine.handling import read_file, read_pictures
from engine.specieslib import Level, Species
import engine.exceptions
import engine.statemachine

logger = logging.getLogger("speciesviewer")

DUMMYSPECIES = ['vogel', 'vogels', 'bird', 'libel', 'libellen', 'onbekend',
                'rups', 'panama', 'californie', 'onbekend', 'sprinkhaan', 'turkey',
                'blog', 'goodidpicture']


def main(args, config):
    global starttime, SpeciesDebug
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
    # get repository-wide sc and per project sp.
    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)   
    MyProject.setSpeciesDebug(SpeciesDebug)

    # start message
    logger.info(f"v3WritePictures: Read file CSV and create enriched species data for each picture.")
    # check if the output can be written, avoid annoying wait
    picturefilename = MyProject.getMetaDirReview()
    try:
        pictures_out_csvfile = open(os.path.join(picturefilename, "data_pictures.csv"), "w", encoding='utf-8', newline="")
    except:
        raise SVException(f"Picture file ({os.path.join(picturefilename, 'data_pictures.csv')}) is locked by (most likely) Excel, please close it in Excel and re-run.")
    try:
        species_out_csvfile = open(os.path.join(picturefilename, "species.csv"), "w", encoding='utf-8', newline="")
        species_out_csvfile.close()
    except:
        raise SVException(f"Picture file ({os.path.join(picturefilename, 'species.csv')}) is locked by (most likely) Excel, please close it in Excel and re-run.")
    #logger.info(f"Species file ({MyProject.getMetaDirReview()+"species.csv"}) is locked by (most likely) Excel, please close it in Excel and re-run.")

    # read the list with all pre-defined species
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)
    # also read list with all known species
    # and count the number of data file items we already have
    sp.readSpeciesInfo(os.path.join(metadatadir, "active", "species.csv"), False)
    RepositoryFileCount = read_file(MyProject, os.path.join(metadatadir, "active", "data_files.csv"))
    RepositoryPictureCount = read_pictures(MyProject, os.path.join(metadatadir, "active", "data_pictures.csv"))

    # read new files from project to review
    read_file(MyProject, os.path.join(MyProject.getMetaDirReview(), "data_files.csv"))
    logger.info(f"Read all new data from the in {time.time()-starttime:.1f} seconds.")
    thissteptime = time.time()   

    # Now summarize files to files list per picture ('UniquePictureName')
    # see how many are part of base
    rowCount = 0
    for row in MyProject.files_dict:
        uniquename = MyProject.files_dict[row]['UniquePictureName']
        rowCount += 1
        if uniquename not in MyProject.UniquePictureName_dict:
            MyProject.UniquePictureName_dict[uniquename] = MyProject.CreatePicture(uniquename)

            if rowCount == RepositoryFileCount:
                RepositoryPictureCount = len(MyProject.UniquePictureName_dict)
        MyProject.UniquePictureName_dict[uniquename]['Files'].append(MyProject.files_dict[row]['File:FileName'])

    # Collect information from each picture
    if MyProject.addSpeciesInformation(sp, RepositoryPictureCount) > 0: return   

    # Collect the JPGs, the best file per picture.
    # Also update counters while we are at it, for all pictures, also the ones without JPGs
    if MyProject.addPicturesToSpecies(sp, RepositoryPictureCount, SpeciesDebug) > 0: return

    # Write Picture CSV file
    picturefilename = os.path.join(MyProject.getMetaDirReview(), "data_pictures.csv")
    with pictures_out_csvfile as csvfile:
        logger.info(f'Write Picture data CSV file {picturefilename}.')
        # needed to get column in order
        ordered_fieldnames = OrderedDict((field, None) for field in PICTURE_FIELDS)                 
        writer = csv.DictWriter(csvfile, fieldnames=ordered_fieldnames, quoting=csv.QUOTE_MINIMAL, delimiter=';')
        writer.writeheader()
        for upn_record in list(MyProject.UniquePictureName_dict.values())[RepositoryPictureCount:]:            
            writer.writerow(upn_record)

    totaltime = time.time() - starttime                
    logger.info(f'{len(MyProject.UniquePictureName_dict)} lines written for pictures to {picturefilename}.')
    
    # also create summarizing species, when more counters are added later.
    # The trip's own list is rewritten in full (it is this run's authority), and
    # anything newly recognised is ALSO appended to the master's list, so a
    # species looked up here is never looked up again on another machine or in
    # the next trip. The master is required: losing that knowledge silently is
    # exactly what this replaced.
    trip_sc_csv = os.path.join(metadatadir, "active", "speciescollection.csv")
    learned = sorted(getattr(sc, "learned", set()))
    sc.updateSpeciesCollection(trip_sc_csv)
    if learned and not MyProject.isMasterRepository():
        master_sc_csv = os.path.join(MyProject.getMasterRepository(),
                                     "metadata", "active", "speciescollection.csv")
        if not os.path.exists(master_sc_csv):
            raise SVException(
                f"{len(learned)} newly recognised species cannot be saved: the master "
                f"species list is not reachable at {master_sc_csv}. Reconnect the "
                f"master and run this step again.")
        sc.appendSpeciesCollection(master_sc_csv, learned)
        logger.info(f"Added {len(learned)} newly recognised species to the master list.")
    sc.learned = set()
    sp.writeSpeciesInfo(os.path.join(MyProject.getMetaDirReview(), "species.csv"), False)
    
    MyProject.addMetricsToLog(sp, RepositoryPictureCount, 
                       os.path.join(MyProject.getMetaDirReview(), "stats report " + MyProject.ProjectName() + ".txt"))
    
    # logger.info(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')

    # totaltime = time.time() - starttime      
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="WritePictures")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = args.config
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    # define state machine and see if we can run this step
    statemachine = engine.statemachine.StateMachine(config.get("repository","BiotaDatabase"), config.get("project","PictureDirectory"))
    # call the actual code
    try:
        if not statemachine.stateOKtorun(parser.description):
            statemachine.explainAbort(parser.description)        
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
        statemachine.updateState(parser.description, False, e.args[0])
    else:
        logger.info(f"{parser.description} succeeded")
        statemachine.updateState(parser.description, True, "Succesfully run.")
    # Store state in statemachine file for this project
    statemachine.writeState()
