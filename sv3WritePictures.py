#!/usr/bin/env python3

# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import argparse
import csv
import os
import sys
import configparser
import time
from pygbif import species
from collections import OrderedDict
from enum import Enum, auto
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from ghexif import files_dict, UniquePictureName_dict 
import ghexif
import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification
from ghexif import COLUMN_NAMES, SELECTED_FIELDS
from ghexif.csv_columns import PICTURE_FIELDS
#from ghexif.specieslib import readSpeciesInfo, writeSpeciesInfo, computeRating
from ghexif.handling import read_file, read_pictures
from ghexif.specieslib import Level, computeRating, Species

logger = logging.getLogger(__name__)

DUMMYSPECIES = ['vogels', 'vogel', 'bird', 'libel', 'libellen',
                'rups', 'panama', 'californie', 'onbekend', 'sprinkhaan', 'turkey']


def CreatePicture(upn):
    PictureRecord = {
        "UniquePictureName": upn,
        "Files":            [],
        "subject":          "",
        "Rating":           "",
        "Label":            "",
        "Rate":             0,
        "WetSoortNaam":     "",
        "EngSoortNaam":     "",
        "NLSoortNaam":      "",
        "ImageSize":        "",     
        "JPGSize":          "",
        "GPSAltitude":      "",
        "GPSPosition":      "",
        "Location":         "",
        "City":             "",
        "State":            "",
        "Country":          "", 
        "CameraCreator":    "", 
        "JPGCreator":       "", 
        "CameraModel":      "", 
        "LensShortName":    "", 
        "LensLongName":     "", 
        "Settings":         "", 
        "Kingdom":          "",
        "Phylum":           "",
        "Class":            "",
        "Order":            "",
        "Family":           "",
        "BestPictureFile":  "", # obsolete?
        "currentHighestRating": 0,
        "BestJPG":      	"",
        "BestJPGPathName":  "",                
        "TargetPath":       "",
        "Description":      "",
        "DateTimeOriginal": "",

    }
    return PictureRecord

def UpdatePicture(upn):
    # files already assigned
    files = upn['Files']
    # fill stuff from the EXIF from the files       
    upn["subject"] =           findXMPItem(files, "XMP:Subject")
    upn["Rating"] =            findXMPItem(files, "XMP:Rating")
    upn["Label"] =             findXMPItem(files, "XMP:Label")
    upn["ImageSize"] =         findCameraItem(files, "ImageSize")
    upn["JPGSize"] =           findJPGItem(files, "ImageSize")
    upn["GPSAltitude"] =       findCameraItem(files, "EXIF:GPSAltitude")
    upn["GPSPosition"] =       findCameraItem(files, "Composite:GPSPosition")
    upn["Location"] =          findXMPItem(files, "XMP:Location")
    upn["City"] =              findXMPItem(files, "XMP:City")
    upn["State"] =             findXMPItem(files, "XMP:State") 
    upn["Country"] =           findXMPItem(files, "XMP:Country") 
    upn["CameraCreator"] =     readableCameraModel(findXMPItem(files, "CameraModel"))  + " Microcode " + findCameraItem(files, "EXIF:Software")
    upn["JPGCreator"] =        findJPGItem(files, "XMP:CreatorTool")
    upn["CameraModel"] =       readableCameraModel(findXMPItem(files, "CameraModel"))
    if upn["CameraModel"] == "": upn["CameraModel"] = findCameraItem(files, "CameraMake")
    upn["LensShortName"] =     findCameraItem(files, "EXIF:LensModel")
    upn["LensLongName"] =      findCameraItem(files, "Composite:LensID")
    upn["Settings"] =          findCameraItem(files, "EXIF:ExposureTime") + " sec at F" + findCameraItem(files, "EXIF:FNumber")  \
             + ", ISO " + findCameraItem(files, "EXIF:ISO") + ", distance " + findCameraItem(files, "MakerNotes:FocusDistance")
    upn["Description"] =       findXMPItem(files, "XMP:Description")
    upn["DateTimeOriginal"] =  findCameraItem(files, "EXIF:DateTimeOriginal")
    if upn["DateTimeOriginal"] == "": upn["DateTimeOriginal"] = findJPGItem(files, "EXIF:DateTimeOriginal")  
    
    if upn["GPSAltitude"] == "undef": 
        upn["GPSAltitude"] = ""

    return upn

