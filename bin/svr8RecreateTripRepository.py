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

import ghexif
from ghexif import SVException
import ghexif.projectlib
import ghexif.exceptions
import ghexif.statemachine
import bin.sv1MakeReposity
import bin.sv2ReadDirExif
import bin.sv3WritePictures
import bin.sv4WriteBlog
import bin.sv5BuildWebsite
import bin.sv6Freeze
import bin.sv7WriteCommit

logger = logging.getLogger(__name__)

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    return config

# def getConfigFileLines(file):
#     configfile = open(file, "r")
#     soort_template_lines = configfile.readlines()
#     configfile.close()
#     return soort_template_lines

def RecreateProject(args, config, project):
    logger.info(f"Recreate project {project}")
    try:
        step = 'bin.sv2ReadDirExif.main'
        bin.sv2ReadDirExif.main(args, config)
        logger.info('=====================================================')
        step = 'bin.sv3WritePictures.main'
        bin.sv3WritePictures.main(args, config)
        logger.info('=====================================================')
        step = 'bin.sv4WriteBlog.main'
        bin.sv4WriteBlog.main(args, config)
        logger.info('=====================================================')
        step = 'bin.sv5BuildWebsite.main'
        bin.sv5BuildWebsite.main(args, config)
        logger.info('=====================================================')
        step = 'bin.sv6Freeze.main'
        logger.info('=====================================================')
        bin.sv6Freeze.main(args, config)
        step = 'bin.sv7WriteCommit.main'
        logger.info('=====================================================')
        bin.sv7WriteCommit.main(args, config) 
    except Exception as e:
        logger.warning(f"{parser.description} {step} failed: {e}")
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
    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatabase"), 
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
        # delete metadata 
        repo = MyProject.getRepositoryPath()
        reviewdirs = os.path.join(repo, 'metadata/review/')
        # file system loops 
        trycount = 1
        while trycount < 10:
            try:
                for item in os.listdir(reviewdirs): 
                    item_path = os.path.join(reviewdirs, item) 
                    if os.path.isdir(item_path): shutil.rmtree(item_path)
                break
            except:
                trycount += 1
       
        # clear admin for added data/pictures
        shutil.copy(os.path.join(os.path.join(os.getcwd(),"dist"), 'installation files/empty_data_files.csv'), 
                os.path.join(repo, 'metadata/active/data_files.csv')) 
        shutil.copy(os.path.join(os.path.join(os.getcwd(),"dist"), 'installation files/empty_data_pictures.csv'), 
                os.path.join(repo, 'metadata/active/data_pictures.csv')) 
        shutil.copy(os.path.join(os.path.join(os.getcwd(),"dist"), 'installation files/empty_species.csv'), 
                os.path.join(repo, 'metadata/active/species.csv'))           
        # Collect states
        directory_to_scan = Path(MyProject.getProjectPath()).parent       # 'C:/laptop/validate/metadata/review'
        RecreateProjects(args, config, directory_to_scan)

        # print(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
        totaltime = time.time() - starttime      
        logger.info(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv8RecreatedTripRepository")
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
