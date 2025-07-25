#!/usr/bin/env python3

# Read the potential species list region
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

logger = logging.getLogger("speciesviewer")
avibase_dict = {}
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

# veel selectie mogelijkheden
# https://avibase.bsc-eoc.org/checklist.jsp?lang=EN
# voorbeeld brazilie:
# https://avibase.bsc-eoc.org/checklist.jsp?lang=EN&p2=1&list=ioc&synlang=NL&region=BRms&version=text&lifelist=&highlight=0
def readAviBase():
    file_path = "C:/Users/gilbe/OneDrive/reizen/gambia/soorten.csv"
    number = 1
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read().split("\n")
        for lines in content:
            tokens = lines.split(";")
            try:
                if tokens[1] not in ['Wet','']:
                    listspecies = {}
                    listspecies['nlname'] = tokens[2]
                    listspecies['enname'] = tokens[0]
                    listspecies['scname'] = tokens[1]
                    if tokens[1] not in ["Wet", ""]:
                        listspecies['number'] = number
                        number += 1 
                    elif tokens[1] == "Wet":
                        listspecies['number'] = "Wet"                   
                    else:
                        listspecies['number'] = ""
                    listspecies['gezien'] = 0
                    listspecies['foto'] = 0
                    listspecies['knownspecies'] = False
                    avibase_dict[listspecies['enname']] = listspecies
                elif tokens[0] not in ['\ufeffEngels']:
                    listspecies = {}
                    listspecies['number'] = -1
                    listspecies['enname'] = tokens[0]
                    listspecies['scname'] = ""
                    avibase_dict[listspecies['enname']] = listspecies            
            except:
               pass
    return

def addSpeciesInfo(sc,sp):
    for enname in avibase_dict:
        listspecies = avibase_dict[enname]

        if listspecies['scname'] != "":     # exclude headers
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
    file_path = "C:/Users/gilbe/OneDrive/reizen/gambia/soorten.prn"
    with open(file_path, 'w', encoding='utf-8') as f:
        header_line = 'No     Nederlandse naam              '
        header_line += ' Engelse naam                  '
        header_line += ' Wetenschappelijke naam        '
        header_line += '   seen?  New? Bekende Foto'
        print(header_line, file=f)

        for enname in avibase_dict:
            listspecies = avibase_dict[enname]          # better to for scientific name
            if listspecies['number'] == -1:
                line = f"{listspecies['enname']}"
            else:
                line = f"{listspecies['number']:6} {listspecies['nlname']:30} {listspecies['enname']:30} {listspecies['scname']:30} "
                if listspecies['gezien'] != 0:
                    line += f"   Yes"
                    groep += 1
                else:
                    line += "       "
                if listspecies['knownspecies']:
                    line += '        Known '
                elif not listspecies['knownspecies'] and listspecies['gezien'] != 0:
                    line += '   New         '
                else:
                    line += '               '

            print(line, file=f)

        print(f"\n\nDe groep zag of hoorde {groep} soorten.",file=f)
        print(f"Ik fotografeerde {fotos} soorten, waarvan {nieuwe} fotos.",file=f)
        f.close()

    file_path = "C:/Users/gilbe/OneDrive/reizen/gambia/soorten-out.csv"
    with open(file_path, 'w', encoding='utf-8') as f:
        print("No;Engels;NL;Wet;seen?;New?;Known;Photo", file=f)
        for enname in avibase_dict:
            listspecies = avibase_dict[enname]
            if listspecies['number'] == -1:
                line = f"{listspecies['enname']}"
            else:            
                line = f"{listspecies['number']};{listspecies['enname']};{listspecies['nlname']};{listspecies['scname']};"
                if listspecies['gezien'] != 0:
                    line += "Yes;"
                    groep += 1
                else:
                    line += ";"
                if listspecies['knownspecies']:
                    line += ';Known;'
                elif not listspecies['knownspecies'] and listspecies['gezien'] != 0:
                    line += 'New;;'
                else:
                    line += ';;'
            print(line, file=f)

        f.close()
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
    MyProject.initMasterAndTrip(config, inittrip=True)
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

    # read BirdingBreaksList
    readAviBase()
    addSpeciesInfo(sc, Reference_sp)
    printbb()

    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="svu2AviBase")
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
