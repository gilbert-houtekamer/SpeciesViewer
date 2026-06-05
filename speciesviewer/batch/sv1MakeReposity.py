#!/usr/bin/env python3

# create new project
#
import os
import sys
import shutil
import configparser
import logging
import argparse
from pathlib import Path
from datetime import datetime, date

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine.sftplib
import engine.projectlib
import engine.triplib
import engine.bloglib

logger = logging.getLogger("speciesviewer")

# reading trip.ini for repositories that are about a trip
def getUTF8(tripconfig, group, ansistring):
    workstr = tripconfig.get(group, ansistring)
    try:
        workstr = workstr.encode('latin1').decode('utf-8')          # we should validate this upon input
        workstr = workstr.replace("<female>", "♀")
        workstr = workstr.replace("<male>", "♂")
    except:
        pass  # too bad, we tried
    return workstr

def main(args, config):
    MY_GHEXIF_DIR = str(Path(__file__).resolve().parent.parent / "dist")
    NEW_DB_TREE = config.get("repository","BiotaDatabase")    # "e:/test3/SpeciesViewDB/"
    WEB_TREE = config.get("repository","BiotaWebRoot")

    logger.info("sv1MakeReposity: define repository and disk and Web.")
    os.makedirs(NEW_DB_TREE, exist_ok=True)               # root for data, metadata and web contents
    #
    # root for data repository with all pictures (will be very big)
    # many directories
    os.makedirs(os.path.join(NEW_DB_TREE,"biota"), exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE,"admin"), exist_ok=True)
    # the metadata describes the data in the repository, and has review subdirectories as specified below
    os.makedirs(os.path.join(NEW_DB_TREE,"metadata"), exist_ok=True)
    # files are expected to use these names:
    # data_files.csv
    # data_pictures.csv
    # species.csv
    # more 'in review' directories is likely very desirable, sub-directories by day or by last qualifier directory
    os.makedirs(os.path.join(NEW_DB_TREE,"metadata/active"), exist_ok=True)  # what is in the big just repository
    os.makedirs(os.path.join(NEW_DB_TREE,"metadata/review"), exist_ok=True)  # the data that is beging reviewed

    initspeciesdir = os.path.join(NEW_DB_TREE,"metadata/initialize")
    os.makedirs(initspeciesdir, exist_ok=True)
    shutil.copy(os.path.join(MY_GHEXIF_DIR, 'installation files/empty_species.csv'), 
                os.path.join(initspeciesdir, 'empty_species.csv'))
    shutil.copy(os.path.join(MY_GHEXIF_DIR, 'installation files/empty_species.csv'), 
                os.path.join(NEW_DB_TREE, 'metadata/active/species.csv'))
    shutil.copy(os.path.join(MY_GHEXIF_DIR, 'installation files/initial_speciescollection.csv'), 
                os.path.join(initspeciesdir, 'initial_speciescollection.csv'))
    shutil.copy(os.path.join(MY_GHEXIF_DIR, 'installation files/initial_speciescollection.csv'), 
                os.path.join(NEW_DB_TREE, 'metadata/active/speciescollection.csv'))
    shutil.copy(os.path.join(MY_GHEXIF_DIR, 'installation files/empty_data_files.csv'), 
                os.path.join(initspeciesdir, 'empty_data_files.csv'))
    shutil.copy(os.path.join(MY_GHEXIF_DIR, 'installation files/empty_data_files.csv'), 
                os.path.join(NEW_DB_TREE, 'metadata/active/data_files.csv'))
    shutil.copy(os.path.join(MY_GHEXIF_DIR, 'installation files/empty_data_pictures.csv'), 
                os.path.join(initspeciesdir, 'empty_data_pictures.csv'))
    shutil.copy(os.path.join(MY_GHEXIF_DIR, 'installation files/empty_data_pictures.csv'), 
                os.path.join(NEW_DB_TREE, 'metadata/active/data_pictures.csv'))

    # the web site is first create locally
    # copy of the web site and directory for new files just frozen
    os.makedirs(os.path.join(NEW_DB_TREE,"web"), exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE,"web/biota"), exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE,"web/biotablog"), exist_ok=True)    # mostly staging

    initwebfiles_source = os.path.join(MY_GHEXIF_DIR,"includes web")
    initwebfiles_target = os.path.join(NEW_DB_TREE,"web/includes web")
    os.makedirs(os.path.join(NEW_DB_TREE,"web/includes web"), exist_ok=True)
    for filename in os.listdir(initwebfiles_source):
        source = os.path.join(initwebfiles_source, filename)
        target = os.path.join(initwebfiles_target, filename)
        shutil.copy(source, target)

    os.makedirs(os.path.join(NEW_DB_TREE,"web/summaries"), exist_ok=True)

    # create missing link pages relative to intro page
    # first create the "master", but that needs a "real" environemnt with MyProject
    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"),
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)
    if not MyProject.isMasterRepository():
        shutil.copy(os.path.join(MyProject.getMasterRepository(), 'metadata/active/speciescollection.csv'), 
                os.path.join(NEW_DB_TREE, 'metadata/active/speciescollection.csv'))
        
    blog = engine.bloglib.Blog(MyProject)   
    blog.updateIndexPage()
    # create intro page, already in good location
    MyProject.setLanguage('nl')
    MyProject.makeIntro('intro','nl')
    MyProject.makeIntro('intro-link','nl')
    MyProject.setLanguage('en')
    MyProject.makeIntro('intro','en')
    MyProject.makeIntro('intro-link','en')

    # now add the files
    def copyMissingLinkPages(directory, exist_ok=True):
        filename = os.path.basename(directory)
        os.makedirs(os.path.join(NEW_DB_TREE,"web",directory), exist_ok=True)
        shutil.copy(os.path.join(NEW_DB_TREE,"web/intro-link-nl.html"), 
                    os.path.join(NEW_DB_TREE, "web", directory, filename + "-nl.html"))
        shutil.copy(os.path.join(NEW_DB_TREE,"web/intro-link-en.html"), 
                    os.path.join(NEW_DB_TREE, "web",  directory, filename + "-en.html"))
        return
    # copy files - directories first
    copyMissingLinkPages("biota/animalia", exist_ok=True)
    copyMissingLinkPages("biota/animalia/chordata/mammalia", exist_ok=True)
    copyMissingLinkPages("biota/animalia/chordata/aves", exist_ok=True)
    copyMissingLinkPages("biota/animalia/arthropoda/insecta", exist_ok=True)
    copyMissingLinkPages("biota/animalia/arthropoda/insecta/003 odonata", exist_ok=True)
    copyMissingLinkPages("biota/animalia/arthropoda/insecta/022 lepidoptera", exist_ok=True)
    copyMissingLinkPages("biota/plantae", exist_ok=True)
    copyMissingLinkPages("biota/fungi", exist_ok=True)
    copyMissingLinkPages("summaries/all biota species", exist_ok=True)    

    if WEB_TREE != "":
        password = config.get("websettings","Sftppwd").strip("'")
        sftp = engine.sftplib.Sftp(
            hostname='houtfp.ssh.transip.me',
            username='houtekamercom',
            password=password,
        )
        # Connect to SFTP
        sftp.connect()
        location_cache = os.path.join(MyProject.getRepositoryPath(), 'admin/FTPcache.json')
        sftp.openFTPCache(location_cache)
        # 
        def createWebTree(path):
            directories = path.strip('/').split('/')
            existingpath = sftp.remotePrefix()

            for directory in directories:
                try:
                    sftp.safeMkdir(existingpath + '/' + directory)
                except Exception as e:
                    print(f"Directory {directory} already exists or cannot be created: {e}")
                existingpath += '/' + directory
            return existingpath
        
        createdpath = createWebTree(WEB_TREE)
        
        sftp.safeMkdir(createdpath + '/biota')
        sftp.safeMkdir(createdpath + '/summaries')
        sftp.safeMkdir(createdpath + '/biotablog')
        sftp.safeMkdir(createdpath + '/admin')
        sftp.safeMkdir(createdpath + '/includes web')
        # copy intro-nl.html and uk version
        sftp.upload(os.path.join(NEW_DB_TREE, "web/intro-nl.html"), createdpath + "/intro-nl.html")
        sftp.upload(os.path.join(NEW_DB_TREE, "web/intro-en.html"), createdpath + "/intro-en.html") 
        # summary species
        sftp.upload(os.path.join(NEW_DB_TREE, "web/intro-link-nl.html"), createdpath + "/summaries/all biota species-nl.html")
        sftp.upload(os.path.join(NEW_DB_TREE, "web/intro-link-en.html"), createdpath + "/summaries/all biota species-en.html")                    
        # install used files
        fromdir = os.path.join(NEW_DB_TREE, "web/includes web/")
        towebdir = createdpath + '/includes web/'
        sftp.put_r_portable(fromdir, towebdir, preserve_mtime=True)
        # dead links
        fromdir = os.path.join(NEW_DB_TREE, "web/biota/")
        towebdir = createdpath + '/biota/'
        sftp.put_r_portable(fromdir, towebdir, preserve_mtime=True)
        # project info
        if not MyProject.isMasterRepository():
            tripname = Path(MyProject.getBiotaWebRoot()).name
            blog.uploadBlogIndex(sftp)          
            try:
                trip = MyProject.Trip.getTrip(tripname)
                sftp.upload(os.path.join(NEW_DB_TREE, trip['trip_banner']), createdpath + '/' + trip['trip_banner'])
                sftp.upload(os.path.join(NEW_DB_TREE, trip['trip_picture']), createdpath + '/' + trip['trip_picture'])
            except:
                logger.warning(f"sv1MakeReposity: you did not customize trip.ini and/or not added banner (2000x270) or picture (600x450).")
                logger.warning(f"Be sure to also FTP them to {createdpath} on the web server.")
        sftp.closeFTPCache(location_cache)
        sftp.disconnect()

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv1MakeReposity.")
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
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"sv1MakeReposity failed: {e}")
    else:
        logger.info("sv1MakeReposity succeeded.")

