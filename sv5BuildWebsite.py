import argparse
import configparser
import os
from pathlib import Path
import sys
import time
from datetime import datetime, date
from collections import OrderedDict
import csv
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import ghexif.projectlib
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

logger = logging.getLogger(__name__)
global starttime  # global

# For both languages
def makeIntroPages(MyProject):
    MyProject.makeIntro('intro-nl')
    MyProject.makeIntro('intro-en')
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

    MyProject.readProject()
    thissteptime = time.time()     

    totaltime = time.time() - starttime
    print(f'Create Website updates for project in {totaltime:.1f} seconds.')
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
    print(f'Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv5BuildWebsite.")
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = "svconfig.ini"
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"sv5BuildWebsite failed: {e}")
    else:
        logger.info("sv5BuildWebsite succeeded")
