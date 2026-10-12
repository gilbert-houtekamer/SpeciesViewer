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
import engine.species_baseline
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

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

def publish_skeleton(sftp, repo_dir, web_root):
    """Publish a repository's site skeleton — the folders, intro pages, the
    summaries placeholder, 'includes web' and the placeholder pages under
    biota/ — from its LOCAL web/ folder (made when the repository was created)
    to `web_root` on the site. Returns the site path. Used by Create, and by
    ensure_skeleton for a site part that never got it."""
    existingpath = sftp.remotePrefix()
    for directory in web_root.strip('/').split('/'):
        try:
            sftp.safeMkdir(existingpath + '/' + directory)
        except Exception as e:
            print(f"Directory {directory} already exists or cannot be created: {e}")
        existingpath += '/' + directory
    createdpath = existingpath
    for sub in ('/biota', '/summaries', '/biotablog', '/admin', '/includes web'):
        sftp.safeMkdir(createdpath + sub)
    web = os.path.join(repo_dir, "web")
    sftp.upload(os.path.join(web, "intro-nl.html"), createdpath + "/intro-nl.html")
    sftp.upload(os.path.join(web, "intro-en.html"), createdpath + "/intro-en.html")
    sftp.upload(os.path.join(web, "intro-link-nl.html"), createdpath + "/summaries/all biota species-nl.html")
    sftp.upload(os.path.join(web, "intro-link-en.html"), createdpath + "/summaries/all biota species-en.html")
    sftp.put_r_portable(os.path.join(web, "includes web") + "/", createdpath + '/includes web/', preserve_mtime=True)
    sftp.put_r_portable(os.path.join(web, "biota") + "/", createdpath + '/biota/', preserve_mtime=True)
    # A master's empty trip index — never over a real one on the site.
    index_pages = [n for n in ("tripindex-nl.html", "tripindex-en.html")
                   if os.path.isfile(os.path.join(web, n))]
    if index_pages:
        try:
            on_site = set(sftp.listdir(createdpath))
        except Exception:
            on_site = set()
        for name in index_pages:
            if name not in on_site:
                sftp.upload(os.path.join(web, name), createdpath + "/" + name)
    return createdpath


def ensure_skeleton(sftp, repo_dir, web_root) -> bool:
    """Make sure the site part at `web_root` has its skeleton; publish it from
    `repo_dir` if not. True when it had to be supplied.

    Create used to be the only step that published a skeleton, so a site part
    created before a website was chosen — or after a switch between this
    computer and a server — never got one: no /nature/intro-nl.html, no trip
    intro (Gilbert, 2026-10-11). One listing decides; a complete site costs
    nothing more."""
    web_root = (web_root or "").strip()
    if not web_root or not os.path.isfile(os.path.join(repo_dir, "web", "intro-nl.html")):
        return False
    try:
        present = "intro-nl.html" in set(sftp.listdir(sftp.remotePrefix() + web_root.rstrip("/")))
    except Exception:
        present = False
    if present:
        return False
    publish_skeleton(sftp, repo_dir, web_root)
    logger.info(f"Site skeleton supplied for {web_root} (from {repo_dir}).")
    return True


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
    # Import Trip RESTORES a repository from a transfer ZIP instead of creating a
    # new one, and then two things below must not happen. See [utilities]
    # trip_restore_mode in utils/batch_config.py.
    restore_mode = config.get("utilities", "trip_restore_mode",
                              fallback="no").strip().lower() == "yes"
    # if trip, initialze master. NOTE inittrip=True also REWRITES the master's
    # shared trip_admin.csv (Trip.readAndWriteTripAdmin) — that file lists every
    # published trip, so a machine restoring one trip must never author it.
    MyProject.initMasterAndTrip(config, inittrip=not restore_mode)
    # On a restore the ZIP already carries the trip's own species collection AND
    # its lifer baseline; re-seeding from THIS machine's master would be wrong,
    # since a laptop's master is partial and a fresh baseline would silently
    # promote species the master already knows into lifers.
    if not MyProject.isMasterRepository() and not restore_mode:
        shutil.copy(os.path.join(MyProject.getMasterRepository(), 'metadata/active/speciescollection.csv'),
                os.path.join(NEW_DB_TREE, 'metadata/active/speciescollection.csv'))
        # Freeze what the master already has pictures of, so Count Species can
        # still tell lifers apart after this trip has been merged into the
        # master. Never fatal: a missing baseline only means Count Species
        # falls back to the live master.
        engine.species_baseline.write_baseline(MyProject.getMasterRepository(), NEW_DB_TREE)

    # The blog index belongs to a TRIP; a master has no blog of its own.
    is_master = MyProject.isMasterRepository()
    if not is_master:
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
    # biota/biota-*.html only appears once a repository has pictures, yet the
    # trip index links to it ("nature", "(Trip soorten)").
    copyMissingLinkPages("biota", exist_ok=True)
    if is_master:
        # The master owns the trip index; a new master had none, so the
        # site's Trips link was a 404 until a trip was advertised (Gilbert,
        # 2026-10-12).
        from engine.triplib import write_trip_index
        for language in ('nl', 'en'):
            write_trip_index(MyProject, language, os.path.join(NEW_DB_TREE, "web"))

    # The local web/ tree above is always made; the server copy only when a
    # website is set up — publishing is optional (docs/plans/website_optional_plan.md).
    from utils import site_config
    if config.get("utilities", "build_only", fallback="no").strip().lower() == "yes":
        # Create Master Collection: build the pages, publish nothing — the site
        # may belong to another master. engine.site_init supplies the skeleton.
        WEB_TREE = ""
    if WEB_TREE != "" and not site_config.publishing_set_up():
        logger.info("sv1MakeReposity: no website set up — created locally only; "
                    "nothing uploaded.")
    if WEB_TREE != "" and site_config.publishing_set_up():
        password = config.get("websettings","Sftppwd").strip("'")
        sftp = engine.sftplib.open_site(password=password)
        # Connect to SFTP
        sftp.connect()
        location_cache = os.path.join(MyProject.getRepositoryPath(), 'admin/FTPcache.json')
        sftp.openFTPCache(location_cache)
        createdpath = publish_skeleton(sftp, NEW_DB_TREE, WEB_TREE)
        # project info
        if not is_master:
            tripname = Path(MyProject.getBiotaWebRoot()).name
            blog.uploadBlogIndex(sftp)          
            try:
                trip = MyProject.Trip.getTrip(tripname)
                # Only images the trip actually names: an empty name would "upload"
                # the trip folder itself (and fail). Without them the pages use
                # the site's default banner from 'includes web'.
                for key in ('trip_banner', 'trip_picture'):
                    name = (trip.get(key) or '').strip()
                    if name:
                        sftp.upload(os.path.join(NEW_DB_TREE, name), createdpath + '/' + name)
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
        default=os.environ.get("SVCONFIG") or str(svconfig_path()),
        help="Module Configuration File",
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
        logger.warning(f"sv1MakeReposity failed: {e}")
    else:
        logger.info("sv1MakeReposity succeeded.")

