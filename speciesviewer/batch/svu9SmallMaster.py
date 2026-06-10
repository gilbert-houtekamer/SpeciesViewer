#!/usr/bin/env python3
"""Copy JPG-bearing species folders from the master biota tree to a SmallMaster target.

Copies only .jpg/.jpeg/.xmp files from biota/ (skipping RAWs, .dop, HTML, and the
web/ output tree), plus the metadata/active/ directory in full.  Running twice is
safe: existing destination files are overwritten and nothing is deleted.
"""

import argparse
import configparser
import csv as csv_mod
import os
import shutil
import sys
import time
import logging
from pathlib import Path

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from engine.exceptions import SVException
from engine.handling import relativize_path

logger = logging.getLogger("speciesviewer")

_COPY_EXTS = {".jpg", ".jpeg", ".xmp"}


def _copy_data_files_relativized(src: str, dst: str, master_root: str) -> None:
    """Copy data_files.csv while rewriting WindowsFileName and File:Directory
    to be root-relative (e.g. /biota/animalia/...) so the copy is portable."""
    with open(src, "r", newline="", encoding="utf-8") as fh_in:
        reader = csv_mod.DictReader(fh_in, delimiter=";")
        fieldnames = reader.fieldnames
        with open(dst, "w", newline="", encoding="utf-8") as fh_out:
            writer = csv_mod.DictWriter(
                fh_out, fieldnames=fieldnames,
                delimiter=";", quoting=csv_mod.QUOTE_MINIMAL,
            )
            writer.writeheader()
            for row in reader:
                row["File:Directory"] = relativize_path(
                    row.get("File:Directory", ""), master_root)
                row["WindowsFileName"] = relativize_path(
                    row.get("WindowsFileName", ""), master_root)
                writer.writerow(row)


def main(args, config):
    starttime = time.time()

    source_root = (
        config.get("master", "MasterBiotaDatabase", fallback="")
        or config.get("repository", "BiotaDatabase", fallback="")
    )
    target_root = config.get("utilities", "smallmaster_target", fallback="")

    if not source_root or not os.path.isdir(source_root):
        raise SVException(f"Master source not found: {source_root!r}")
    if not target_root:
        raise SVException("No target directory specified.")
    if os.path.normcase(os.path.normpath(source_root)) == os.path.normcase(os.path.normpath(target_root)):
        raise SVException("Source and target are the same directory.")

    biota_src = os.path.join(source_root, "biota")
    meta_src = os.path.join(source_root, "metadata", "active")

    if not os.path.isdir(biota_src):
        raise SVException(f"biota/ not found under {source_root!r}")

    # ── biota tree: copy only JPG/XMP files ─────────────────────────────────
    copied_pics = 0
    errors = 0
    biota_dst = os.path.join(target_root, "biota")

    for dirpath, _dirs, filenames in os.walk(biota_src):
        wanted = [f for f in filenames if Path(f).suffix.lower() in _COPY_EXTS]
        if not wanted:
            continue
        rel = os.path.relpath(dirpath, biota_src)
        dst_dir = os.path.join(biota_dst, rel) if rel != "." else biota_dst
        os.makedirs(dst_dir, exist_ok=True)
        for fname in wanted:
            try:
                shutil.copy2(os.path.join(dirpath, fname), os.path.join(dst_dir, fname))
                copied_pics += 1
                if copied_pics % 200 == 0:
                    logger.info(f"  Biota: {copied_pics} files copied...")
            except Exception as e:
                logger.warning(f"  Copy failed {fname}: {e}")
                errors += 1

    # ── metadata/active: copy all CSVs and support files ────────────────────
    copied_meta = 0
    if os.path.isdir(meta_src):
        meta_dst = os.path.join(target_root, "metadata", "active")
        os.makedirs(meta_dst, exist_ok=True)
        for fname in os.listdir(meta_src):
            src_file = os.path.join(meta_src, fname)
            if not os.path.isfile(src_file):
                continue
            dst_file = os.path.join(meta_dst, fname)
            try:
                if fname.lower() == "data_files.csv":
                    _copy_data_files_relativized(src_file, dst_file, source_root)
                else:
                    shutil.copy2(src_file, dst_file)
                copied_meta += 1
            except Exception as e:
                logger.warning(f"  Metadata copy failed {fname}: {e}")
                errors += 1

    elapsed = time.time() - starttime
    warn = f", {errors} errors" if errors else ""
    msg = (
        f"SmallMaster done in {elapsed:.1f}s: "
        f"{copied_pics} biota files + {copied_meta} metadata files → {target_root}{warn}"
    )
    logger.info(msg)
    return msg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svu9SmallMaster — create SmallMaster database")
    parser.add_argument(
        "--config",
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Configuration file",
    )
    parser.add_argument("--target", required=True, help="Target directory for SmallMaster output")
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    if not config.has_section("utilities"):
        config.add_section("utilities")
    config["utilities"]["smallmaster_target"] = args.target
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"svu9SmallMaster failed: {e}")
    else:
        logger.info("svu9SmallMaster succeeded")
