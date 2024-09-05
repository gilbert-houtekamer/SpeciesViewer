#!/usr/bin/env python3

# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import argparse
import csv
import os
import sys
import configparser
import time
# relative to .py file
INI_LOCATION = "svconfig.ini"

scriptpath = os.path.dirname(__file__)
modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
os.chdir(modpath)
sys.path.append(modpath)

import ghexif
import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    return config

def main(args):
    global starttime, SpeciesDebug
    starttime = time.time()
    SpeciesDebug = 0        # default   

    config = readConfig()
    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)   

    # read the list with all pre-defined species
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(metadatadir + "active/speciescollection.csv", False)

    # read list with scientific names that we want to add.
    # Reads a CSV file line by line, splits by delimiter, and creates dictionaries.
    # Assumes the first row contains column headers.
    speclistinfile = MyProject.getRepositoryPath() + "admin/speclist.csv"
    speclistprinted = MyProject.getRepositoryPath() + "admin/speclist.txt"
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
    sc.updateSpeciesCollection(metadatadir + "active/speciescollection.csv")  

    # print(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')
 

if __name__ == "__main__":
    # get commandline parameters and mix everything
    parser = argparse.ArgumentParser(description="Handle photos.")
    parser.add_argument(
        "--dir", type=str, default=os.path.join(modpath, "data"), help="foto directory"
    )
    main(parser.parse_args())
