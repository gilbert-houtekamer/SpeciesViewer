#!/usr/bin/env python3
"""Copy JPG-bearing species folders from the master biota tree to a SmallMaster ZIP.

Writes smallmaster.zip into the target directory containing:
  biota/**/*.jpg + *.xmp  (RAWs, .dop, HTML and web/ output excluded)
  metadata/active/        (all CSVs; data_files.csv paths are made root-relative)

Running twice is safe: the existing ZIP is overwritten.
"""

import argparse
import configparser
import csv as csv_mod
import io
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
from engine.handling import relativize_path

logger = logging.getLogger("speciesviewer")

_COPY_EXTS = {".jpg", ".jpeg", ".xmp"}


def _add_data_files_to_zip(zf: zipfile.ZipFile, src: str, master_root: str, arc_name: str) -> None:
    """Write data_files.csv into the ZIP with WindowsFileName/File:Directory made root-relative."""
    buf = io.StringIO()
    with open(src, "r", newline="", encoding="utf-8") as fh_in:
        reader = csv_mod.DictReader(fh_in, delimiter=";")
        fieldnames = reader.fieldnames
        writer = csv_mod.DictWriter(
            buf, fieldnames=fieldnames,
            delimiter=";", quoting=csv_mod.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in reader:
            row["File:Directory"] = relativize_path(row.get("File:Directory", ""), master_root)
            row["WindowsFileName"] = relativize_path(row.get("WindowsFileName", ""), master_root)
            writer.writerow(row)
    zf.writestr(arc_name, buf.getvalue().encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)


def main(args, config):
    starttime = time.time()

    source_root = (
        config.get("master", "MasterBiotaDatabase", fallback="")
        or config.get("repository", "BiotaDatabase", fallback="")
    )
    target_dir = config.get("utilities", "smallmaster_target", fallback="")

    if not source_root or not os.path.isdir(source_root):
        raise SVException(f"Master source not found: {source_root!r}")
    if not target_dir:
        raise SVException("No target directory specified.")
    if os.path.normcase(os.path.normpath(source_root)) == os.path.normcase(os.path.normpath(target_dir)):
        raise SVException("Source and target are the same directory.")

    biota_src = os.path.join(source_root, "biota")
    meta_src  = os.path.join(source_root, "metadata", "active")

    if not os.path.isdir(biota_src):
        raise SVException(f"biota/ not found under {source_root!r}")

    os.makedirs(target_dir, exist_ok=True)
    zip_path = os.path.join(target_dir, "smallmaster.zip")

    copied_pics = 0
    copied_meta = 0
    errors = 0

    with zipfile.ZipFile(zip_path, "w") as zf:

        # ── biota tree: JPG/XMP only ─────────────────────────────────────────
        for dirpath, _dirs, filenames in os.walk(biota_src):
            for fname in filenames:
                if Path(fname).suffix.lower() not in _COPY_EXTS:
                    continue
                abs_path = os.path.join(dirpath, fname)
                arc_name = "biota/" + os.path.relpath(abs_path, biota_src).replace("\\", "/")
                # JPGs are already compressed; XMPs are small text → deflate
                compress = (zipfile.ZIP_STORED
                            if Path(fname).suffix.lower() in {".jpg", ".jpeg"}
                            else zipfile.ZIP_DEFLATED)
                try:
                    zf.write(abs_path, arc_name, compress_type=compress)
                    copied_pics += 1
                    if copied_pics % 500 == 0:
                        logger.info(f"  Biota: {copied_pics} files added to ZIP…")
                except Exception as e:
                    logger.warning(f"  ZIP biota failed {fname}: {e}")
                    errors += 1

        # ── metadata/active ──────────────────────────────────────────────────
        if os.path.isdir(meta_src):
            for fname in os.listdir(meta_src):
                src_file = os.path.join(meta_src, fname)
                if not os.path.isfile(src_file):
                    continue
                arc_name = "metadata/active/" + fname
                try:
                    if fname.lower() == "data_files.csv":
                        _add_data_files_to_zip(zf, src_file, source_root, arc_name)
                    else:
                        zf.write(src_file, arc_name, compress_type=zipfile.ZIP_DEFLATED)
                    copied_meta += 1
                except Exception as e:
                    logger.warning(f"  ZIP metadata failed {fname}: {e}")
                    errors += 1

    elapsed = time.time() - starttime
    warn = f", {errors} errors" if errors else ""
    zip_mb = os.path.getsize(zip_path) / 1024 / 1024
    msg = (
        f"SmallMaster ZIP done in {elapsed:.1f}s: "
        f"{copied_pics} biota files + {copied_meta} metadata files, "
        f"{zip_mb:.0f} MB → {zip_path}{warn}"
    )
    logger.info(msg)
    return msg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svu9SmallMaster — create SmallMaster database ZIP")
    parser.add_argument(
        "--config",
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Configuration file",
    )
    parser.add_argument("--target", required=True, help="Target directory to write smallmaster.zip into")
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
