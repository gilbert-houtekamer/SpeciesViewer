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

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine
import engine.projectlib
import engine.exceptions
import engine.statemachine

logger = logging.getLogger("speciesviewer")

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    return config

def getAllProjectStatus(directory):
    days_dict = {}
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.ini'):
                file_path = os.path.join(root, file)
                parseddir = root.split('\\')
                name = parseddir[1]
                if name == "": name = parseddir[0]
                statemachine = engine.statemachine.StateMachine(config.get("repository","BiotaDatabase"), name) #, readState=False)
                days_dict[name] = statemachine.Taskstate.copy()

    # make summary report
    print(f"Summary of projects and progress for each report")     
    print(f"{'Project':15}")   
    for statemachine in days_dict:
        line = f"{statemachine:15}"
        for task in days_dict[statemachine]:
            if task != "MakeRepository":
                if days_dict[statemachine][task][0]:
                    line += f" {task:15}"
                else:
                    line += f" {'':15}"
        print(line)
    return days_dict


def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))
    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
    # Collect states
    directory_to_scan = os.path.join(MyProject.getMetaDir(), 'review') # 'C:/laptop/validate/metadata/review'
    days_dict = getAllProjectStatus(directory_to_scan)

    # # sample code to determine what is needed, up to level 2024-06-29
    # current_day = '2024-06-29'
    # for statemachine in days_dict:                      # projects
    #     if statemachine >= current_day: 
    #         break
    #     if not days_dict[statemachine]['WriteCommit'][0]:  # this project is not
    #         # read this older project
    #         print(f"Reading data from {statemachine}.") 

        
    # print(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv3StatusProjects")
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
