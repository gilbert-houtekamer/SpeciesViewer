import argparse
import configparser
import os
from pathlib import Path
import sys
import time
from datetime import datetime, date
from collections import OrderedDict
import csv
import engine.projectlib
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine
import engine.projectlib
import engine.websupport
import engine.specieslib
import engine.speciesclassification
from engine import COLUMN_NAMES, SELECTED_FIELDS
from engine.csv_columns import PICTURE_FIELDS
from engine.handling import read_file, read_pictures
from engine.specieslib import Level
from engine.websupport import getTemplateOutputString
import engine.exceptions
import engine.statemachine
import engine.triplib

logger = logging.getLogger("speciesviewer")
global starttime  # global

# For both languages
def makeIntroPages(MyProject):
    MyProject.setLanguage('nl')
    MyProject.makeIntro('intro','nl')
    MyProject.makeIntro('intro-link','nl')
    MyProject.setLanguage('en')
    MyProject.makeIntro('intro','en')
    MyProject.makeIntro('intro-link','en')
    return

def main(args, config):
    global starttime
    starttime = time.time()

    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)

    # get repository-wide sc and per project sp.
    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

    logger.info(f"sv5BuildWebsite: Add new species for project {MyProject.ProjectName()} on local copy on Windows")

    MyProject.readProject(getEarlierDays = True)
    thissteptime = time.time()     

    totaltime = time.time() - starttime
    logger.info(f'Create Website updates for project in {totaltime:.1f} seconds.')

    # A day can hold JPGs but no recognized species at all — then the species tree
    # has no 'biota' root and every page builder below would raise KeyError: 'biota'.
    # There is simply nothing to publish for such a day; say so and stop cleanly.
    if 'biota' not in MyProject.sp.species_dict:
        logger.info(f"{MyProject.ProjectName()}: no recognized species — no website pages built.")
        return engine.statemachine.nothing_to_do("no species this day — no website pages built.")

    MyProject.setLanguage('nl')
    MyProject.makeWebDirectories('biota', 'New')
    # same code, but no JPG files needed
    MyProject.setLanguage('en')
    MyProject.makeWebDirectories('biota', 'New') 
    # make all species page, sorted by order, no pictures 
    saveSkipJPGsave = MyProject.getSkipJPGs()
    MyProject.setSkipJPGs(False)
    MyProject.makeAllSpeciesPage('biota','nl')
    MyProject.setSkipJPGs(saveSkipJPGsave)    
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
