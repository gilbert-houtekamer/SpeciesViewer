#!/usr/bin/env python3

# svr1AddSpecies.py: Add species to the species collection based on a list of scientific names.

# this is a one-off script to add species to the collection based on a list of scientific names in a CSV file.
# It reads the list of scientific names from "admin/speclist.csv", looks up their details in the existing species 
# collection, and prints the scientific name along with Dutch and English names to "admin/speclist.txt". 
# Finally, it updates the species collection CSV file.   

# in the GUI, this function is superceeded by the "Add Species" button in the Species Editor, 
# which allows adding species one by one with more control and immediate feedback.

# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import argparse
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

logger = logging.getLogger("speciesviewer")

def main(args, config):
    global starttime, SpeciesDebug
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

    # read the list with all pre-defined species
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)

    # read list with scientific names that we want to add.
    # Reads a CSV file line by line, splits by delimiter, and creates dictionaries.
    # Assumes the first row contains column headers.
    speclistinfile = os.path.join(MyProject.getRepositoryPath(), "admin/speclist.csv")
    speclistprinted = os.path.join(MyProject.getRepositoryPath(), "admin/speclist.txt")
    with open(speclistinfile, mode="r", newline="", encoding='utf-8') as csvfile: 
        with open(speclistprinted, mode="w", newline="", encoding='utf-8') as printfile:           
            header = csvfile.readline().strip().split(';')
            for line in csvfile:
                # Split the line by semicolon
                values = line.strip().split(';')
                scientificname = sp.getSpecies(values[0])
                if scientificname != None:
                    scinstance = sp.species_dict[scientificname]
                    print(f"{scientificname:30}  {scinstance['DutchName']:24} {scinstance['EnglishName']:24}", 
                          file=printfile)

    # also create summarizing species, when more counters are added later
    sc.updateSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"))

    # print(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="svr1AddSpecies")
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

