#!/usr/bin/env python3

# svu1BirdingBreaks.py: Cross-reference a guided-tour checklist against the species collection.
# Reads a semicolon-delimited checklist CSV (hardcoded path — update per trip before running),
# looks up each species by English or Dutch name in the species classification, and writes a
# formatted .prn report showing count, heard-only, known/new, and photo status.
# Hardcoded to the Oman 2025 birding-break file; adapt input/output paths for each new trip.
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
    reporoot = os.path.abspath(os.path.join(scriptpath, os.pardir, os.pardir))
    os.chdir(reporoot)
    sys.path.append(os.path.join(reporoot, 'speciesviewer'))

import engine
import engine.projectlib
import engine.specieslib
import engine.speciesclassification
from engine.specieslib import Level
import engine.triplib
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")
bb_dict = {}
headers = []
counter = 1
newcounter = 1
jpgneeded = 0
Reference_sp = None

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    return config

def readBirdingBreaksList():
    file_path = "C:/CurrentPictures/2025/oman/Checklist Vogelreis Oman - G25012 - 27-01-2025.csv"
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read().split("\n")
        for lines in content:
            tokens = lines.split(";")
            if tokens[0] not in ['\ufeff','']:
                listspecies = {}
                listspecies['number'] = tokens[0]
                listspecies['nlname'] = tokens[2]
                listspecies['enname'] = tokens[3]
                listspecies['scname'] = ""
                listspecies['count'] = 0
                listspecies['heard'] = 0                
                for counttokens in tokens[4:]:
                    if counttokens != "":
                        if 'count' in listspecies:
                            try:
                                if counttokens == "h":
                                    listspecies['heard'] += 1
                                else:
                                    listspecies['count'] += int(counttokens)
                            except:
                                s = 1
                listspecies['knownspecies'] = False
                listspecies['foto'] = 0
                bb_dict[listspecies['enname']] = listspecies

    return

def addSpeciesInfo(sc,sp):
    for enname in bb_dict:
        listspecies = bb_dict[enname]

        if enname.lower() in sc.english_dict:
            listspecies['scname'] = sc.english_dict[enname.lower()]
            if listspecies['scname'] in sp.species_dict:
                listspecies['knownspecies'] = True
        elif listspecies['nlname'].lower() in sc.dutch_dict:
            listspecies['scname'] = sc.dutch_dict[listspecies['nlname'].lower()]
            if listspecies['scname'] in sp.species_dict:
                listspecies['knownspecies'] = True            
    return


# caller
def printbb():
    groep = 0
    fotos = 0
    nieuwe = 0
    file_path = "C:/CurrentPictures/2025/oman/checklist Oman 2025.prn"
    with open(file_path, 'w', encoding='utf-8') as f:
        header_line = 'No     Nederlandse naam              '
        header_line += ' Engelse naam                  '
        header_line += ' Wetenschappelijke naam        '
        header_line += ' Gezien Hoord? Nieuw? Bekende Foto'
        print(header_line, file=f)

        for enname in bb_dict:
            listspecies = bb_dict[enname]
            line = f"{listspecies['number']:6} {listspecies['nlname']:30} {listspecies['enname']:30} {listspecies['scname']:30} "
            if listspecies['count'] >0:
                line += f"{listspecies['count']:6}"
                groep += 1
            else:
                line += "       "
            if listspecies['heard'] == 0:
                line += "       "
            else:
                line += f"{listspecies['heard']:7}"
            if listspecies['knownspecies'] and int(listspecies['count'])>0:
                line += '        Bekend '
            elif not listspecies['knownspecies'] and int(listspecies['count'])>0:
                line += ' Nieuw         '
            else:
                line += '               '
            if listspecies['foto']:
                fotos += 1
                if not listspecies['knownspecies']:
                    nieuwe += 1

            print(line, file=f)

        print(f"\n\nDe groep zag of hoorde {groep} soorten.",file=f)
        print(f"Ik fotografeerde {fotos} soorten, waarvan {nieuwe} fotos.",file=f)
    return

def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   
    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
    
    # get repository-wide sc and per project sp.
    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)   

    # reference project
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)
    referenceSpecies = None
    if MyProject.Trip:
        trip_name = MyProject.Trip.tripname
        trip_master = MyProject.Trip.trip_dict.get(trip_name, {}).get('master_repository_disk', '')
        if trip_master and os.path.isdir(trip_master):
            candidate = os.path.join(trip_master, 'metadata', 'active', 'species.csv')
            if os.path.isfile(candidate):
                referenceSpecies = candidate
    if not referenceSpecies:
        referenceSpecies = config.get(
            "repository", "ReferenceSpecies",
            fallback=os.path.join(MyProject.getMasterRepository(), 'metadata/active/species.csv')
        )
    Reference_sp = engine.specieslib.Species(None)
    Reference_sp.readSpeciesInfo(referenceSpecies, False)
    # test 
    speciesok = Reference_sp.species_dict['biota']

    # read the list with all pre-defined species, as updated in each project using it
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)

    # read BirdingBreaksList
    readBirdingBreaksList()
    addSpeciesInfo(sc, Reference_sp)
    printbb()

    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="svu1BirdingBreaks")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    parser.add_argument(
        "--dir", type=str, default=os.path.join(reporoot, "data"), help="foto directory"
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
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
