#!/usr/bin/env python3

# Generate Dutch and English PhotoMechanic keyword .snap files from an AviBase species list.

import argparse
import configparser
import os
import sys
import time
import logging

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification
from ghexif.avibaselib import read_avibase, add_species_info

logger = logging.getLogger("speciesviewer")

_PM_SETTINGS_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Camera Bits, Inc", "Photo Mechanic", "IPTCKeywordsSettings"
)


def write_photomechanic_snap(names, output_path):
    """Write a sorted, deduplicated list of names as a PhotoMechanic .snap XML file."""
    names = sorted(set(n.strip() for n in names if n.strip()))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<KeywordsSettings>",
        "  <KeywordsArray>",
    ]
    for i, name in enumerate(names):
        tag = f"item_{i:08x}"
        lines.append(f"    <{tag}>{name}</{tag}>")
    lines += ["  </KeywordsArray>", "</KeywordsSettings>", ""]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"Written {len(names)} keywords to {output_path}")


def main(args, config):
    starttime = time.time()

    MyProject = ghexif.projectlib.Project(
        config.get("repository", "BiotaDatabase"),
        config.get("project", "PictureDirectory"),
        config.get("repository", "BiotaWebRoot"),
    )

    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

    MyProject.initMasterAndTrip(config, inittrip=True)

    if MyProject.Trip:
        referenceSpecies = os.path.join(
            MyProject.getMasterRepository(), "metadata/active/species.csv"
        )
    else:
        referenceSpecies = config.get("repository", "ReferenceSpecies")

    Reference_sp = ghexif.specieslib.Species(None)
    Reference_sp.readSpeciesInfo(referenceSpecies, False)

    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(
        os.path.join(metadatadir, "active", "speciescollection.csv"), False
    )

    avibase_dict = read_avibase(MyProject.Repository, "avibase.csv")
    add_species_info(avibase_dict, sc, Reference_sp)

    # Collect Dutch and English names — skip section headers (number == -1)
    dutch_names = []
    english_names = []
    for entry in avibase_dict.values():
        if entry.get("number") == -1:
            continue
        nl = entry.get("nlname", "").strip()
        if nl:
            dutch_names.append(nl)
        en = (entry.get("english") or entry.get("enname", "")).strip()
        if en:
            english_names.append(en)

    # Output directory: configurable, default to PhotoMechanic settings dir
    output_dir = config.get("photomechanic", "output_dir", fallback=_PM_SETTINGS_DIR)
    if not output_dir or not os.path.isdir(output_dir):
        output_dir = MyProject.Repository
        logger.warning(
            f"PhotoMechanic settings directory not found, writing to {output_dir}"
        )

    trip_name = MyProject.Trip.tripname if MyProject.Trip else "trip"

    nl_path = os.path.join(output_dir, f"{trip_name} nl.snap")
    en_path = os.path.join(output_dir, f"{trip_name} en.snap")

    write_photomechanic_snap(dutch_names, nl_path)
    write_photomechanic_snap(english_names, en_path)

    totaltime = time.time() - starttime
    logger.info(
        f"PhotoMechanic snap files created in {totaltime:.1f}s: "
        f"{len(set(dutch_names))} Dutch, {len(set(english_names))} English keywords."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svu8PhotoMechanic")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
