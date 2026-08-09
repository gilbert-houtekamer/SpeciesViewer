#!/usr/bin/env python3
"""
svrUnadvertiseTrip — Trip: remove the active trip from the master trip index.

The mirror of svr4UpdateTrips ("List Trip on the Site"): instead of writing the
trip's row into the master trip_admin.csv it REMOVES it, regenerates the
tripindex pages and uploads them. The trip's own published pages and pictures
stay online at /trips/<slug>/ — only the link from the index goes.

The trip half of engine.publish_index.unadvertise; svv8UnadvertiseAlbum is the
album half. Same operation either way: the index does not care whether the row
came from a trip or an album, only which ini names the repository.
"""
import argparse
import configparser
import logging
import os
import sys

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine.publish_index

logger = logging.getLogger("speciesviewer")


def main(args, config):
    return engine.publish_index.unadvertise(config, kind="trip")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UnadvertiseTrip")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG", "svconfig.ini"))
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"UnadvertiseTrip failed: {e}")
        sys.exit(1)
