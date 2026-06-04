import argparse
import configparser
import os
from pathlib import Path
import sys
import time
import engine.projectlib
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from engine import SVException
import engine
import engine.projectlib
import engine.specieslib
import engine.speciesclassification
import engine.sftplib
import engine.specieslib
import engine.translatelib
import engine.bloglib
import engine.exceptions
import engine.statemachine
import engine.triplib
from engine import SVException

logger = logging.getLogger("speciesviewer")

global online

def main(args, config):
    global starttime
    starttime = time.time()

    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)

    # get repository-wide sc and per project sp.
    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

    logger.info(f"svu4CheckList: Create CheckList {MyProject.ProjectName()}")


    metadatadir = MyProject.getMetaDir()
    metadatadirreview = MyProject.getMetaDirReview()

    # get species from master repository
    MyProject.readSpeciesInfo(os.path.join(MyProject.MasterRepository, "metadata", "active", "species.csv"), False)
    MyProject.sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)
    # INPUT AND OUTPUT
    in_csv = "D:/CurrentPictures/2026/2026 Extramadura/soorten.csv"
    out_csv = "D:/CurrentPictures/2026/2026 Extramadura/soorten_out.csv"

    import csv
    with open(in_csv, "r", encoding="utf-8") as fin, \
         open(out_csv, "w", newline="", encoding="utf-8") as fout:
        reader = csv.reader(fin, delimiter=";")
        writer = csv.writer(fout, delimiter=";")
        for row in reader:
            scientific_name = row[2].strip().lower() if len(row) > 2 else ""
            # English name from species collection
            sc_entry = MyProject.sc.sc_dict.get(scientific_name, {})
            english_name = sc_entry.get("EnglishName", "")
            if english_name == "":
                dutch_name = row[1].strip().lower() if len(row) > 2 else "" 
                scientific_name = MyProject.sc.dutch_dict.get(dutch_name, {})
                if scientific_name != {}:
                    sc_entry = MyProject.sc.sc_dict.get(scientific_name, {})                    
                    english_name = sc_entry.get("EnglishName", "")  
                      
            # Known if species exists in the species (sp) database
            try:
                sp_entry = MyProject.sp.species_dict.get(scientific_name, {})
                if sp_entry:
                    known = "Known" if sp_entry.get('BestJPG', '') != '' else ""
                else:
                    known = ""
            except:
                known = "?"
            row.append(english_name)
            row.append(known)
            writer.writerow(row)

    logger.info(f"Checklist written to {out_csv}")


    totaltime = time.time() - starttime      
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="CheckList")
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

    main(args, config)

