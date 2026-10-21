#!/usr/bin/env python3
"""Zip a virtual trip's authored content for transfer to another machine.

Writes <trip folder name>.zip into the target directory containing:
  virttrip.ini
  metadata/**   (blog drafts, extracted day CSVs, seeded species lists)

No picture files are included, and web/ output is excluded — a virtual trip
references the master's biota/ tree by path rather than duplicating it (see
project_virtual_trips design note), and Build Website regenerates web/ on the
target machine. The day CSVs under metadata/review/ carry absolute paths
resolved against THIS machine's master, so Import Virtual Trip re-runs Extract
Days against the target's own master to make them correct there.

Running twice is safe: the existing ZIP is overwritten.
"""

import argparse
import configparser
import os
import sys
import time
import logging
import zipfile
from pathlib import Path

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from engine.exceptions import SVException
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")


def main(args, config):
    starttime = time.time()

    source_root = config.get("repository", "BiotaDatabase", fallback="")
    target_dir  = config.get("utilities", "virttrip_export_target", fallback="")

    if not source_root or not os.path.isdir(source_root):
        raise SVException(f"Virtual trip source not found: {source_root!r}")
    if not target_dir:
        raise SVException("No target directory specified.")

    ini_path = os.path.join(source_root, "virttrip.ini")
    if not os.path.isfile(ini_path):
        raise SVException(f"virttrip.ini not found under {source_root!r}")
    if os.path.normcase(os.path.normpath(source_root)) == os.path.normcase(os.path.normpath(target_dir)):
        raise SVException("Source and target are the same directory.")

    os.makedirs(target_dir, exist_ok=True)
    trip_folder = Path(source_root).name
    zip_path = os.path.join(target_dir, f"{trip_folder}.zip")

    copied = 0
    errors = 0

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(ini_path, "virttrip.ini", compress_type=zipfile.ZIP_DEFLATED)
        copied += 1

        meta_src = os.path.join(source_root, "metadata")
        if os.path.isdir(meta_src):
            for dirpath, _dirs, filenames in os.walk(meta_src):
                for fname in filenames:
                    abs_path = os.path.join(dirpath, fname)
                    arc_name = "metadata/" + os.path.relpath(abs_path, meta_src).replace("\\", "/")
                    try:
                        zf.write(abs_path, arc_name, compress_type=zipfile.ZIP_DEFLATED)
                        copied += 1
                    except Exception as e:
                        logger.warning(f"  ZIP metadata failed {fname}: {e}")
                        errors += 1

    elapsed = time.time() - starttime
    warn = f", {errors} errors" if errors else ""
    zip_mb = os.path.getsize(zip_path) / 1024 / 1024
    msg = (
        f"Virtual trip export done in {elapsed:.1f}s: "
        f"{copied} files, {zip_mb:.1f} MB → {zip_path}{warn}"
    )
    logger.info(msg)
    return msg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svv7ExportVirtualTrip — zip a virtual trip for transfer")
    parser.add_argument(
        "--config",
        default=os.environ.get("SVCONFIG") or str(svconfig_path()),
        help="Configuration file",
    )
    parser.add_argument("--target", required=True, help="Target directory to write the ZIP into")
    args = parser.parse_args()
    config = configparser.ConfigParser()
    read_ini(config, args.config)
    if not config.has_section("utilities"):
        config.add_section("utilities")
    config["utilities"]["virttrip_export_target"] = args.target
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"ExportVirtualTrip failed: {e}")
    else:
        logger.info("ExportVirtualTrip succeeded")
