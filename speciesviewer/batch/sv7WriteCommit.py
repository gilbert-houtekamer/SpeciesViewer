
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
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")


def checkFrozenData(MyProject):
    frozendir = MyProject.ReviewFrozen()
        # sanity checks - at least present
    if not os.path.exists(os.path.join(frozendir, "frozen_data_files.csv")) or \
        not os.path.exists(os.path.join(frozendir, "frozen_data_pictures.csv")) or \
        not os.path.exists(os.path.join(frozendir, "species.csv")):
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

    # now copy the frozen data
    logger.info(f"Copy frozen files in {frozendir} to {activedir}.")
    shutil.copy(os.path.join(frozendir, "frozen_data_files.csv"),    os.path.join(activedir, "data_files.csv"))
    shutil.copy(os.path.join(frozendir, "frozen_data_pictures.csv"), os.path.join(activedir, "data_pictures.csv"))
    shutil.copy(os.path.join(frozendir, "species.csv"),              os.path.join(activedir, "species.csv"))
    return

# The stylesheet and the map library are SHARED by every rendered page, but each
# site carries its own copy under '<site>/includes web/'. Only repository
# creation (sv1MakeReposity) and the album build (svv4BuildWebsite) ever wrote
# them, so a fix to animals.css reached a trip or the master only if someone
# remembered to run one-off/push_animals_css.py. Publishing HTML that depends on
# a stylesheet without ever refreshing that stylesheet is the bug; the CSS now
# ships WITH the pages.
#
# Only the text assets. The category JPGs in the same folder are ~14 MB, change
# by hand once a year, and are already on every site from creation - re-shipping
# them on every publish would cost far more than it can ever fix.
SHARED_WEB_ASSETS = ("animals.css", "leaflet.css", "leaflet.js")


def putSharedWebAssets(MyProject, sftp):
    """Refresh '<site>/includes web/' with the shipped stylesheet + map library,
    and supply any shipped picture (category images, the default banner) the
    site does not have yet.

    cachedUpload compares a CRC of the content for non-JPGs, so the text assets
    transfer nothing at all once a site is current, and exactly the changed file
    after a stylesheet fix. A missing remote 'includes web' heals itself:
    cachedUpload creates the parent and retries.

    The pictures (~14 MB) are only sent when MISSING — one listing of the
    folder decides. They used to be written only when a repository was created,
    so a site that got a NEW destination later (a trip made before a website
    was chosen; a switch between this computer and a server) had no banner.
    (Gilbert, 2026-10-11.) A site that has them transfers nothing extra."""
    src_dir = Path(__file__).parent.parent / "dist" / "includes web"
    target = sftp.remotePrefix() + MyProject.getBiotaWebRoot() + "includes web"
    sent = 0
    for name in SHARED_WEB_ASSETS:
        src = src_dir / name
        if not src.is_file():
            logger.warning(f"Shared web asset missing, not uploaded: {src}")
            continue
        if not sftp.cachedUpload(str(src), target + "/" + name, preserve_mtime=True):
            sent += 1
    try:
        on_site = set(sftp.listdir(target))
    except Exception:
        on_site = set()
    missing = [p for p in sorted(src_dir.iterdir())
               if p.is_file() and p.name not in SHARED_WEB_ASSETS and p.name not in on_site]
    for src in missing:
        sftp.upload(str(src), target + "/" + src.name)
    if missing:
        logger.info(f"Supplied {len(missing)} missing picture(s) to the site's 'includes web'.")
    if sent:
        logger.info(f"Refreshed {sent} shared web asset(s) (stylesheet / map library).")
    else:
        logger.info("Shared web assets (stylesheet / map library) were already current.")


def ensureSiteSkeletons(MyProject, sftp):
    """Make sure this repository's part of the site AND the master's part have
    their skeleton (intro pages, summaries, 'includes web', placeholder pages);
    supply what is missing from the repositories' own web/ folders. Create used
    to be the only step that published one. See sv1MakeReposity.ensure_skeleton."""
    from batch.sv1MakeReposity import ensure_skeleton
    from engine.websupport import masterWebRoot
    ensure_skeleton(sftp, MyProject.getRepositoryPath(), MyProject.getBiotaWebRoot())
    if not MyProject.isMasterRepository():
        ensure_skeleton(sftp, MyProject.getMasterRepository(),
                        masterWebRoot(MyProject, ""))


def putTripImages(MyProject, sftp):
    """Upload the trip's banner and trip picture to the trip's web root.

    Only repository creation (sv1MakeReposity) ever uploaded them, so a banner
    or picture changed afterwards in the trip settings was named by every page
    but never reached the server: the blogs showed a broken banner. They now
    ship with every commit; cachedUpload skips them once the site is current.
    The names are the ones the pages use (the trip registry), plus trip.ini's
    in case the two disagree. The files live in the trip folder itself."""
    if MyProject.isMasterRepository() or not MyProject.Trip:
        return
    names = set()
    try:
        row = MyProject.Trip.getTrip(MyProject.Trip.getTripName())
        names.update((row.get("trip_banner", ""), row.get("trip_picture", "")))
    except Exception:
        pass
    try:
        from utils.trip_config import read_trip_ini
        ini = read_trip_ini(MyProject.getRepositoryPath()) or {}
        names.update((ini.get("trip_banner", ""), ini.get("trip_picture", "")))
    except Exception as e:
        logger.warning(f"sv7WriteCommit: could not read trip.ini: {e}")
    target = sftp.remotePrefix() + MyProject.getBiotaWebRoot()
    for name in sorted(n.strip() for n in names if n and n.strip()):
        src = os.path.join(MyProject.getRepositoryPath(), name)
        if not os.path.isfile(src):
            logger.warning(f"Trip banner/picture not found in the trip folder, "
                           f"not uploaded: {src}")
            continue
        if not sftp.cachedUpload(src, target.rstrip("/") + "/" + name, preserve_mtime=True):
            logger.info(f"Uploaded trip image {name}.")


def putRecursiveTreeWeb(MyProject, config):
    password = config.get("websettings","Sftppwd").strip("'")
    sftp = engine.sftplib.open_site(password=password)
    location_cache = os.path.join(MyProject.getRepositoryPath(), 'admin/FTPcache.json')
    sftp.openFTPCache(location_cache)
    # Connect to SFTP
    sftp.connect()
    fromdisk = MyProject.ReviewFrozen()
    toweb = sftp.remotePrefix() + MyProject.getBiotaWebRoot()
    ensureSiteSkeletons(MyProject, sftp)
    putSharedWebAssets(MyProject, sftp)
    putTripImages(MyProject, sftp)
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
    # The website is optional (docs/plans/website_optional_plan.md): without
    # one the master is updated on disk only. With one, the server must answer
    # BEFORE anything is committed — a half-published commit is the worse state.
    from utils import site_config
    website = site_config.publishing_set_up()
    if website:
        import engine.connectivity as connectivity
        if not connectivity.publish_reachable():
            raise SVException(connectivity.publish_unreachable_msg())

    if checkFrozenData(MyProject):
    # move "approved" files to the active directory
        commitProjectFiles(MyProject)

        # update the website
        if website:
            putRecursiveTreeWeb(MyProject, config)
        else:
            logger.info("sv7WriteCommit: no website set up — the master is "
                        "updated on this computer only; nothing uploaded.")
        putRecursiveTreeDisk(MyProject)
        # TODO rewrite the Blog with active links to species

    totaltime = time.time() - starttime
    logger.info(f'sv7WriteCommit: Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="WriteCommit")
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
