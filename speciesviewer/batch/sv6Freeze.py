import argparse
import configparser
import os
import shutil
import sys
import time
from datetime import datetime
from collections import OrderedDict
import csv
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine
import engine.projectlib
import engine.specieslib
import engine.speciesclassification
from engine import COLUMN_NAMES, SELECTED_FIELDS, SVException
from engine.csv_columns import PICTURE_FIELDS
from engine.handling import read_file, read_pictures
from engine.specieslib import Level
import engine.exceptions
import engine.statemachine
import engine.triplib

logger = logging.getLogger("speciesviewer")

def getTemplateFileAsString():
    global soort_template_lines                     # will be re-read
    fileasstring = ""
    for line in soort_template_lines:
        fileasstring += line
    return fileasstring

def getDateFromPictures(MyProject):
    for filename in MyProject.files_dict:
        filedata = MyProject.files_dict[filename]
        parsed_date = datetime.strptime(filedata["EXIF:DateTimeOriginal"][0:10], '%Y:%m:%d') 
        return parsed_date.strftime('Blog Pictures %Y-%m-%d')
    return ""

def _same_file_on_disk(src, dst):
    """True when src and dst refer to the same on-disk file even if
    the path strings differ (Windows is case-insensitive, mixes
    forward and back slashes, etc.). Falls back to a normalised
    string compare when one of the paths doesn't yet exist — so the
    skip-guard still fires before the copy on a clean target."""
    try:
        if os.path.exists(src) and os.path.exists(dst):
            return os.path.samefile(src, dst)
    except OSError:
        pass
    return (os.path.normcase(os.path.normpath(src))
            == os.path.normcase(os.path.normpath(dst)))


def makeSpeciesDirectory(MyProject, leaf):
    global debugfile, count, copy_failures
    species = MyProject.sp.species_dict[leaf]
    # os.path.join handles a missing trailing separator on the repository
    # path; raw '+' concat produced 'naturedbbiota/...' for repositories
    # configured without a trailing slash, which broke both the file
    # copies and the != skip-guard a few lines below.
    directoryname = os.path.join(MyProject.getRepositoryPath(),
                                  species['TargetPath']).lower()
    os.makedirs(directoryname, exist_ok=True)

    # find all files in this directory, this is an expensive scan, let's find out how slow it is
    for picture in MyProject.UniquePictureName_dict:
        if MyProject.UniquePictureName_dict[picture]['WetSoortNaam'] == leaf:        # we want this
            for filename in MyProject.UniquePictureName_dict[picture]['Files']:
                sourcefile = ""
                targetfile = ""
                try:
                    sourcefile = MyProject.files_dict[filename]['WindowsFileName']
                    targetfile = os.path.join(
                        MyProject.getRepositoryPath(),
                        MyProject.UniquePictureName_dict[picture]['TargetPath'],
                        MyProject.files_dict[filename]['File:FileName'])
                    print(f"File {filename:40}: {sourcefile:64} to {targetfile}",
                          file=debugfile)
                    # samefile() catches the cases plain != misses on
                    # Windows: forward vs back slashes, case differences,
                    # symlinks. Falls back to a normalised string compare
                    # when the destination doesn't exist yet so a clean
                    # first run still skips no-op copies.
                    if not _same_file_on_disk(sourcefile, targetfile):
                        # Create THIS file's target directory from the exact target
                        # path — the directoryname made above is lowercased and
                        # built from species['TargetPath'], which can be empty for
                        # an undeveloped species or differ in case (Mac). This
                        # guarantees the destination folder exists before the copy.
                        os.makedirs(os.path.dirname(targetfile), exist_ok=True)
                        shutil.copy(sourcefile, targetfile)
                    # Verify the master now physically holds a byte-for-byte copy.
                    # A CSV row must NEVER be published for a file that did not
                    # actually land (the ghost-row incident). A size mismatch means
                    # a partial/aborted copy — just as bad. Any miss is collected
                    # and fails the whole freeze in main() before sv7 commits.
                    if (not os.path.exists(targetfile)
                            or os.path.getsize(targetfile) != os.path.getsize(sourcefile)):
                        copy_failures.append((sourcefile, targetfile, "missing or size mismatch"))
                    count += 1
                except Exception as exc:
                    copy_failures.append((sourcefile, targetfile,
                                          f"{type(exc).__name__}: {exc}"))
                    logger.warning(
                        f"Species copy failed {sourcefile} -> {targetfile}: "
                        f"{type(exc).__name__}: {exc}"
                    )
        #logger.info(f"Species: Directory {directoryname} created for {species['ScientificName'] + '.html'}.")
    return

