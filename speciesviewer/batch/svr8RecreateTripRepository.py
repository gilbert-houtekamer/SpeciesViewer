#!/usr/bin/env python3

# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import re
import csv
import os
import sys
import configparser
import time
import logging
import argparse
from pathlib import Path
import pathlib
import shutil
global modpath

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine
from engine import SVException
import engine.projectlib
import engine.exceptions
import engine.statemachine
from engine.tripresetlib import clear_review_directories, reset_active_data
import batch.sv1MakeReposity
import batch.sv2ReadDirExif
import batch.sv3WritePictures
import batch.sv4WriteBlog
import batch.sv5BuildWebsite
import batch.sv6Freeze
import batch.sv7WriteCommit
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    return config

# def getConfigFileLines(file):
#     configfile = open(file, "r")
#     soort_template_lines = configfile.readlines()
#     configfile.close()
#     return soort_template_lines

def RecreateProject(args, config, project):
    logger.info(f"Recreate project {project}")      # project format is 2024-12-20
    if project <= '2024-04-xx': return
    try:
        step = 'batch.sv2ReadDirExif.main'
        batch.sv2ReadDirExif.main(args, config)
        logger.info(f'====={project}=====================================================')
        step = 'batch.sv3WritePictures.main'
        batch.sv3WritePictures.main(args, config)
        logger.info(f'====={project}=====================================================')
        step = 'batch.sv4WriteBlog.main'
        batch.sv4WriteBlog.main(args, config)
        logger.info(f'====={project}=====================================================')
        step = 'batch.sv5BuildWebsite.main'
        batch.sv5BuildWebsite.main(args, config)
        logger.info(f'====={project}=====================================================')
        step = 'batch.sv6Freeze.main'
        logger.info(f'====={project}=====================================================')
        batch.sv6Freeze.main(args, config)
        step = 'batch.sv7WriteCommit.main'
        logger.info(f'====={project}=====================================================')
        batch.sv7WriteCommit.main(args, config) 
        logger.info(f'====={project}=====================================================') 
    except Exception as e:
        logger.warning(f"sv8RecreatedTripRepository: {project} {step} failed: {e}")
        exit
    return

def RecreateProjects(args, config, directory):
    for root, dirs, files in os.walk(directory):
        blogPath = pathlib.PurePath(os.path.join(root, 'blog.ini'))
        if os.path.exists(blogPath):
            config['project']['PictureDirectory'] = str(blogPath.parent)
            RecreateProject(args, config, blogPath.parent.name)            
    return

def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))
    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
    # make sure that this is a trip, not the master 
    try:
        MyProject.MasterRepository = config.get("master","MasterBiotaDatabase")
    except:
        MyProject.MasterRepository = ""
    if MyProject.MasterRepository == "":
        raise SVException(f'sv8RecreatedTripRepository can only work on trips.')
    else:
        # delete metadata and reset active data
        repo = MyProject.getRepositoryPath()
        clear_review_directories(repo)
        reset_active_data(repo)           
        # Collect states
        directory_to_scan = Path(MyProject.getProjectPath()).parent       # 'C:/laptop/validate/metadata/review'
        RecreateProjects(args, config, directory_to_scan)

        # create statistics
        # svr2
        # svr4

        # print(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
        totaltime = time.time() - starttime      
        logger.info(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv8RecreatedTripRepository")
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
    # A file log for a command-line run only. It used to be attached at import
    # time, and the GUI imports this module at start-up — so every log line of
    # every session also went to a file in the working directory.
    from utils.logger_support import log_file
    fh = logging.FileHandler(log_file("sysprint-recreatetrip.txt"), encoding="utf-8")
    fh.setLevel(logging.INFO)
    logger.addHandler(fh)
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
