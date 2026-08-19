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
import engine.speciesclassification
import engine.specieslib
import engine.bloglib
from engine.handling import read_file, read_pictures
import batch.sv1MakeReposity
import batch.sv2ReadDirExif
import batch.sv3WritePictures
import batch.sv4WriteBlog
import batch.sv5BuildWebsite
import batch.sv6Freeze
import batch.sv7WriteCommit
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")

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
        raise SVException(f'sv9RedoBlog can only work on trips.')
    elif not MyProject.online:
        raise SVException(f'sv9RedoBlog requires internet access, which is not available.')
    else:
        logger.info("Re-create Blog with latest code")
        # if trip, initialze master
        MyProject.initMasterAndTrip(config, inittrip=True)
        # get repository-wide sc and per project sp.
        sc = engine.speciesclassification.SpeciesClassification(MyProject)
        MyProject.registerSpeciesClassification(sc)
        sp = engine.specieslib.Species(MyProject)
        MyProject.registerSpecies(sp)

        # RedoBlog operates on committed trip-wide data. Unlike WriteBlog (which
        # reads the current day's review CSVs), we load only the active/committed
        # baseline so this works regardless of which day is currently selected
        # in the GUI.
        metadatadir = MyProject.getMetaDir()
        species_active = os.path.join(metadatadir, "active", "species.csv")
        if os.path.exists(species_active):
            sp.readSpeciesInfo(species_active, False)
        sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)
        read_file(MyProject, os.path.join(metadatadir, "active", "data_files.csv"))
        read_pictures(MyProject, os.path.join(metadatadir, "active", "data_pictures.csv"))

        # read blog_index.csv
        blog = engine.bloglib.Blog(MyProject)
        blog.read_BlogAdmin()

        # setup internet with cache objects
        blog.password = config.get("websettings","Sftppwd").strip("'")
        sftp = blog.openSFTP(blog.password)
        location_cache = os.path.join(MyProject.getRepositoryPath(), 'admin/FTPcache.json')
        sftp.openFTPCache(location_cache)

        # Recreate a blog when BOTH conditions hold:
        #   1) the day is fully committed (WriteCommit done in its statemachine)
        #   2) blog_admin.csv has saved details for that day (iterating blog.blog_dict)
        def is_committed(blogday):
            project = blogday[25:35]
            sm_ini = os.path.join(MyProject.getMetaDir(), 'review', project, 'statemachine.ini')
            if not os.path.exists(sm_ini):
                return False
            try:
                sm = engine.statemachine.StateMachine(
                    config.get("repository", "BiotaDatabase"),
                    MyProject.NewPictDir[:-10] + project)
                return bool(sm.Taskstate.get("WriteCommit", [False])[0])
            except Exception as e:
                logger.info(f'Cannot read state for "{project}", skipping: {e}')
                return False

        # Precompute committed blog days so the nav-sentence token can filter
        # Previous/Next links to committed days only.
        blog.committed_blogdays = {bd for bd in blog.blog_dict if is_committed(bd)}

        # Merge each committed day's per-day file/picture CSVs into files_dict
        # and UniquePictureName_dict. active/data_files.csv is sometimes
        # missing rows (Freeze/WriteCommit accumulation issue), but each day's
        # review/<day>/data_files.csv is written once and untouched, so it is
        # the authoritative source of file metadata we need for BestJPGs
        # lookups. read_file/read_pictures dedupe by key so this is safe.
        committed_days = {bd[25:35] for bd in blog.committed_blogdays}
        for day in sorted(committed_days):
            day_files = os.path.join(metadatadir, "review", day, "data_files.csv")
            day_pictures = os.path.join(metadatadir, "review", day, "data_pictures.csv")
            if os.path.exists(day_files):
                read_file(MyProject, day_files, silent=True)
            if os.path.exists(day_pictures):
                read_pictures(MyProject, day_pictures)

        for blogday in blog.blog_dict:
            if blogday not in blog.committed_blogdays:
                continue
            b = blog.blog_dict[blogday]
            logger.info(blogday)
            if 'blogjpg' not in b:
                b['blogjpg'] = ''
            blog.initBlog(b['title_nl'], b['title_en'], b['message_nl'], b['message_en'],
                          b['blogjpg'], b['SelectedGroup'], b['OnlyNewSpecies'], b['MinimumScore'])
            blog.blogday = blogday
            blog.BestJPGs = b['BestJPGs']
            MyProject.setSkipJPGs(True)
            try:
                blog.makeRunSummaryPost('biota', 'nl')
                blog.makeRunSummaryPost('biota', 'en')
                blog.uploadBlog(sftp, sftpPictures=False)
            except Exception as e:
                project = blogday[25:35]
                logger.warning(f'Blog recreation failed for "{project}": {e}')
        # remove blogs without JPGs
        # Create a new dictionary to hold valid entries
        cleaned_dict = {}

        for blogday, entry in blog.blog_dict.items():
            if entry["BestJPGs"] == []:
                logger.warning(f"Removed blog entry for {blogday} without JPG pictures.")
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
