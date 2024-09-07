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
from ghexif import SVException
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

DUMMYSPECIES = ['vogel', 'vogels', 'bird', 'libel', 'libellen', 'onbekend',
                'rups', 'panama', 'californie', 'onbekend', 'sprinkhaan', 'turkey',
                'blog', 'goodidpicture']

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
        "malefemale":       "",
        "ageintext":        "",
        "summerwinter":     "",
    }
    return PictureRecord

def UpdatePicture(upn):
    # files already assigned
    files = upn['Files']
    # fill stuff from the EXIF from the files       
    upn["subject"] =           findXMPItem(files, "XMP:Subject")
    upn["Rating"] =            findXMPItem(files, "XMP:Rating")
    upn["Label"] =             findXMPItem(files, "XMP:Label")
    if upn["ImageSize"] == "": upn["ImageSize"] = findCameraOrXMPItem(files, "ImageSize")
    upn["JPGSize"] =           findJPGItem(files, "ImageSize")
    if upn["GPSAltitude"] == "": upn["GPSAltitude"] =       findAnySourceItem(files, "EXIF:GPSAltitude")
    if upn["GPSPosition"] == "": upn["GPSPosition"] =       findAnySourceItem(files, "Composite:GPSPosition")
    upn["Location"] =          findXMPItem(files, "XMP:Location")
    upn["City"] =              findXMPItem(files, "XMP:City")
    upn["State"] =             findXMPItem(files, "XMP:State") 
    upn["Country"] =           findXMPItem(files, "XMP:Country") 
    upn["CameraCreator"] =     readableCameraModel(findXMPItem(files, "CameraModel"))  + " Microcode " + findCameraItem(files, "EXIF:Software")
    upn["JPGCreator"] =        findJPGItem(files, "XMP:CreatorTool")
    upn["CameraModel"] =       readableCameraModel(findXMPItem(files, "CameraModel"))
    if upn["CameraModel"] == "": 
        upn["CameraModel"] = findRawOrJPGItem(files, "CameraMake")
        if upn["CameraModel"] == "OM Digital Solutions": upn["CameraModel"] = "OM-1"
    if upn["LensShortName"] == "": upn["LensShortName"] =     findRawOrJPGItem(files, "EXIF:LensModel")
    if upn["LensLongName"] == "": upn["LensLongName"] =      findRawOrJPGItem(files, "Composite:LensID")
    upn["Settings"] = findRawOrJPGItem(files, "EXIF:ExposureTime") + " sec at F" + findRawOrJPGItem(files, "EXIF:FNumber")  \
             + ", ISO " + findRawOrJPGItem(files, "EXIF:ISO") + ", distance " + getDistance(files)
    upn["Description"] =       findXMPItem(files, "XMP:Description")
    if upn["DateTimeOriginal"] == "": upn["DateTimeOriginal"] =  findRawOrJPGItem(files, "EXIF:DateTimeOriginal")
    
    if upn["GPSAltitude"] == "undef": 
        upn["GPSAltitude"] = ""

    upn["malefemale"] = ""
    upn["ageintext"] = ""
    upn["summerwinter"] = ""      

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
def findAnySourceItem(filelist, column):
    return findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC", "JPG", "JPEG", "XMP"))
def findRawOrJPGItem(filelist, column):
    return findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC", "JPG", "JPEG"))
def findCameraOrXMPItem(filelist, column):
    return findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC", "XMP"))
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

