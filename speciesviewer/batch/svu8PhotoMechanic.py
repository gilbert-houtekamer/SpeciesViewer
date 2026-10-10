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

import engine.projectlib
import engine.specieslib
import engine.speciesclassification
from engine.avibaselib import (add_species_info, read_list_file,
                              selected_list_files)
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

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

    MyProject = engine.projectlib.Project(
        config.get("repository", "BiotaDatabase"),
        config.get("project", "PictureDirectory"),
        config.get("repository", "BiotaWebRoot"),
    )

    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

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

    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(
        os.path.join(metadatadir, "active", "speciescollection.csv"), False
    )

    # Every species list this trip holds, merged. Keywords are for typing a name
    # while culling, where nobody wants to swap keyword sets between the legs of
    # one trip — so this covers the trip, not whichever list the checklist step
    # happens to be pointed at. Each file is read with the columns marked for it.
    trip_slug = MyProject.Trip.tripname if MyProject.Trip else ""
    sources = selected_list_files(MyProject.Repository, config, trip_slug)
    if not sources:
        raise RuntimeError(
            f"No species list found in {MyProject.Repository}. Put the list "
            f"there, or pick one in Utilities ▸ AviBase, and run this again."
        )

    avibase_dict = {}
    for source in sources:
        try:
            one = read_list_file(source, config)
        except Exception as err:
            # One unreadable list must not cost the keywords of the others.
            logger.warning(f"Skipped '{os.path.basename(source)}': {err}")
            continue
        species = sum(1 for v in one.values() if v.get("number") != -1)
        fresh = sum(1 for k in one if k not in avibase_dict)
        logger.info(f"{os.path.basename(source)}: {species} species, "
                    f"{fresh} not already in the set.")
        avibase_dict.update(one)

    if not avibase_dict:
        raise RuntimeError(
            "None of the species lists in the trip folder could be read — see "
            "the warnings above for which, and mark their columns in "
            "Utilities ▸ AviBase ▸ Columns…."
        )
    add_species_info(avibase_dict, sc, Reference_sp)

    # One keyword per SPECIES, not one per spelling, and the spelling is the one
    # this collection already uses. Two operators can call the same bird "Bank
    # Swallow" and "Sand Martin", or differ only over a hyphen, and a keyword
    # typed in the field is worth most when it matches the name the pipeline
    # will look for afterwards. add_species_info has already filled `english`
    # from the species database wherever it knows the bird, so an entry that
    # carries one is preferred over one that only carries the operator's word.
    by_species = {}      # scientific name -> {"en", "nl", "from_database"}
    nameless = []        # entries with no scientific name to group on
    for entry in avibase_dict.values():
        if entry.get("number") == -1:
            continue
        sc = entry.get("scname", "").strip().lower()
        en = (entry.get("english") or entry.get("enname", "")).strip()
        nl = entry.get("nlname", "").strip()
        if not sc:
            nameless.append((en, nl))
            continue
        known = bool(entry.get("english", "").strip())
        held = by_species.get(sc)
        if held is None:
            by_species[sc] = {"en": en, "nl": nl, "from_database": known,
                              "spellings": {en} if en else set()}
            continue
        if en:
            held["spellings"].add(en)
        if known and not held["from_database"]:
            held["en"], held["from_database"] = en, True     # the database wins
        held["nl"] = held["nl"] or nl

    disagreed = [(sc, sorted(v["spellings"])) for sc, v in by_species.items()
                 if len(v["spellings"]) > 1]
    if disagreed:
        logger.info(
            f"{len(disagreed)} species are spelled differently across these "
            f"lists; each keeps one keyword, in the spelling this collection uses."
        )
        for sc, spellings in sorted(disagreed)[:10]:
            logger.info(f"   {sc}: {' | '.join(spellings)}")

    dutch_names = [v["nl"] for v in by_species.values() if v["nl"]]
    english_names = [v["en"] for v in by_species.values() if v["en"]]
    dutch_names += [nl for _, nl in nameless if nl]
    english_names += [en for en, _ in nameless if en]

    # Output directory: configurable, default to PhotoMechanic settings dir
    output_dir = config.get("photomechanic", "output_dir", fallback=_PM_SETTINGS_DIR)
    if not output_dir or not os.path.isdir(output_dir):
        output_dir = MyProject.Repository
        logger.warning(
            f"PhotoMechanic settings directory not found, writing to {output_dir}"
        )

    # One set per trip, since the set now holds every list the trip has.
    trip_name = MyProject.Trip.tripname if MyProject.Trip else "trip"

    nl_path = os.path.join(output_dir, f"{trip_name} nl.snap")
    en_path = os.path.join(output_dir, f"{trip_name} en.snap")

    write_photomechanic_snap(dutch_names, nl_path)
    write_photomechanic_snap(english_names, en_path)

    totaltime = time.time() - starttime
    logger.info(
        f"PhotoMechanic snap files created in {totaltime:.1f}s from "
        f"{len(sources)} species list{'s' if len(sources) != 1 else ''}: "
        f"{len(set(dutch_names))} Dutch, {len(set(english_names))} English keywords."
    )
    if not dutch_names:
        # Worth saying out loud: a keyword file with no Dutch names in it looks
        # exactly like a working one until it is loaded in PhotoMechanic.
        logger.warning(
            "No Dutch names in this list, so the Dutch keyword file is empty. "
            "Mark a Dutch name column in Utilities ▸ AviBase ▸ Columns…, or use "
            "a list that carries one."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svu8PhotoMechanic")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG") or str(svconfig_path()),
        help="Module Configuration File",
    )
    args = parser.parse_args()
    config = configparser.ConfigParser()
    read_ini(config, args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
