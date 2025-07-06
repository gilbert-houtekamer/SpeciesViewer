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

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import ghexif
import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification
from ghexif.specieslib import Level
import ghexif.triplib

logger = logging.getLogger(__name__)
result_dict = {}
headers = []
counter = 1
newcounter = 1
jpgneeded = 0
Reference_sp = None

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    return config

def scan_files(directory, search_string):
    print(f"\nFile stats GPS and location data:\n\nFile           Count  NoGPS   Noloc  NoCity  NoSpec    Good     Bad  NoColor")    
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.txt'):
                file_path = os.path.join(root, file)
                parseddir = root.split('\\')
                name = parseddir[1]
                if name == "": name = parseddir[0]
                headers.append(name)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    NoGPS = 0
                    NoLocation = 0
                    NoCity = 0
                    NoSpecies = 0
                    Good = 0
                    NotGood = 0
                    NoColor = 0
                    if search_string in content:
                        #print(f"'{search_string}' found in {file_path}")
                        lines = content.split('\n')
                        for i, line in enumerate(lines):
                            # check counters
                            if "No GPS specified for" in line:
                                NoGPS = int(line[32:35])
                            if "No Location specified" in line:
                                NoLocation = int(line[32:35])
                            if "No City specified" in line:
                                NoCity = int(line[32:35])
                            if "No Species name specified" in line:
                                NoSpecies = int(line[32:35]) 
                            if "Interesting or Good quality" in line:
                                Good = int(line[32:35])     
                            if "Duplicate or bad quality" in line:
                                NotGood = int(line[32:35])
                            if "No color label rating" in line:
                                NoColor = int(line[32:35])                                                                                                                                                                                                
                            if search_string in line:
                                # print summary 
                                print(f"{name:12}  {Good+NotGood+NoColor:6} {NoGPS:6}  {NoLocation:6}  {NoCity:6}  {NoSpecies:6}  {Good:6}  {NotGood:6}  {NoColor:6}")
                                # get species info
                                for subsequent_line in lines[i+3:]:
                                    if subsequent_line.strip():  # Check if the line is non-empty
                                        # numbers fixed
                                        numbers = re.findall(r'(\d+)', subsequent_line[0:30])
                                        # names not in same way
                                        tokens = re.split(r'[()/]', subsequent_line[31:])
                                        key = tokens[0].strip()
                                        info = [name, numbers]; 
                                        if key in result_dict:
                                            result_dict[key].append(info)
                                        else:
                                            result_dict[key] = [info]
                    # else:
                    #     print(f"'{search_string}' not found in {file_path}")
    return

# create website based on current count setting, now to test iSpeciesThisRunCount
def scanSpecies(sc, speciesname):
    global counter, newcounter, jpgneeded
    global subheaderdone
    species = sc.sc_dict[speciesname]
    for leaf in species['Leaves']:
        species = sc.sc_dict[leaf]
        if str(species['Level']) == str(Level.SPECIES):
            # do we have this?
            if species['ScientificName'].lower() in result_dict and species['EnglishName'].lower() != "":
                best_rating = 0
                if sc.sc_dict[sc.sc_dict[species['Up']]['Up']]['Up'] != 'aves' and not subheaderdone:
                    print(f"\nOther animals and plants:\n")
                    counter = 1
                    newcounter = 1
                    subheaderdone = True
                data_line = str(counter).rjust(3) + ' ' + species['EnglishName'].ljust(32) + species['DutchName'].ljust(40)
                try:
                    Reference_sp.species_dict[species['ScientificName'].lower()]
                    data_line += '     '
                except:
                    data_line += str(newcounter).rjust(3) + '  '
                    newcounter += 1
                counter += 1
                seen = 0
                # run by file for which we have data
                days_line = ""
                for header in headers:
                    # run through the list to see if we have this list
                    summary = '' 
                    for info in result_dict[species['ScientificName']]:
                        if info[0] == header:
                    # if header in result_dict[species['ScientificName']][0]:
                    #     info = result_dict[species['ScientificName']][header]
                            if info[1][1] > '0':        # JPGs
                                if info[1][2] > '0':    # best rating
                                    summary = str(info[1][2]) + '  '
                                    if int(info[1][2]) > best_rating: best_rating = int(info[1][2])
                                else:                   # JPGs, but none rated
                                    summary = 'j  '
                                seen += 1
                            elif int(info[1][0]) > 0:   # RAWs
                                if not any(c for c in days_line if c != '.' and not c.isspace()):
                                    summary = 'F  '
                                    jpgneeded += 1
                                else:
                                    summary = 'x  '
                                seen += 1                                                       
                            break
                    if summary != "":
                        days_line += summary
                    else:
                        days_line += '.  '

                data_line += f"{days_line} {seen:3} "               
                if best_rating > 0: 
                    data_line += f" {best_rating:3}"
                else:
                    try:
                        Reference_sp.species_dict[species['ScientificName'].lower()]
                    except:
                        data_line += '   Needed     '         
                print(data_line)                
        else:
            scanSpecies(sc, leaf)
    return

# caller
def print_headers(sc):
    global subheaderdone
    print(f'\n\n')
    counter = 0
    header_line = ' '.ljust(36+40) + 'New '
    for header in headers:
        header_line += header[-2:] + ' '
    print(header_line)

    subheaderdone = False
    scanSpecies(sc, 'biota')

    return

def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   
    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
    
    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)   

    # reference project
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=False)
    if MyProject.Trip:
        referenceSpecies = os.path.join(MyProject.getMasterRepository(), 'metadata/active/species.csv')
    else:
        referenceSpecies = config.get("repository","ReferenceSpecies")
    Reference_sp = ghexif.specieslib.Species(None)
    Reference_sp.readSpeciesInfo(referenceSpecies, False)
    # test 
    speciesok = Reference_sp.species_dict['biota']

    # read the list with all pre-defined species, as updated in each project using it
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(metadatadir + "active/speciescollection.csv", False)

    # Collect species
    directory_to_scan = MyProject.getMetaDir() + 'review' # 'C:/laptop/validate/metadata/review'
    search_string = 'Species specified in this dir:'
    scan_files(directory_to_scan, search_string)

    # print in order classification
    print_headers(sc)
    if jpgneeded:
        print(f"There are {jpgneeded} species that have no JPGs on the first occurrence of that species.")

    # print(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="svr2CountSpecies")
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
    config.read(INI_LOCATION)
    # init the logging subsystem
    logging.basicConfig(level=logging.INFO)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
