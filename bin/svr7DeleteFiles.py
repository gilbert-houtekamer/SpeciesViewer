# module to delete files that are copied to targetdatabase 
# use this with a project file for the trip, 
# AFTER you created both the trip but also you did the merge in the master.
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
logger = logging.getLogger(__name__)

def deleteCopiedFiles(deletelist):
    with open(deletelist, mode="r", newline="", encoding='utf-8') as csvfile: 
        linecount = 0
        cleaneddirectory = ""
        for filename in csvfile:
            filename = filename.replace("\n","").replace('"', '')
            cleaneddirectory = os.path.dirname(filename)            
            if os.path.exists(filename): 
                linecount += 1
                os.remove(filename)
        filesleft = sum(len(files) for _, _, files in os.walk(cleaneddirectory))
        logger.info(f'{linecount} files deleted from {cleaneddirectory}, {filesleft} left without species information.')
    return

def delete_files(directory_to_scan, file_deletelist):
    # scan through days
    for root, dirs, files in os.walk(directory_to_scan):
        for file in files:
            if file == file_deletelist:
                file_path = os.path.join(root, file)
                deleteCopiedFiles(file_path)
    return

def main(args, config):
    global starttime
    starttime = time.time()
    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
 
    # Search through all metadate directories
    directory_to_scan = MyProject.getMetaDir() + 'review' # 'C:/laptop/validate/metadata/review'
    delete_files(directory_to_scan, 'deletelist.csv')
    return

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv7DeleteFiles")
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
