
#!/usr/bin/env python3

# svr7DeleteFiles.py: Delete files that are copied to target database.
# This script searches through the metadata directories for a project, looking for 'deletelist.csv' files. 
# For each 'deletelist.csv' found, it reads the list of files to delete and removes them from the filesystem. 
# It also logs the number of files deleted and how many are left without species information. 
# This is a one-off script to clean up files that were copied to the target database but need to be 
# removed after a merge in the master. In the GUI, this function is not directly available, 
# but it can be used as part of the maintenance process after merging trips in the master database.

# BE careful: use this AFTER you created both the trip but also you did the merge in the master.
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
    reporoot = os.path.abspath(os.path.join(scriptpath, os.pardir, os.pardir))
    os.chdir(reporoot)
    sys.path.append(os.path.join(reporoot, 'speciesviewer'))

import engine
import engine.projectlib
import engine.exceptions
import engine.triplib
logger = logging.getLogger("speciesviewer")

def deleteCopiedFiles(deletelist):
    deleted = 0
    skipped_no_master = 0
    already_gone = 0
    cleaneddirectory = ""

    with open(deletelist, mode="r", newline="", encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile, delimiter=';')
        for row in reader:
            source = row.get('source_path', '').strip()
            target = row.get('target_path', '').strip()
            if not source:
                continue
            cleaneddirectory = os.path.dirname(source)
            master_has_file = os.path.exists(target) if target else False

            if master_has_file:
                if os.path.exists(source):
                    os.remove(source)
                    deleted += 1
                else:
                    already_gone += 1
            else:
                if os.path.exists(source):
                    logger.warning(f'Skipping {os.path.basename(source)}: not found in master at {target}')
                    skipped_no_master += 1
                else:
                    logger.info(f'Already gone and not in master: {os.path.basename(source)}')
                    already_gone += 1

    filesleft = sum(len(files) for _, _, files in os.walk(cleaneddirectory)) if cleaneddirectory else 0
    logger.info(
        f'{deleted} files deleted from {cleaneddirectory}, '
        f'{skipped_no_master} skipped (not in master), '
        f'{already_gone} already absent, '
        f'{filesleft} files remaining.'
    )
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
    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
 
    # Search through all metadate directories
    directory_to_scan = os.path.join(MyProject.getMetaDir(), 'review') # 'C:/laptop/validate/metadata/review'
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
        "--dir", type=str, default=os.path.join(reporoot, "data"), help="foto directory"
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
