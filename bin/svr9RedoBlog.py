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

logger = logging.getLogger("speciesviewer")

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
        raise SVException(f'sv9RedoBlog can only work on trips.')
    elif not MyProject.online:
        raise SVException(f'sv9RedoBlog requires internet access, which is not available.')
    else:
        logger.info("Re-create Blog with latest code")
        # if trip, initialze master
        MyProject.initMasterAndTrip(config, inittrip=True)
        # get repository-wide sc and per project sp.
        sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
        MyProject.registerSpeciesClassification(sc)
        sp = ghexif.specieslib.Species(MyProject)
        MyProject.registerSpecies(sp)
        # read picture meta data
        files, pictures, from_time, last_time = MyProject.readProject()

        # read blog_index.csv
        blog = ghexif.bloglib.Blog(MyProject)
        blog.read_BlogAdmin()

        # setup internet with cache objects
        blog.password = config.get("websettings","Sftppwd").strip("'")
        sftp = blog.openSFTP(blog.password)
        location_cache = os.path.join(MyProject.getRepositoryPath(), 'admin/FTPcache.json')
        sftp.openFTPCache(location_cache)

        # run through the blogs
        for blogday in blog.blog_dict:
            logger.info(blogday)
            # copy the current blogday
            b = blog.blog_dict[blogday]
            if 'blogjpg'  not in b: b['blogjpg'] = ''
            blog.initBlog(b['title_nl'], b['title_en'], b['message_nl'], b['message_en'], 
                          b['blogjpg'], b['SelectedGroup'], b['OnlyNewSpecies'], b['MinimumScore'])
            # also copy best JPSs to avoid new changes to best JPG
            project = blogday[25:35]
            blog.blogday = blogday
            metadir = os.path.join(MyProject.getMetaDir(), 'review', project)
            try:
                if os.path.exists(os.path.join(metadir, 'statemachine.ini')):
                    statemachine = ghexif.statemachine.StateMachine(config.get("repository","BiotaDatabase"), 
                                                                MyProject.NewPictDir[:-10] + project)
                # make sure all steps are made such that all jpg's files are committed
                    if statemachine.Taskstate["WriteCommit"][0]:
                        blog.BestJPGs = b['BestJPGs']
                        MyProject.setSkipJPGs(True)
                        blog.makeRunSummaryPost('biota', 'nl')
                        blog.makeRunSummaryPost('biota', 'en')
                        blog.uploadBlog(sftp, sftpPictures=False)
                    else:
                        logger.warning(f'Not all data is commmitted to the repository for project "{project}"')
                else: # no statemachine.ini at all, go for it anyhow
                    blog.BestJPGs = b['BestJPGs']
                    if blog.BestJPGs != []:
                        MyProject.setSkipJPGs(True)
                        blog.makeRunSummaryPost('biota', 'nl')
                        blog.makeRunSummaryPost('biota', 'en')
                        blog.uploadBlog(sftp, sftpPictures=False)
            except:
                logger.warning(f'None of the steps are executed for "{project}"')
        # remove blogs without JPGs
        # Create a new dictionary to hold valid entries
        cleaned_dict = {}

        for blogday, entry in blog.blog_dict.items():
            if entry["BestJPGs"] == []:
                logger.info(f"Removed blog entry for {blogday} without JPG pictures.")
            else:
                cleaned_dict[blogday] = entry

        # Replace the original dictionary with the cleaned one
        blog.blog_dict = cleaned_dict

        # all updated, also update index
        blog.updateIndexPage()
        blog.uploadBlogIndex(sftp)
        # Disconnect from SFTP
        sftp.closeFTPCache(location_cache)
        sftp.disconnect()   
    

    
    totaltime = time.time() - starttime      
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv9RedoBlog")
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
