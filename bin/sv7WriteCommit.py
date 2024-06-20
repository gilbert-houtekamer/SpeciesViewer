
# UNTESTED

import argparse
import configparser
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from ghexif import files_dict, UniquePictureName_dict
import ghexif
import ghexif.projectlib
from ghexif import COLUMN_NAMES, SELECTED_FIELDS, SVException
from ghexif.csv_columns import PICTURE_FIELDS
from ghexif.handling import read_file, read_pictures
from ghexif.specieslib import Level
import ghexif.specieslib
import ghexif.sftplib
logger = logging.getLogger(__name__)


def checkFrozenData(MyProject):
    frozendir = MyProject.ReviewFrozen()
        # sanity checks - at least present
    if not os.path.exists(frozendir+"frozen_data_files.csv") or \
        not os.path.exists(frozendir+"frozen_data_pictures.csv") or \
        not os.path.exists(frozendir+"species.csv") or \
        not os.path.exists(frozendir+"deletelist.csv"):
        raise SVException(f'Make sure the frozen files (data_files.csv, data_pictures.csv and select.csv) are present in {frozendir}. ')
    return True


def commitProjectFiles(MyProject):
    activedir = MyProject.getMetaDir() + "active/"
    activebackupdir = MyProject.getMetaDir() + "active/backup/"    
    frozendir = MyProject.ReviewFrozen()
    today = datetime.today().strftime('%Y-%m-%d')
         
    # save old versions 
    logger.info(f"Keep old files for each day in {activebackupdir}, oldest per day is kept.")
    if not os.path.exists(activebackupdir + today + "_data_files.csv"): 
        logger.info(f'rename active files with prefix "{today}" to directory "{activebackupdir}".')
        os.makedirs(activebackupdir, exist_ok=True)   
        shutil.copy(activedir+"data_files.csv", activebackupdir+ today + "_data_files.csv")
        shutil.copy(activedir+"data_pictures.csv", activebackupdir+ today + "_data_pictures.csv")
        shutil.copy(activedir+"species.csv", activebackupdir + today + "_species.csv")
        shutil.copy(activedir+"speciescollection.csv", activebackupdir + today + "_speciescollection.csv")        
    # Always keep last one per project
    shutil.copy(frozendir+"deletelist.csv", activebackupdir + today + "_"+ MyProject.ProjectName() + "_deletelist.csv")

    # now copy the frozen data
    logger.info(f"Copy frozen files in {frozendir} to {activedir}.")
    shutil.copy(frozendir+"frozen_data_files.csv", activedir+"data_files.csv")
    shutil.copy(frozendir+"frozen_data_pictures.csv", activedir+"data_pictures.csv")
    shutil.copy(frozendir+"species.csv", activedir+"species.csv")              
    return

def deleteCopiedFiles(deletelist):
    with open(deletelist, mode="r", newline="", encoding='utf-8') as csvfile: 
        linecount = 0
        cleaneddirectory = ""
        for filename in csvfile:
            filename = filename.replace("\n","").replace('"', '')
            if os.path.exists(filename): 
                linecount += 1
                os.remove(filename)
                if cleaneddirectory == "":
                    cleaneddirectory = os.path.dirname(filename)
        filesleft = sum(len(files) for _, _, files in os.walk(cleaneddirectory))
        logger.info(f'{linecount} files deleted from {cleaneddirectory}, {filesleft} left without species information.')
    return


def putRecursiveTreeWeb(MyProject):
    password = config.get("websettings","Sftppwd").strip("'")
    sftp = ghexif.sftplib.Sftp(
        hostname='houtfp.ssh.transip.me',
        username='houtekamercom',
        password=password,
    )
    # Connect to SFTP
    sftp.connect()
    fromdisk = MyProject.ReviewFrozen()
    toweb = sftp.remotePrefix() + MyProject.getBiotaWebRoot()
    sftp.put_r_portable(fromdisk + 'biota/', toweb + 'biota/', preserve_mtime=True)

    fromintro = MyProject.getBiotaDir()
    tointro = sftp.remotePrefix() + MyProject.getBiotaWebRoot()
    sftp.connection.put(fromintro + 'intro-nl.html', tointro + 'intro-nl.html', preserve_mtime=True)
    sftp.connection.put(fromintro + 'intro-en.html', tointro + 'intro-en.html', preserve_mtime=True)
    sftp.connection.put(fromintro + 'summaries/all biota species-nl.html', tointro + 'summaries/all biota species-nl.html', preserve_mtime=True)
    sftp.connection.put(fromintro + 'summaries/all biota species-en.html', tointro + 'summaries/all biota species-en.html', preserve_mtime=True)       
    sftp.disconnect()
    return

def putRecursiveTreeDisk(MyProject):
    fromlastproject  = MyProject.ReviewFrozen()
    tomainrepository = MyProject.getRepositoryPath()
    shutil.copytree(fromlastproject + 'biota/', tomainrepository+ 'biota/', dirs_exist_ok=True)
    return

def main(args, config):
    global starttime
    starttime = time.time()

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
    if not MyProject.online:
        print(f'Internet not online, you cannot commit without an internet connection.')
        sys.exit(16)
    
    if checkFrozenData(MyProject):
    # move "approved" files to the active directory
        commitProjectFiles(MyProject)
        
        # update the website
        putRecursiveTreeWeb(MyProject)
        putRecursiveTreeDisk(MyProject)

        # TODO rewrite the Blog with active links to species

        # remove files that are copied
        #deleteCopiedFiles(MyProject.ReviewFrozen()+"deletelist.csv")

    totaltime = time.time() - starttime      
    logger.info(f'sv7WriteCommit: Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv7WriteCommit.")
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = "svconfig.ini"
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"sv7WriteCommit failed: {e}")
    else:
        logger.info("sv7WriteCommit succeeded")
