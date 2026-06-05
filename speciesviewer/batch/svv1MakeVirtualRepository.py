#!/usr/bin/env python3
import os
import sys
import shutil
import configparser
import logging
import argparse
from pathlib import Path

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

logger = logging.getLogger("speciesviewer")


def main(args, config):
    MY_GHEXIF_DIR = str(Path(__file__).resolve().parent.parent / "dist")
    NEW_DB_TREE   = config.get("repository", "BiotaDatabase")

    logger.info("svv1MakeVirtualRepository: creating virtual trip directory structure.")
    os.makedirs(NEW_DB_TREE, exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE, "biota"),  exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE, "admin"),  exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE, "metadata"), exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE, "metadata/active"),     exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE, "metadata/review"),     exist_ok=True)

    initspeciesdir = os.path.join(NEW_DB_TREE, "metadata/initialize")
    os.makedirs(initspeciesdir, exist_ok=True)

    inst = os.path.join(MY_GHEXIF_DIR, "installation files")
    for csv_name in ("empty_species.csv", "initial_speciescollection.csv",
                     "empty_data_files.csv", "empty_data_pictures.csv"):
        shutil.copy(os.path.join(inst, csv_name), os.path.join(initspeciesdir, csv_name))

    shutil.copy(os.path.join(inst, "empty_species.csv"),
                os.path.join(NEW_DB_TREE, "metadata/active/species.csv"))
    shutil.copy(os.path.join(inst, "empty_data_files.csv"),
                os.path.join(NEW_DB_TREE, "metadata/active/data_files.csv"))
    shutil.copy(os.path.join(inst, "empty_data_pictures.csv"),
                os.path.join(NEW_DB_TREE, "metadata/active/data_pictures.csv"))

    # Prefer the master speciescollection so the virtual trip starts with real taxonomy
    master_db = config.get("master", "MasterBiotaDatabase", fallback="")
    master_species = os.path.join(master_db, "metadata/active/speciescollection.csv") if master_db else ""
    if master_db and os.path.isfile(master_species):
        shutil.copy(master_species, os.path.join(NEW_DB_TREE, "metadata/active/speciescollection.csv"))
        logger.info("Copied speciescollection.csv from master.")
    else:
        shutil.copy(os.path.join(inst, "initial_speciescollection.csv"),
                    os.path.join(NEW_DB_TREE, "metadata/active/speciescollection.csv"))

    os.makedirs(os.path.join(NEW_DB_TREE, "web"),              exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE, "web/biota"),        exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE, "web/biotablog"),    exist_ok=True)
    os.makedirs(os.path.join(NEW_DB_TREE, "web/summaries"),    exist_ok=True)

    initwebfiles_source = os.path.join(MY_GHEXIF_DIR, "includes web")
    initwebfiles_target = os.path.join(NEW_DB_TREE, "web/includes web")
    os.makedirs(initwebfiles_target, exist_ok=True)
    for filename in os.listdir(initwebfiles_source):
        shutil.copy(os.path.join(initwebfiles_source, filename),
                    os.path.join(initwebfiles_target, filename))

    logger.info(f"✅ Virtual trip structure created at {NEW_DB_TREE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svv1MakeVirtualRepository.")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG", "svconfig.ini"),
                        help="Module Configuration File")
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"svv1MakeVirtualRepository failed: {e}")
    else:
        logger.info("svv1MakeVirtualRepository succeeded.")