# create website based on current count setting, now to test iSpeciesThisRunCount
def makeDirectories(MyProject, speciesname, all_or_new, copy_gate="jpg"):
    # copy_gate selects which counter decides whether a subtree's files are
    # copied into the master:
    #   "pictures" — any picture identified this run (has a species), developed
    #                or not. This is what the master needs: EVERY named file must
    #                be backed up, including undeveloped ORF/XMP pairs. Gating on
    #                JPGs (the old behaviour) skipped species whose new pictures
    #                had no rendered JPG, writing CSV rows for files it never
    #                copied — the root of the ghost-row data-loss incident.
    #   "jpg"      — only subtrees with a rendered JPG this run (unchanged legacy
    #                behaviour; kept for the topics/landscape branch, which is
    #                ch.12 territory and deliberately left as-is here).
    species = MyProject.sp.species_dict[speciesname]
    # create database to store pictures as needed
    directoryname = os.path.join(MyProject.getRepositoryPath(),
                                  species['TargetPath']).lower()
    try:
        os.makedirs(directoryname, exist_ok=True)
        for leaf in species['Leaves']:
            species = MyProject.sp.species_dict[leaf]
            gate_count = (int(species['iPictureThisRunCount']) if copy_gate == "pictures"
                          else int(species['iPicturesWithJPGsThisRun']))
            if gate_count > 0:
                if species['Level'] != Level.SPECIES:
                    try:
                        makeDirectories(MyProject, leaf, all_or_new, copy_gate)
                    except Exception as e:
                        logger.info(f"makeDirectories failed ()  {e.strerror} {e.filename}")
                else: # only species store actual data
                    makeSpeciesDirectory(MyProject, leaf)
    except Exception as e:
        print(f"makeDirectories failed: {e.strerror} {e.filename}")
        logger.info(f'Cannot create "{e.filename}": {e.strerror}')
    return

def AppendChangedFileData(MyProject, firstNewFile):
    # Rewrite the frozen data_files.csv from scratch with the CURRENT-schema
    # header, emitting both the existing master rows and the new ones in one
    # consistent layout. Previously this copied the active master file (whose
    # header can predate added columns) and only appended new rows, producing
    # a frozen file whose header was narrower than its rows. sv7 copies this
    # file straight onto active/data_files.csv, so this also re-heads (migrates)
    # the master to the current schema. restval='' fills columns missing from
    # older master rows; extrasaction='ignore' drops any stray keys.
    targetpathbase = MyProject.getRepositoryPath()
    datafilename = os.path.join(MyProject.ReviewFrozen(), "frozen_data_files.csv")
    os.makedirs(MyProject.ReviewFrozen(), exist_ok=True)
    SC = engine.speciesclassification.SpeciesClassification
    all_files = list(MyProject.files_dict.values())
    written = 0
    with open(datafilename, 'w', newline='', encoding='utf-8') as frozencsv:
        writer = csv.DictWriter(frozencsv, fieldnames=list(COLUMN_NAMES.values()),
                                restval='', extrasaction='ignore',
                                quoting=csv.QUOTE_MINIMAL, delimiter=';')
        writer.writeheader()
        for idx, filespec in enumerate(all_files):
            upn = filespec['UniquePictureName']
            # Blog extras (landscape/context photos) that COULD be routed into
            # the topics/ branch now have a WetSoortNaam + TargetPath and are
            # committed like any picture. Only an UN-routed extra (e.g. no
            # capture date -> no year leaf) is still excluded from the master.
            extra_picture = MyProject.UniquePictureName_dict.get(upn)
            if (extra_picture is not None and SC.isBlogExtra(extra_picture['subject'])
                    and not extra_picture.get('WetSoortNaam')):
                continue
            if idx >= firstNewFile:
                # NEW file. In a master commit an untagged picture (no species and
                # not a routed landscape) is NOT part of the master: writing its
                # row would create a ghost entry at the master root (empty
                # TargetPath) with no physical file — the ghost-row incident. Skip
                # it. Existing master rows are left untouched (historical cleanup
                # is a separate, explicit prune).
                if MyProject.isMasterRepository() and not (
                        extra_picture and extra_picture.get('WetSoortNaam')):
                    continue
                # A new file's stored path may point at the repository ONLY when
                # the file will actually be copied there. The copy is gated per
                # species by makeDirectories/makeSpeciesDirectory: master mode
                # copies every identified species (iPictureThisRunCount), a trip
                # only the developed ones (iPicturesWithJPGsThisRun — the JPG gate).
                # Rewriting the path for a file whose species is NOT gated-in this
                # run wrote a ghost row: a CSV entry pointing at a repo file that
                # was never copied. A later run that DOES develop that species
                # (a multi-day species: e.g. alectoris chukar undeveloped on 03-16,
                # a _DxO on 03-20) then read the ghost path as the copy SOURCE and
                # the freeze aborted. Keep the real (CurrentPictures) source path
                # for an un-gated file so a subsequent gated-in freeze copies it
                # from where it actually lives. [[project-ghost-row-incident]]
                leaf = extra_picture.get('WetSoortNaam') if extra_picture else None
                sp_node = MyProject.sp.species_dict.get(leaf) if leaf else None
                gate_counter = ('iPictureThisRunCount' if MyProject.isMasterRepository()
                                else 'iPicturesWithJPGsThisRun')
                will_copy = False
                if sp_node is not None:
                    try:
                        will_copy = int(sp_node.get(gate_counter, 0) or 0) > 0
                    except (TypeError, ValueError):
                        will_copy = False
                if will_copy:
                    # Rewrite its path to the repository location. TargetPath comes
                    # from getPathToSpecies with '/' seps and a trailing '/';
                    # normpath collapses the trailing sep and unifies separators
                    # (the '/\\' path-hygiene bug) and we rebuild the filename from
                    # the authoritative File:FileName, not a fragile str.replace.
                    targetpath = MyProject.UniquePictureName_dict[upn]['TargetPath']
                    wfn = filespec['WindowsFileName']
                    fd = filespec['File:Directory']
                    new_dir = os.path.normpath(os.path.join(targetpathbase, targetpath))
                    filespec['File:Directory'] = new_dir
                    filespec['WindowsFileName'] = os.path.join(new_dir, filespec['File:FileName'])
                    writer.writerow(filespec)
                    # restore actual situation for the subsequent file move
                    filespec['WindowsFileName'] = wfn
                    filespec['File:Directory'] = fd
                else:
                    # Not copied this run (species not gated-in): keep the real
                    # source path so no ghost row is published.
                    writer.writerow(filespec)
            else:
                # Existing master file: already at its repository path.
                writer.writerow(filespec)
            written += 1

    logger.info(f'{written} file records written to {datafilename} (current-schema header).')
    return

