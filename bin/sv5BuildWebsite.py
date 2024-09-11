import argparse
import configparser
import os
from pathlib import Path
import sys
import time
from datetime import datetime, date
from collections import OrderedDict
import csv
import ghexif.projectlib
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from ghexif import files_dict, UniquePictureName_dict
import ghexif
import ghexif.projectlib
import ghexif.websupport
import ghexif.specieslib
import ghexif.speciesclassification
from ghexif import COLUMN_NAMES, SELECTED_FIELDS
from ghexif.csv_columns import PICTURE_FIELDS
from ghexif.specieslib import computeRating, computeRatingFile
from ghexif.handling import read_file, read_pictures
from ghexif.specieslib import Level
from ghexif.websupport import getTemplateOutputString
import ghexif.exceptions
import ghexif.statemachine

logger = logging.getLogger(__name__)
global starttime  # global

# For both languages
def makeIntroPages(MyProject):
    MyProject.makeIntro('intro-nl')
    MyProject.makeIntro('intro-en')
    # test
    MyProject.makeIntro('intro-link-nl')
    MyProject.makeIntro('intro-link-en')    
    return

def main(args, config):
    global starttime
    starttime = time.time()

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
  
    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

    logger.info(f"sv5BuildWebsite: Add new species for project {MyProject.ProjectName()} on local copy on Windows")

    MyProject.readProject()
    thissteptime = time.time()     

    totaltime = time.time() - starttime
    logger.info(f'Create Website updates for project in {totaltime:.1f} seconds.')
    MyProject.setLanguage('nl')
    MyProject.makeWebDirectories('biota', 'New')
    # same code, but no JPG files needed
    MyProject.setLanguage('en')
    MyProject.makeWebDirectories('biota', 'New') 
    # make all species page, sorted by order, no pictures copied
    MyProject.makeAllSpeciesPage('biota','nl')
    MyProject.makeAllSpeciesPage('biota','en')
    makeIntroPages(MyProject)

    totaltime = time.time() - thissteptime      
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="BuildWebsite")
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
    statemachine = ghexif.statemachine.StateMachine(config.get("repository","BiotaDatebase"), config.get("project","PictureDirectory"))
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