# Find the best JPG for a single picture.  
# Typical caes are:
# 1. No JPGs, this most likely
# 2. One JPG from DxO tool or Lightroom
# 3. One JPG from DxO tool AND one from Lightroom.
# In case 3. the DxO JPGs are better (and have a long file name)
def upnBestJPG(filelist):
    bestjpg = ""
    for file in filelist:
        ext = files_dict[file]['WindowsExtension']
        if ext in ("jpg", "jpeg"):
            # first JPG always good
            if not bestjpg:
                bestjpg = file
            # typically PureRaw output has a longer file name and is better than plain Lightroom
            elif len(file)>len(bestjpg):        
                bestjpg = file            
    return bestjpg

# Find the most descriptive value for a column (PICTURE_FIELDS) for a single picture.
def findBestItem(filelist, column, preferredtypes):
    bestresult = ""
    # preferred location
    for file in filelist:
        ext = files_dict[file]['WindowsExtension']
        if ext.upper() in preferredtypes:
            bestresult = files_dict[file].get(column,"")
            if bestresult != "":
                return bestresult

    return ""

# first look for a CameraRaw format ("ORF", "NEF", "NRW", "HEIC")
def findCameraItem(filelist, column):
    return findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC"))
# check for manual input by PhotoMechanic or Lightroom (mostly)
def findXMPItem(filelist, column):
    return findBestItem(filelist, column, ("XMP"))
def findDNGItem(filelist, column):
    return findBestItem(filelist, column, ("DNG"))
# should look at best JPG first!
def findJPGItem(filelist, column):
    return findBestItem(filelist, column, ("JPG", "JPEG"))

# more descriptive names
def readableCameraModel(rawname):
    if rawname == "OM-1MarkII":
        rawname = "OM-1 Mark II"
    return rawname