def AppendChangedPictureData(MyProject, firstNewPicture):
    # Rewrite the frozen data_pictures.csv from scratch with the current-schema
    # header (see AppendChangedFileData for the rationale and the master
    # migration via sv7). firstNewPicture is no longer needed to slice — every
    # row is re-emitted uniformly — but is kept for call-site compatibility.
    picturename = os.path.join(MyProject.ReviewFrozen(), "frozen_data_pictures.csv")
    os.makedirs(MyProject.ReviewFrozen(), exist_ok=True)
    SC = engine.speciesclassification.SpeciesClassification
    written = 0
    extras_skipped = 0
    untagged_skipped = 0
    with open(picturename, 'w', newline='', encoding='utf-8') as frozencsv:
        ordered_fieldnames = OrderedDict((field, None) for field in PICTURE_FIELDS)
        writer = csv.DictWriter(frozencsv, fieldnames=ordered_fieldnames,
                                restval='', extrasaction='ignore',
                                quoting=csv.QUOTE_MINIMAL, delimiter=';')
        writer.writeheader()
        for idx, picture in enumerate(MyProject.UniquePictureName_dict.values()):
            # A blog extra now lives in the master under topics/<topic>/<leaf>
            # once it was routed (it has a WetSoortNaam). Only an UN-routed
            # extra (no leaf, e.g. missing capture date) stays out.
            if SC.isBlogExtra(picture['subject']) and not picture.get('WetSoortNaam'):
                extras_skipped += 1
                continue
            # NEW untagged pictures (no species, not a routed landscape) are not
            # part of the master — skip them in a master commit so they never
            # become ghost picture rows. Existing master rows pass through
            # (historical cleanup is a separate, explicit prune).
            if (idx >= firstNewPicture and MyProject.isMasterRepository()
                    and not picture.get('WetSoortNaam')):
                untagged_skipped += 1
                continue
            writer.writerow(picture)
            written += 1

    if extras_skipped:
        logger.info(f'{extras_skipped} un-routed blog-extra picture(s) excluded from the master collection.')
    if untagged_skipped:
        logger.info(f'{untagged_skipped} untagged picture(s) excluded from the master (no species).')
    logger.info(f'{written} picture records written to {picturename} (current-schema header).')
    return

