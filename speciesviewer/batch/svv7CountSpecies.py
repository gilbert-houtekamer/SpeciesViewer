#!/usr/bin/env python3
"""svv7CountSpecies — Album: Count Species.

ONE JOB: write the album's species report (engine.album_species) from the
master's pictures over the album's date range, into <album>/metadata/reports/.
The Count Species window shows it, exports it and flags species without a JPG.
Reads the master only; writes only the two report files.
"""

import argparse
import configparser
import logging
import os
import sys

if __name__ == "__main__":
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import engine.projectlib
import engine.speciesclassification
from engine.album_species import species_table, write_report
from engine import SVException
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path
from utils.virttrip_config import read_virttrip_ini

logger = logging.getLogger("speciesviewer")


def main(args, config):
    album = config.get("repository", "BiotaDatabase")
    master = config.get("master", "MasterBiotaDatabase", fallback="")
    if not master or not os.path.isdir(master):
        raise SVException(f"Master database not found: {master!r}")
    info = read_virttrip_ini(album) or {}
    fromdate = (info.get("fromdate") or "").strip()
    todate = (info.get("todate") or "").strip()
    if not (fromdate and todate):
        raise SVException("The album has no dates; set them in Select or Update Album.")

    project = engine.projectlib.Project(album, "", config.get("repository", "BiotaWebRoot",
                                                               fallback=""))
    sc = engine.speciesclassification.SpeciesClassification(project)
    sc.readSpeciesCollection(os.path.join(master, "metadata", "active",
                                          "speciescollection.csv"), False)

    from db.master_db import MasterDB
    by_day = MasterDB(master).pictures_in_daterange(fromdate, todate)
    days, rows = species_table(sc.sc_dict, by_day)
    title = info.get("title_en") or info.get("trip_name") or os.path.basename(album)
    write_report(os.path.join(album, "metadata", "reports"), title, fromdate, todate,
                 days, rows)
    logger.info(f"Count Species: {sum(1 for r in rows if r['Nr'] != '')} species over "
                f"{len(days)} day(s), {fromdate} to {todate}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svv7CountSpecies")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG") or str(svconfig_path()))
    args = parser.parse_args()
    config = configparser.ConfigParser()
    read_ini(config, args.config)
    logging.basicConfig(level=logging.INFO)
    main(args, config)