# addSpeciesInformation for each UniquePictureName
#
# - Add information from the individual files (UpdatePicture(upn))
# - Compute the rating for the picture (computeRating(upn))
# - Read species data and put in the UniquePictureName (upn)
#   - this includes finding the species name, possibly using the GBIF (getSpecies)
# - Compute the directory where the picture needs to be moved to 
#   - this requires a WetSoortNaam, since the path is based on it
#
def addSpeciesInformation(sp, firstUPNtoProcess):
    ClassificationGroupName = ["Species","Family", "Order", "Class", "Phylum", "Kingdom", "Biota"]
    MyMagic = sp.getMP()
    # Add properties for picture
    if SpeciesDebug > 0:  
        filebug = open("addSpeciesInformation.txt", "w")
    recognized_species = 0
    for upn in list(UniquePictureName_dict.values())[firstUPNtoProcess:]:
        picture = UpdatePicture(upn)
        picture['Rate'] = computeRating(upn)
        scientificname = ""
        # Read species data and put in the UniquePictureName (upn)
        for filename in picture['Files']:
            thisfile = files_dict[filename]
            subject_in_jpg = thisfile['XMP:Subject']
            if subject_in_jpg != "":
                scientificname = sp.getSpecies(subject_in_jpg)
                if scientificname == "":       
                    print(f"""The species "{subject_in_jpg}" is not found in the GBIF database as a scientific name for "{upn['UniquePictureName']}".""")
                else:
                    try:           
                        species = MyMagic.getSpecies(scientificname)
                        # these columns are in picture
                        picture['WetSoortNaam'] = species['ScientificName']
                        picture['NLSoortNaam'] = species['DutchName']
                        picture['EngSoortNaam'] = species['EnglishName']
                        picture['Family'] = species['Up']
                        next = sp.species_dict[species['Up']]
                        while next['Up']:       # may need to skip levels
                            picture[ClassificationGroupName[next['Level'].value-1]] = next['Up']
                            next = sp.species_dict[next['Up']]
                        # picture['Class'] = next['Up']
                        # next = species_dict[next['Up']]
                        # picture['Phylum'] = next['Up']
                        # next = species_dict[next['Up']]                    
                        # picture['Kingdom'] = next['Up']
                        recognized_species += 1                                   
                        break                        
                    except:
                        print(f"KEY NOT FOUND {scientificname} for file {filename}")
                        scientificname = ""

        # now compute path when we have a scientific name
        if picture['WetSoortNaam'] != "":
            picture['TargetPath'] = sp.getPathToSpecies(picture['WetSoortNaam'])
            

        # find JPG (if there is more than 1, find the best JPG)
        for filename in picture['Files']:
            thisfile = files_dict[filename]
            rating = 0
            if thisfile['WindowsExtension'] in ['jpg','jpeg']:
                if (picture['BestJPG'] == "") or ('dxo' in thisfile['File:FileName'].lower()):
                    picture['BestJPGPathName'] = upn['TargetPath']
                    picture['BestJPG'] = thisfile['File:FileName']
                if (scientificname != ""):
                    if (species['BestJPG'] == "") or \
                        (int(species['currentHighestRating']) < int(picture['Rate'])):
                        species['BestJPGPathName'] = upn['TargetPath']
                        species['BestJPG'] = thisfile['File:FileName']
                        species['BestUPN'] = upn['UniquePictureName']
                        species['TargetPath'] = upn['TargetPath']
                        species['currentHighestRating'] = int(picture['Rate'])      # so we know if there are better pictures
                        if SpeciesDebug > 0:  
                            print(f"{picture['WetSoortNaam']:28}: Added file {thisfile['WindowsFileName']} to best {species['BestJPGPathName']}/{species['BestJPG']} {species['JPGmap']}", file=filebug)
                    species['JPGmap'].append(thisfile['File:FileName'])        # at some point this was the highest

    print(f"Species information added for {recognized_species} pictures.")
    return 0

# Collect the JPGs, the best file per picture.
# Also update counters while we are at it, for all pictures, also the ones without JPGs 
def addPicturesToSpecies(sp, firstUPNtoProcess, SpeciesDebug):
    if SpeciesDebug > 0:  
        debugfile = open("Species print.txt", "w")
        print(f"Debug enabled for Species analysis at level {SpeciesDebug}.", file=debugfile)
    else:
        debugfile = ""
    # Analyze the current set of pictures, update the statistics
    for upn in list(UniquePictureName_dict.values())[firstUPNtoProcess:]:
        #print(f"addPicturesToSpecies: {upn['UniquePictureName']}  {upn['WetSoortNaam']} ")
        if upn['WetSoortNaam'] != "":               # did we find a species
            species = sp.species_dict[upn['WetSoortNaam']]
            species['iPictureThisRunCount'] = int(species['iPictureThisRunCount']) + 1        # add the pictures from this run, starting from 0
            if (species['iSpeciesCollectionCount'] == 0): species['iSpeciesThisRunCount'] = int(1)                        
            if upn['BestJPG'] != "":                # do we have a picture?
                species['iPicturesWithJPGsThisRun'] = int(species['iPicturesWithJPGsThisRun']) + 1    # add the JPGs from this run, starting from 0

    # at least biota must be present
    if 'biota' not in sp.species_dict:
        print(f"FATAL ERROR: No species found in the project files.")
        return 12

    # Add counters from species to higher levels, tail-recursion works fine
    counters = []
    counters = sp.summarizeSpeciesCounters('biota', counters, debugfile)

    # debug ouput, using Option 2
    sp.printDataForSpecies('biota', debugfile)

    return 0