def main(args, config):
    global starttime, species_dict, debugfile, count, copy_failures
    starttime = time.time()
    copy_failures = []

    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)

    # metafiles
    # get repository-wide sc and per project sp.
    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)   
    
    logger.info(f"sv6Freeze: Freeze data for project {MyProject.ProjectName()} on local copy on Windows")

    RepositoryFileCount, RepositoryPictureCount, from_time, last_time = MyProject.readProject()
  
    logger.info(f'Now writing consolidated data to a frozen directory for manual review as desired')
    # change and append
    AppendChangedFileData(MyProject, RepositoryFileCount)
    AppendChangedPictureData(MyProject, RepositoryPictureCount)

    # copy all files to big file depository based on classification of data
    # logger.info(f"Will copy all files to destination.")
    debugfile = open("Species print.txt", "w")
    count = 0
    print(f"Debug enabled for Species copy.", file=debugfile)
    # Copy gate by mode (confirmed live on Kreta 2026-07-24):
    #   MASTER  -> "pictures": copy EVERY file of every identified picture,
    #     developed or not (undeveloped ORF/XMP included). The master is the
    #     backup that makes deleting the local originals safe, so it MUST hold
    #     them all. The master gets the undeveloped ones from D:\CurrentPictures
    #     during "Merge Pictures into Master".
    #   TRIP    -> "jpg": a trip repo only needs the website (developed) files;
    #     it is transient (retired once the album is set up). Copying undeveloped
    #     RAWs there is pure bloat — the master already backs them up via Merge.
    # SAFETY DEPENDS ON: run Merge (reads D:\CurrentPictures) BEFORE deleting
    # locals; and the delete gate refuses to remove any file not physically in
    # the master, so a wrong order still can't lose a picture. See docs/DataFlow.
    MyProject.setLanguage('en')
    biota_gate = "pictures" if MyProject.isMasterRepository() else "jpg"
    makeDirectories(MyProject, 'biota', 'New', copy_gate=biota_gate)
    MyProject.makeWebDirectories('biota', 'New')
    # web in both language
    MyProject.setLanguage('nl')
    MyProject.makeWebDirectories('biota', 'New')

    # Build the all-species index (writes frozen/allspecies.csv, which sv7
    # WriteCommit reads) + the intro pages — the work the retired "Build Website"
    # (sv5) step used to do. Freeze now publishes the COMPLETE trip website, so
    # Publish Day (Freeze + Write Commit) needs no separate build step. Trip only:
    # the master keeps its own build (_master_build still runs sv5). Guard on a
    # 'biota' root so a species-less day doesn't KeyError (mirrors sv5).
    if not MyProject.isMasterRepository() and 'biota' in MyProject.sp.species_dict:
        _saveSkip = MyProject.getSkipJPGs()
        MyProject.setSkipJPGs(False)
        MyProject.makeAllSpeciesPage('biota', 'nl')
        MyProject.setSkipJPGs(_saveSkip)
        MyProject.makeAllSpeciesPage('biota', 'en')
        MyProject.setLanguage('nl')
        MyProject.makeIntro('intro', 'nl')
        MyProject.makeIntro('intro-link', 'nl')
        MyProject.setLanguage('en')
        MyProject.makeIntro('intro', 'en')
        MyProject.makeIntro('intro-link', 'en')

    # Copy the topics/ branch FILES into the master tree so landscapes are
    # backed up (topics/<topic>/<leaf>/). Deliberately NO makeWebDirectories for
    # topics: they carry no species pages / occurrence map (on_website=no). The
    # biota walk above never reaches this sibling root, so species pages are
    # unaffected. Left on the legacy JPG gate on purpose — landscapes in the
    # master are ch.12 territory, out of scope for this fix. See engine/topics.py.
    if 'topics' in MyProject.sp.species_dict:
        makeDirectories(MyProject, 'topics', 'New')

    #logger.info(f"{count} files copied to species picture repository in {time.time() - starttime:.1f} seconds.")
    debugfile.close()

    # Fail the freeze LOUDLY if any file did not physically land in the master.
    # The frozen CSVs already list these files at their master paths; letting sv7
    # promote those rows would recreate the ghost-row condition. Aborting here
    # keeps sv7 from running (statemachine gate / GUI step loop both stop on the
    # exception), so no CSV claiming a missing file is ever committed.
    if copy_failures:
        for src, dst, why in copy_failures[:30]:
            logger.warning(f"sv6Freeze: master copy NOT confirmed: {src} -> {dst} ({why})")
        raise SVException(
            f"{len(copy_failures)} file(s) could not be copied into the master (see log). "
            f"Commit aborted so no CSV row is published for a file that isn't there.")
    # simply append new files and pictures, based on the new files.
    sp.writeSpeciesInfo(os.path.join(MyProject.ReviewFrozen(), "species.csv"), commit_counters=True)
    sc.updateSpeciesCollection(os.path.join(MyProject.ReviewFrozen(),"speciescollection.csv")) 

    totaltime = time.time() - starttime      
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="Freeze")
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
