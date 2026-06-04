
# UNTESTED

import argparse
import configparser
import os
import shutil
import sys
import time
import csv
from datetime import datetime
from pathlib import Path
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine
import engine.projectlib
from engine import COLUMN_NAMES, SELECTED_FIELDS, SVException
from engine.csv_columns import PICTURE_FIELDS
from engine.handling import read_file, read_pictures
from engine.specieslib import Level
from engine.websupport import jpgResizeCopy, getSmallJPG
import engine.specieslib
import engine.sftplib
import engine.exceptions
import engine.statemachine
import engine.triplib

logger = logging.getLogger("speciesviewer")


def checkFrozenData(MyProject):
    frozendir = MyProject.ReviewFrozen()
        # sanity checks - at least present
    if not os.path.exists(os.path.join(frozendir, "frozen_data_files.csv")) or \
        not os.path.exists(os.path.join(frozendir, "frozen_data_pictures.csv")) or \
        not os.path.exists(os.path.join(frozendir, "species.csv")) or \
        not os.path.exists(os.path.join(frozendir, "deletelist.csv")):
        raise SVException(f'Make sure the frozen files (data_files.csv, data_pictures.csv and select.csv) are present in {frozendir}. ')
    return True


def commitProjectFiles(MyProject):
    activedir = os.path.join(MyProject.getMetaDir(), "active")
    activebackupdir = os.path.join(activedir, "backup")
    frozendir = MyProject.ReviewFrozen()
    today = datetime.today().strftime('%Y-%m-%d')

    # save old versions
    logger.info(f"Keep old files for each day in {activebackupdir}, oldest per day is kept.")
    if not os.path.exists(os.path.join(activebackupdir, today + "_data_files.csv")):
        logger.info(f'rename active files with prefix "{today}" to directory "{activebackupdir}".')
        os.makedirs(activebackupdir, exist_ok=True)
        shutil.copy(os.path.join(activedir, "data_files.csv"),        os.path.join(activebackupdir, today + "_data_files.csv"))
        shutil.copy(os.path.join(activedir, "data_pictures.csv"),     os.path.join(activebackupdir, today + "_data_pictures.csv"))
        shutil.copy(os.path.join(activedir, "species.csv"),           os.path.join(activebackupdir, today + "_species.csv"))
        shutil.copy(os.path.join(activedir, "speciescollection.csv"), os.path.join(activebackupdir, today + "_speciescollection.csv"))
    # Always keep last one per project
    shutil.copy(os.path.join(frozendir, "deletelist.csv"),
                os.path.join(activebackupdir, today + "_" + MyProject.ProjectName() + "_deletelist.csv"))

    # now copy the frozen data
    logger.info(f"Copy frozen files in {frozendir} to {activedir}.")
    shutil.copy(os.path.join(frozendir, "frozen_data_files.csv"),    os.path.join(activedir, "data_files.csv"))
    shutil.copy(os.path.join(frozendir, "frozen_data_pictures.csv"), os.path.join(activedir, "data_pictures.csv"))
    shutil.copy(os.path.join(frozendir, "species.csv"),              os.path.join(activedir, "species.csv"))
    return

def putRecursiveTreeWeb(MyProject, config):
    password = config.get("websettings","Sftppwd").strip("'")
    sftp = engine.sftplib.Sftp(
        hostname='houtfp.ssh.transip.me',
        username='houtekamercom',
        password=password,
    )
    location_cache = os.path.join(MyProject.getRepositoryPath(), 'admin/FTPcache.json')
    sftp.openFTPCache(location_cache)
    # Connect to SFTP
    sftp.connect()
    fromdisk = MyProject.ReviewFrozen()
    toweb = sftp.remotePrefix() + MyProject.getBiotaWebRoot()
    sftp.put_r_portable(os.path.join(fromdisk, 'biota/'), toweb + 'biota/', preserve_mtime=True)

    fromintro = MyProject.getBiotaDir()
    tointro = sftp.remotePrefix() + MyProject.getBiotaWebRoot()
    # sftp.connection.put(fromintro + 'intro-nl.html', tointro + 'intro-nl.html', preserve_mtime=True)
    # sftp.connection.put(fromintro + 'intro-en.html', tointro + 'intro-en.html', preserve_mtime=True)
    # sftp.connection.put(fromintro + 'summaries/all biota species-nl.html', tointro + 'summaries/all biota species-nl.html', preserve_mtime=True)
    # sftp.connection.put(fromintro + 'summaries/all biota species-en.html', tointro + 'summaries/all biota species-en.html', preserve_mtime=True)       

    sftp.cachedUpload(os.path.join(fromintro, 'intro-nl.html'),                          tointro + 'intro-nl.html', preserve_mtime=True)
    sftp.cachedUpload(os.path.join(fromintro, 'intro-en.html'),                          tointro + 'intro-en.html', preserve_mtime=True)
    sftp.cachedUpload(os.path.join(fromintro, 'summaries', 'all biota species-nl.html'), tointro + 'summaries/all biota species-nl.html', preserve_mtime=True)
    sftp.cachedUpload(os.path.join(fromintro, 'summaries', 'all biota species-en.html'), tointro + 'summaries/all biota species-en.html', preserve_mtime=True)
    # send small pictures voor all species
    with open(os.path.join(MyProject.ReviewFrozen(), "allspecies.csv"), mode="r", newline="", encoding='utf-8') as csvfile:
        csvreader = csv.reader(csvfile)
        for row in csvreader:
            source = row[0].replace('"', '')
            ftptarget = getSmallJPG(sftp.remotePrefix() + row[1].replace('"', '').lstrip())
            jpgResizeCopy(source)
#           sftp.connection.put(getSmallJPG(source), ftptarget)
            if not sftp.cachedUpload(getSmallJPG(source), ftptarget):
                logger.info(f"Small image for All Species report: {getSmallJPG(source)}")

    sftp.closeFTPCache(location_cache)    
    sftp.disconnect()


    return

def putRecursiveTreeDisk(MyProject):
    fromlastproject  = MyProject.ReviewFrozen()
    tomainrepository = MyProject.getRepositoryPath()
    shutil.copytree(os.path.join(fromlastproject, 'biota/'), os.path.join(tomainrepository, 'biota'), dirs_exist_ok=True)
    return

def main(args, config):
    global starttime
    starttime = time.time()

    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)
    
    logger.info(f"sv7WriteCommit: Commit project {MyProject.ProjectName()} on repository.")
    if not MyProject.online:
        print(f'Internet not online, you cannot commit without an internet connection.')
        sys.exit(16)
    
    if checkFrozenData(MyProject):
    # move "approved" files to the active directory
        commitProjectFiles(MyProject)
        
        # update the website
        putRecursiveTreeWeb(MyProject, config)
        putRecursiveTreeDisk(MyProject)
        # TODO rewrite the Blog with active links to species

        # remove files that are copied
        #deleteCopiedFiles(MyProject.ReviewFrozen()+"deletelist.csv")

    totaltime = time.time() - starttime      
    logger.info(f'sv7WriteCommit: Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="WriteCommit")
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
    # init the logging subsystem
    logging.basicConfig(level=logging.INFO)    
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
