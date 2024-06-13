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

global config

INI_LOCATION = "svconfig.ini"

# pass ini file to module
parser = argparse.ArgumentParser(description="Web site Demo.")
parser.add_argument(
    "--config",
    type=str,
    default=os.environ.get("GHCONFIG", "/etc/ghconfig.ini"),
    help="Gilbert Houtekamer configuration file",
)
args = parser.parse_args()
scriptpath = os.path.dirname(__file__)
modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
os.chdir(modpath)
sys.path.append(modpath)

global starttime  # global

# start garbage lines, should not be here?

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

# For both languages
def makeIntroPages(MyProject):
    MyProject.makeIntro('intro-nl')
    MyProject.makeIntro('intro-en')
    return

def main(args):
    global starttime, config
    # read ini file
    config = configparser.ConfigParser()
    #config.read(args.config)
    config.read(INI_LOCATION)    
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
     main(parser.parse_args())