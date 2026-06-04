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

import speciesviewer.engine as engine
import speciesviewer.engine.projectlib
import speciesviewer.engine.specieslib
import speciesviewer.engine.speciesclassification
from speciesviewer.engine import COLUMN_NAMES, SELECTED_FIELDS, SVException
from speciesviewer.engine.csv_columns import PICTURE_FIELDS
from speciesviewer.engine.handling import read_file, read_pictures
from speciesviewer.engine.specieslib import Level
import speciesviewer.engine.exceptions
import speciesviewer.engine.statemachine
import speciesviewer.engine.triplib

logger = logging.getLogger("speciesviewer")
files_to_delete = []

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
    global debugfile, count
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
                        shutil.copy(sourcefile, targetfile)
                        #also make delete list
                        files_to_delete.append(sourcefile)
                    count += 1
                except Exception as exc:
                    logger.info(
                        f"Species copy failed {sourcefile} -> {targetfile}: "
                        f"{type(exc).__name__}: {exc}"
                    )
        #logger.info(f"Species: Directory {directoryname} created for {species['ScientificName'] + '.html'}.")
    return

# create website based on current count setting, now to test iSpeciesThisRunCount
def makeDirectories(MyProject, speciesname, all_or_new):
    species = MyProject.sp.species_dict[speciesname]
    # create database to store pictures as needed
    directoryname = os.path.join(MyProject.getRepositoryPath(),
                                  species['TargetPath']).lower()
    try:
        os.makedirs(directoryname, exist_ok=True)
        for leaf in species['Leaves']:
            species = MyProject.sp.species_dict[leaf]
            if int(species['iPicturesWithJPGsThisRun']) > 0:
                if species['Level'] != Level.SPECIES:
                    try:
                        makeDirectories(MyProject, leaf, all_or_new)
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
            # Non-species "blog extras" (landscape/context photos) are not
            # committed to the master: they live only in the trip and in the
            # blog's own copy. Skip their file records here.
            extra_picture = MyProject.UniquePictureName_dict.get(upn)
            if extra_picture is not None and SC.isBlogExtra(extra_picture['subject']):
                continue
            if idx >= firstNewFile:
                # New file: rewrite its path to the repository (master) location.
                targetpath = MyProject.UniquePictureName_dict[upn]['TargetPath']
                wfn = filespec['WindowsFileName']
                fd = filespec['File:Directory']
                new_dir = os.path.join(targetpathbase, targetpath)
                filespec['WindowsFileName'] = filespec['WindowsFileName'].replace(filespec['File:Directory'], new_dir)
                filespec['File:Directory'] = new_dir
                writer.writerow(filespec)
                # restore actual situation for the subsequent file move
                filespec['WindowsFileName'] = wfn
                filespec['File:Directory'] = fd
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
    with open(picturename, 'w', newline='', encoding='utf-8') as frozencsv:
        ordered_fieldnames = OrderedDict((field, None) for field in PICTURE_FIELDS)
        writer = csv.DictWriter(frozencsv, fieldnames=ordered_fieldnames,
                                restval='', extrasaction='ignore',
                                quoting=csv.QUOTE_MINIMAL, delimiter=';')
        writer.writeheader()
        for picture in MyProject.UniquePictureName_dict.values():
            # Blog extras (landscape/context photos) stay out of the master.
            if SC.isBlogExtra(picture['subject']):
                extras_skipped += 1
                continue
            writer.writerow(picture)
            written += 1

    if extras_skipped:
        logger.info(f'{extras_skipped} blog-extra (landscape) pictures excluded from the master collection.')
    logger.info(f'{written} picture records written to {picturename} (current-schema header).')
    return

def writeDeleteList(deletelistfilename):
    with open(deletelistfilename, 'w', newline='', encoding='utf-8') as deletecsv:
        for fn in files_to_delete:
            print(f'"{fn}"', file=deletecsv)
    return

def main(args, config):
    global starttime, species_dict, debugfile, count
    starttime = time.time()

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
    # add new directories as needed, right in database
    MyProject.setLanguage('en')
    makeDirectories(MyProject, 'biota', 'New')
    MyProject.makeWebDirectories('biota', 'New')
    # web in both language
    MyProject.setLanguage('nl')
    MyProject.makeWebDirectories('biota', 'New')

    # write the list with files to delete
    writeDeleteList(os.path.join(MyProject.ReviewFrozen(), "deletelist.csv"))
    #logger.info(f"{count} files copied to species picture repository in {time.time() - starttime:.1f} seconds.")
    debugfile.close()   
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