# not too many decimals, no more than 3 significant digits
def getDistance(files):
    textdistance = findRawOrJPGItem(files, "MakerNotes:FocusDistance")
    if textdistance != '' and textdistance != 'inf':
        try:
            numerictext = textdistance.strip(' m')
            periodposition = numerictext.find('.')
            if periodposition > 0:
                meters = textdistance[:periodposition]
                if int(meters) < 100:
                    if int(meters) < 10:
                        if int(meters) < 2:
                            meters = meters + numerictext[periodposition:]
                        else:
                            meters = meters + numerictext[periodposition:periodposition+3]                    
                    else:
                        meters = meters + numerictext[periodposition:periodposition+2]
                meters += ' m'
            else:
                return textdistance
            #print(f"{textdistance} -> {meters}")
            return meters
        except:
            return textdistance
    return textdistance


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
                    logger.info(f"""The species "{subject_in_jpg}" is not found in the GBIF database as a scientific name for "{upn['UniquePictureName']}".""")
                elif scientificname == None:
                    logger.info(f"""Species "{subject_in_jpg}" may have a typo in "{thisfile['WindowsFileName']}", correct it.""")
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
                        # 
                        upn["malefemale"], upn["ageintext"], upn["summerwinter"] = sp.getBirdInfo(thisfile['XMP:Subject'])

                        # picture['Class'] = next['Up']
                        # next = species_dict[next['Up']]
                        # picture['Phylum'] = next['Up']
                        # next = species_dict[next['Up']]                    
                        # picture['Kingdom'] = next['Up']
                        recognized_species += 1                                   
                        break                        
                    except:
                        logger.info(f"KEY NOT FOUND {scientificname} for file {filename}")
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

    logger.info(f"Species information added for {recognized_species} pictures.")
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
        #logger.info(f"addPicturesToSpecies: {upn['UniquePictureName']}  {upn['WetSoortNaam']} ")
        if upn['WetSoortNaam'] != "":               # did we find a species
            species = sp.species_dict[upn['WetSoortNaam']]
            species['iPictureThisRunCount'] = int(species['iPictureThisRunCount']) + 1        # add the pictures from this run, starting from 0
            if (species['iSpeciesCollectionCount'] == 0): species['iSpeciesThisRunCount'] = int(1)                        
            if upn['BestJPG'] != "":                # do we have a picture?
                species['iPicturesWithJPGsThisRun'] = int(species['iPicturesWithJPGsThisRun']) + 1    # add the JPGs from this run, starting from 0

    # at least biota must be present
    if 'biota' not in sp.species_dict:
        raise SVException(f"FATAL ERROR: No species found in the project files.")

    # Add counters from species to higher levels, tail-recursion works fine
    counters = []
    counters = sp.summarizeSpeciesCounters('biota', counters, debugfile)

    # debug ouput, using Option 2
    sp.printDataForSpecies('biota', debugfile)

    return 0

