#!/usr/bin/env python3

# create new project
#
import os
import sys
import shutil
import configparser
import logging
import argparse

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import ghexif.sftplib
import ghexif.projectlib

logger = logging.getLogger(__name__)

def main(args, config):
    MY_GHEXIF_DIR = os.path.join(modpath,"dist")
    NEW_DB_TREE = config.get("repository","BiotaDatebase")    # "e:/test3/SpeciesViewDB/"
    WEB_TREE = config.get("repository","BiotaWebRoot")

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
    shutil.copy(MY_GHEXIF_DIR+'/installation files/empty_species.csv', initspeciesdir+'empty_species.csv')
    shutil.copy(MY_GHEXIF_DIR+'/installation files/empty_species.csv', NEW_DB_TREE+'metadata/active/species.csv')
    shutil.copy(MY_GHEXIF_DIR+'/installation files/initial_speciescollection.csv', initspeciesdir+'initial_speciescollection.csv')
    shutil.copy(MY_GHEXIF_DIR+'/installation files/initial_speciescollection.csv', NEW_DB_TREE+'metadata/active/speciescollection.csv')
    shutil.copy(MY_GHEXIF_DIR+'/installation files/empty_data_files.csv', initspeciesdir+'empty_data_files.csv')
    shutil.copy(MY_GHEXIF_DIR+'/installation files/empty_data_files.csv', NEW_DB_TREE+'metadata/active/data_files.csv')
    shutil.copy(MY_GHEXIF_DIR+'/installation files/empty_data_pictures.csv', initspeciesdir+'empty_data_pictures.csv')
    shutil.copy(MY_GHEXIF_DIR+'/installation files/empty_data_pictures.csv', NEW_DB_TREE+'metadata/active/data_pictures.csv')

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
    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"),
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # create master
    # ToDo: MyProject.makeIntro('intro-link-nl')
    # ToDo: MyProject.makeIntro('intro-link-en')
    # now add the files
    def copyMissingLinkPages(directory, exist_ok=True):
        os.makedirs(os.path.join(NEW_DB_TREE,"web",directory), exist_ok=True)
        # ToDo shutil.copy(os.path.join(NEW_DB_TREE,"web/intro-link-nl.csv"), NEW_DB_TREE+"web" + directory + "/intro-link-nl.csv")
        # ToDo shutil.copy(os.path.join(NEW_DB_TREE,"web/intro-link-en.csv"), NEW_DB_TREE+"web" + directory + "/intro-link-en.csv")
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

    if WEB_TREE != "":
        password = config.get("websettings","Sftppwd").strip("'")
        sftp = ghexif.sftplib.Sftp(
            hostname='houtfp.ssh.transip.me',
            username='houtekamercom',
            password=password,
        )
        # Connect to SFTP
        sftp.connect()
        #
        sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE)
        sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/biota')
        sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/summaries')
        sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/biotablog')
        sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/admin')
        sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/includes web')
        fromdir = NEW_DB_TREE + "web/includes web/"
        towebdir = sftp.remotePrefix() + WEB_TREE + '/includes web/'
        sftp.put_r_portable(fromdir, towebdir, preserve_mtime=True)
        # untested
        fromdir = NEW_DB_TREE + "web/biota/"
        towebdir = sftp.remotePrefix() + WEB_TREE + '/biota/'
        sftp.put_r_portable(fromdir, towebdir, preserve_mtime=True)
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
        logger.info("sv1MakeReposity succeeded")

