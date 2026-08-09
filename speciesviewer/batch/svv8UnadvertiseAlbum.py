#!/usr/bin/env python3
"""
svv8UnadvertiseAlbum — Album: remove the album from the master trip index.

The album half of engine.publish_index.unadvertise (svrUnadvertiseTrip is the
trip half — same operation, since the index does not care which kind wrote the
row). The album's own published website (/trips/<slug>/) is left untouched: only
its advertising row goes away, so it stays reachable by its URL but is no longer
linked from the trip index. This is the "un-advertise" half that Delete Album
flags as a separate step.
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
    return engine.publish_index.unadvertise(config, kind="album")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UnadvertiseAlbum")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG", "svconfig.ini"))
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"UnadvertiseAlbum failed: {e}")
        sys.exit(1)