# Add statistics file (sf)
def addMetricsToLog(sp, firstUPNtoProcess, filename):
    MyProject = sp.getMP()
    # open file
    logger.info(f'Write report to "{filename}"')
    with open(filename, "w", newline="", encoding='utf-8') as sf:
        # Add properties for picture
        processedUPNs = 0
        NoGPSPos = 0
        NoLocation = 0
        NoCity = 0
        NoSpeciesGiven = 0
        JPGpresent = 0
        GoodImage = 0
        BadOrDuplicate = 0
        NoColorLabel = 0
        SpeciesNameSet = 0
        SpeciesJPGCount = {}
        # count them
        for upn in list(UniquePictureName_dict.values())[firstUPNtoProcess:]:
            processedUPNs += 1
            if upn['GPSPosition'] in ["", None]:
                NoGPSPos += 1
            if upn['Location'] in ["", None]:
                NoLocation += 1
            if upn['City'] in ["", None]:
                NoCity += 1                
            if upn['BestJPG'] != "":
                JPGpresent += 1
            if upn['Label'] in ["Red", "Yellow", "Green"]:
                GoodImage += 1
            elif upn['Label'] in ["Blue", "Purple"]:
                BadOrDuplicate += 1
            else:
                NoColorLabel += 1
            if upn['WetSoortNaam'] in ["", None]:
                NoSpeciesGiven += 1
            else:
                # species given, make sure we add it to dictionary 
                if upn['WetSoortNaam'] not in SpeciesJPGCount:
                    SpeciesJPGCount[upn['WetSoortNaam']] = [0,0,0,0]
                pictures, jpgs, starred, yellow_or_red = SpeciesJPGCount[upn['WetSoortNaam']]
                if upn['Label'] in ["Red", "Yellow"]: yellow_or_red += 1
                if upn['BestJPG'] != "": jpgs += 1
                if upn['Rating'] > starred: starred = upn['Rating'] 
                SpeciesJPGCount[upn['WetSoortNaam']] = [pictures+1, jpgs, starred, yellow_or_red]
        # report
        if processedUPNs:
            logger.info("")
            if NoGPSPos:
                print(f"No GPS specified for          {NoGPSPos:5} ({int(float(NoGPSPos)/float(processedUPNs)*100)}%) of all images", file=sf)
            else:
                print(f"GPS specified for all         {processedUPNs:5} images", file=sf)
            if NoLocation:
                print(f"No Location specified for     {NoLocation:5} ({int(float(NoLocation)/float(processedUPNs)*100)}%) of all images", file=sf)
            else:
                print(f"Location specified for all    {processedUPNs:5} images", file=sf)
            if NoCity:
                print(f"No City specified for         {NoCity:5} ({int(float(NoCity)/float(processedUPNs)*100)}%) of all images", file=sf)
            else:
                print(f"City specified for all        {processedUPNs:5} images", file=sf)
            if NoSpeciesGiven:
                print(f"No Species name specified for {NoSpeciesGiven:5} ({int(float(NoSpeciesGiven)/float(processedUPNs)*100)}%) of all images", file=sf)
            else:
                print(f"Species name specified for all {processedUPNs} images", file=sf)
            print(f"JPG specified for             {JPGpresent:5}  ({int(float(JPGpresent)/float(processedUPNs)*100)}%) of all images", file=sf)
            print(f"Species information added for {processedUPNs-NoSpeciesGiven:5} pictures.", file=sf)
            print(f"Interesting or Good quality   {GoodImage:5} ({int(float(GoodImage)/float(processedUPNs)*100)}%)", file=sf)
            print(f"Duplicate or bad quality      {BadOrDuplicate:5} ({int(float(BadOrDuplicate)/float(processedUPNs)*100)}%)", file=sf)
            print(f"No color label rating set     {NoColorLabel:5} ({int(float(NoColorLabel)/float(processedUPNs)*100)}%)", file=sf)

            # verify if JPGs are present for each species
            if len(SpeciesJPGCount) > 0:
                print("\nSpecies specified in this dir:\n", file=sf)
                print(f" #Raw  #JPG  Best Color    old Name                             (Engels/Nederlands)", file=sf)
                for species in SpeciesJPGCount:
                    pictures, jpgcount, starred, yellow_or_red = SpeciesJPGCount[species]
                    knownjpg = sp.species_dict[species]['iPicturesWithJPGsCollection']
                    print(f"{pictures:5} {jpgcount:5} {starred:5} {yellow_or_red:5} {knownjpg:6} {species:32} ({sp.species_dict[species]['EnglishName']} / {sp.species_dict[species]['DutchName']})", file=sf)

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

    # start message
    logger.info(f"v3WritePictures: Read file CSV and create enriched species data for each picture.")
    # check if the output can be written, avoid annoying wait
    picturefilename = MyProject.getMetaDirReview()
    try:
        pictures_out_csvfile = open(os.path.join(picturefilename, "data_pictures.csv"), "w", encoding='utf-8', newline="")
    except:
        raise SVException(f"Picture file ({os.path.join(picturefilename, 'data_pictures.csv')}) is locked by (most likely) Excel, please close it in Excel and re-run.")
    try:
        species_out_csvfile = open(os.path.join(picturefilename, "species.csv"), "w", encoding='utf-8', newline="")
        species_out_csvfile.close()
    except:
        raise SVException(f"Picture file ({os.path.join(picturefilename, 'species.csv')}) is locked by (most likely) Excel, please close it in Excel and re-run.")
    #logger.info(f"Species file ({MyProject.getMetaDirReview()+"species.csv"}) is locked by (most likely) Excel, please close it in Excel and re-run.")

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
    read_file(os.path.join(MyProject.getMetaDirReview(), "data_files.csv"))
    logger.info(f"Read all new data from the in {time.time()-starttime:.1f} seconds.")
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
    picturefilename = os.path.join(MyProject.getMetaDirReview(), "data_pictures.csv")
    with pictures_out_csvfile as csvfile:
        logger.info(f'Write Picture data CSV file {picturefilename}.')
        # needed to get column in order
        ordered_fieldnames = OrderedDict((field, None) for field in PICTURE_FIELDS)                 
        writer = csv.DictWriter(csvfile, fieldnames=ordered_fieldnames, quoting=csv.QUOTE_MINIMAL, delimiter=';')
        writer.writeheader()
        for upn_record in list(UniquePictureName_dict.values())[RepositoryPictureCount:]:            
            writer.writerow(upn_record)

    totaltime = time.time() - starttime                
    logger.info(f'{len(UniquePictureName_dict)} lines written for pictures to {picturefilename}.')
    
    # also create summarizing species, when more counters are added later
    sc.updateSpeciesCollection(metadatadir + "active/speciescollection.csv")  
    sp.writeSpeciesInfo(os.path.join(MyProject.getMetaDirReview(), "species.csv"), False)
    
    if addMetricsToLog(sp, RepositoryPictureCount, 
                       os.path.join(MyProject.getMetaDirReview(), "stats report " + MyProject.ProjectName() + ".txt")) > 0: return

    # logger.info(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
    # totaltime = time.time() - starttime      
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv3WritePictures.")
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
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"sv3WritePictures failed: {e}")
    else:
        logger.info("sv3WritePictures succeeded")
