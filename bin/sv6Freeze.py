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

import ghexif
import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification
from ghexif import COLUMN_NAMES, SELECTED_FIELDS, SVException
from ghexif.csv_columns import PICTURE_FIELDS
from ghexif.handling import read_file, read_pictures
from ghexif.specieslib import Level
import ghexif.exceptions
import ghexif.statemachine
import ghexif.triplib

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

def makeSpeciesDirectory(MyProject, leaf):
    global debugfile, count
    species = MyProject.sp.species_dict[leaf]
    directoryname = (MyProject.getRepositoryPath() + species['TargetPath']).lower()
    os.makedirs(directoryname, exist_ok=True)    

    # find all files in this directory, this is an expensive scan, let's find out how slow it is
    for picture in MyProject.UniquePictureName_dict:
        if MyProject.UniquePictureName_dict[picture]['WetSoortNaam'] == leaf:        # we want this
            for filename in MyProject.UniquePictureName_dict[picture]['Files']:
                try:
                    print(f"File {filename:40}: {MyProject.files_dict[filename]['WindowsFileName']:64} to " + \
                          f"{MyProject.getRepositoryPath()+MyProject.UniquePictureName_dict[picture]['TargetPath']+MyProject.files_dict[filename]['File:FileName']}",
                          file=debugfile)
                    sourcefile = MyProject.files_dict[filename]['WindowsFileName']
                    targetfile = MyProject.getRepositoryPath() + \
                            MyProject.UniquePictureName_dict[picture]['TargetPath']+MyProject.files_dict[filename]['File:FileName']                                      
                    if sourcefile != targetfile:        # == happens when freeze/commit are run twice
                        shutil.copy(sourcefile, targetfile)
                        #also make delete list
                        files_to_delete.append(sourcefile)
                    count += 1
                except:
                    logger.info(f"Species: Directory {(MyProject.getRepositoryPath() + species['TargetPath']).lower()} of file {species['BestJPG']} could not be copied.")
        #logger.info(f"Species: Directory {directoryname} created for {species['ScientificName'] + '.html'}.")
    return

# create website based on current count setting, now to test iSpeciesThisRunCount
def makeDirectories(MyProject, speciesname, all_or_new):
    species = MyProject.sp.species_dict[speciesname]
    # create database to store pictures as needed
    directoryname = (MyProject.getRepositoryPath() + species['TargetPath']).lower()
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
    # Copy existing FileData 
    existing_file_data = MyProject.getMetaDir() + 'active/data_files.csv'
    targetpathbase = MyProject.getRepositoryPath()
    datafilename = os.path.join(MyProject.ReviewFrozen(), "frozen_data_files.csv")     # the base our data
    os.makedirs(MyProject.ReviewFrozen(), exist_ok=True)    
    shutil.copy(existing_file_data, datafilename)
    # Copy existing file data, now add other file records
    with open(datafilename, 'a', newline='', encoding='utf-8') as frozencsv:
        writer = csv.DictWriter(frozencsv, fieldnames=COLUMN_NAMES.values(), quoting=csv.QUOTE_MINIMAL,  delimiter=';')        
        for filespec in list(MyProject.files_dict.values())[firstNewFile:]:
            upn = filespec['UniquePictureName']
            targetpath = MyProject.UniquePictureName_dict[upn]['TargetPath']
            # save for future situation (after move)
            wfn = filespec['WindowsFileName'] 
            fd = filespec['File:Directory']
            filespec['WindowsFileName'] = \
                filespec['WindowsFileName'].replace(filespec['File:Directory'], targetpathbase + targetpath).replace('//','/')
            filespec['File:Directory'] = (targetpathbase + targetpath).replace('//','/')

            writer.writerow(filespec)
            # restore actual situation for move
            filespec['WindowsFileName'] = wfn
            filespec['File:Directory'] = fd
    
    logger.info(f'Data from {len(MyProject.files_dict)-firstNewFile} files added to {len(MyProject.files_dict)} and written to {datafilename}')
    return

def AppendChangedPictureData(MyProject, firstNewPicture):
    existing_picture_data = MyProject.getMetaDir() + 'active/data_pictures.csv'    
    picturename = os.path.join(MyProject.ReviewFrozen(), "frozen_data_pictures.csv")     # the base our data
    shutil.copy(existing_picture_data, picturename)
    with open(picturename, 'a', newline='', encoding='utf-8') as frozencsv:
        ordered_fieldnames = OrderedDict((field, None) for field in PICTURE_FIELDS)                 
        writer = csv.DictWriter(frozencsv, fieldnames=ordered_fieldnames, quoting=csv.QUOTE_MINIMAL,  delimiter=';')
        for picture in list(MyProject.UniquePictureName_dict.values())[firstNewPicture:]:
            writer.writerow(picture)
            
    logger.info(f'Data from {len(MyProject.UniquePictureName_dict)-firstNewPicture} pictures added, total {len(MyProject.UniquePictureName_dict)} now written to {picturename}')
    return

def writeDeleteList(deletelistfilename):
    with open(deletelistfilename, 'w', newline='', encoding='utf-8') as deletecsv:
        for fn in files_to_delete:
            print(f'"{fn}"', file=deletecsv)
    return

def main(args, config):
    global starttime, species_dict, debugfile, count
    starttime = time.time()

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)

    # metafiles
    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
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
    statemachine = ghexif.statemachine.StateMachine(config.get("repository","BiotaDatabase"), config.get("project","PictureDirectory"))
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