def main(args, config):
    global starttime, SpeciesDebug
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)   

    # check if the output can be written, aavoid annoying wait
    picturefilename = MyProject.getMetaDirReview()
    try:
        pictures_out_csvfile = open(picturefilename + "data_pictures.csv", "w", encoding='utf-8', newline="")
    except:
        print(f"Picture file ({picturefilename + 'data_pictures.csv'}) is locked by (most likely) Excel, please close it in Excel and re-run.")
        exit()
    try:
        species_out_csvfile = open(picturefilename + "species.csv", "w", encoding='utf-8', newline="")
        species_out_csvfile.close()
    except:
        print(f"Picture file ({picturefilename + 'species.csv'}) is locked by (most likely) Excel, please close it in Excel and re-run.")
        exit()
    #print(f"Species file ({MyProject.getMetaDirReview()+"species.csv"}) is locked by (most likely) Excel, please close it in Excel and re-run.")

    # read the list with all pre-defined species
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(metadatadir + "active/speciescollection.csv", False)
    # also read list with all known species
    # and count the number of data file items we already have
    sp.readSpeciesInfo(metadatadir + "active/species.csv", False)
    RepositoryFileCount = read_file(metadatadir+"active/data_files.csv")
    RepositoryPictureCount = read_pictures(metadatadir+"active/data_pictures.csv")
    # count number of data file items we already have
    # RepositoryFileCount = len(files_dict)      # used?
    # RepositoryPictureCount = 0

    # read new files from project to review
    read_file(MyProject.getMetaDirReview() + "data_files.csv")
    print(f"Read all new data from the in {time.time()-starttime:.1f} seconds.")
    thissteptime = time.time()   

    # Now summarize files to files list per picture ('UniquePictureName')
    # see how many are part of base
    rowCount = 0
    for row in files_dict:
        uniquename = files_dict[row]['UniquePictureName']
        rowCount += 1
        if uniquename not in UniquePictureName_dict:
            UniquePictureName_dict[uniquename] = CreatePicture(uniquename)

            if rowCount == RepositoryFileCount:
                RepositoryPictureCount = len(UniquePictureName_dict)
        UniquePictureName_dict[uniquename]['Files'].append(files_dict[row]['File:FileName'])

    # Collect information from each picture
    if addSpeciesInformation(sp, RepositoryPictureCount) > 0: return   

    # Collect the JPGs, the best file per picture.
    # Also update counters while we are at it, for all pictures, also the ones without JPGs
    if addPicturesToSpecies(sp, RepositoryPictureCount, SpeciesDebug) > 0: return

    # Write Picture CSV file
    picturefilename = MyProject.getMetaDirReview() + "data_pictures.csv"
    with pictures_out_csvfile as csvfile:
        print(f'Write Picture data CSV file {picturefilename}.')
        # needed to get column in order
        ordered_fieldnames = OrderedDict((field, None) for field in PICTURE_FIELDS)                 
        writer = csv.DictWriter(csvfile, fieldnames=ordered_fieldnames, quoting=csv.QUOTE_MINIMAL, delimiter=';')
        writer.writeheader()
        for upn_record in list(UniquePictureName_dict.values())[RepositoryPictureCount:]:            
            writer.writerow(upn_record)

    totaltime = time.time() - starttime                
    print(f'{len(UniquePictureName_dict)} lines written for pictures to {picturefilename}.')
    
    # also create summarizing species, when more counters are added later
    sc.updateSpeciesCollection(metadatadir + "active/speciescollection.csv")  
    sp.writeSpeciesInfo(MyProject.getMetaDirReview()+"species.csv", False)

    # print(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
    # totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')
 

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv3WritePictures.")
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = "svconfig.ini"
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"sv3WritePictures failed: {e}")
    else:
        logger.info("sv3WritePictures succeeded")
