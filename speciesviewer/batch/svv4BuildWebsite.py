#!/usr/bin/env python3
"""
svv4BuildWebsite — Virtual Trip: build and upload species web pages.

Reads all per-day data_files.csv / data_pictures.csv from
  <virttrip>/metadata/review/<YYYY-MM-DD>/
derives species counts, generates species HTML pages and the all-species
overview into the virtual trip's frozen + web folders, then uploads via
SFTP to mirror what sv7WriteCommit does for a regular trip.

Upload steps (same as sv7WriteCommit.putRecursiveTreeWeb):
  1. Upload biota/ tree (species HTML + full-size JPGs) from frozen/biota/
  2. Upload summaries/all biota species-{nl,en}.html from web/summaries/
  3. Resize and upload small JPGs from master originals (via allspecies.csv)
"""
import csv
import os
import sys
import configparser
import logging
import argparse
from pathlib import Path

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine.projectlib
import engine.specieslib
import engine.speciesclassification
import engine.bloglib
from engine import SVException
from engine.handling import read_file, read_pictures
from engine.websupport import jpgResizeCopy, getSmallJPG

logger = logging.getLogger("speciesviewer")


def read_virttrip_days(project, virttrip_root: str) -> int:
    """Load all per-day CSVs into project.files_dict / UniquePictureName_dict.
    Returns the number of days loaded."""
    review_root = Path(virttrip_root) / "metadata" / "review"
    if not review_root.is_dir():
        logger.warning(f"svv4: review directory not found: {review_root}")
        return 0

    day_dirs = sorted(d for d in review_root.iterdir() if d.is_dir()
                      and (d / "data_files.csv").exists())
    loaded = 0
    for day_dir in day_dirs:
        files_csv = day_dir / "data_files.csv"
        pics_csv  = day_dir / "data_pictures.csv"
        if files_csv.exists():
            read_file(project, str(files_csv), silent=True)
        if pics_csv.exists():
            read_pictures(project, str(pics_csv))
        if files_csv.exists():
            loaded += 1
            logger.info(f"  {day_dir.name}: loaded")

    return loaded


def main(args, config):
    virttrip_root = config.get("repository", "BiotaDatabase")
    web_root      = config.get("repository", "BiotaWebRoot")
    master_db     = config.get("master", "MasterBiotaDatabase", fallback="")

    if not master_db or not os.path.isdir(master_db):
        raise SVException(f"Master database not found: {master_db!r}")

    logger.info(f"svv4BuildWebsite: {virttrip_root}")

    project = engine.projectlib.Project(virttrip_root, "", web_root)
    project.initMasterAndTrip(config, inittrip=False)

    sc = engine.speciesclassification.SpeciesClassification(project)
    project.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(project)
    project.registerSpecies(sp)

    master_sc_csv      = os.path.join(master_db, "metadata", "active", "speciescollection.csv")
    master_species_csv = os.path.join(master_db, "metadata", "active", "species.csv")
    sc.readSpeciesCollection(master_sc_csv, False)
    sp.readSpeciesInfo(master_species_csv, True)

    # Load all per-day data from the virtual trip
    days = read_virttrip_days(project, virttrip_root)
    if days == 0:
        raise SVException("No day data found — run 'Extract Days from Master' first.")
    logger.info(f"Loaded {days} day(s) ({len(project.files_dict)} files, "
                f"{len(project.UniquePictureName_dict)} pictures).")

    # Derive species per picture
    project.addSpeciesInformation(sp, 0)
    project.addPicturesToSpecies(sp, 0, 0)

    # Reset the all-species file tracking before building (class-level dict)
    project.resetAllSpeciesFiles()

    # Build web pages in both languages.
    # Use "New" so species HTML lands in ReviewFrozen() alongside the JPG copies,
    # enabling a single put_r_portable upload instead of two separate trees.
    for language in ("nl", "en"):
        project.setLanguage(language)
        project.makeWebDirectories("biota", "New")
        project.makeAllSpeciesPage("biota", language)
        logger.info(f"  Built {language.upper()} pages.")

    logger.info(f"svv4BuildWebsite: local build complete ({days} day(s)).")

    # --- SFTP upload ---
    if not project.online:
        logger.warning("Not online — local build only, nothing uploaded.")
        return

    password = config.get("websettings", "Sftppwd", fallback="").strip("'")
    blog_obj = engine.bloglib.Blog(project)
    sftp = blog_obj.openSFTP(password)
    if sftp is None:
        logger.warning("Could not open SFTP — local build only.")
        return

    prefix    = sftp.remotePrefix()
    bio_web   = project.getBiotaWebRoot()    # e.g. /trips/Tucson_2023/
    frozen    = project.ReviewFrozen()       # virttrip/metadata/review/frozen/
    biota_dir = project.getBiotaDir()        # virttrip/web/

    # Ensure remote directories (virtual trips skip sv1/sv7 which create them)
    sftp.safeMkdir(prefix + bio_web.rstrip("/"))
    sftp.safeMkdir(prefix + bio_web.rstrip("/") + "/biota")
    sftp.safeMkdir(prefix + bio_web.rstrip("/") + "/summaries")
    sftp.safeMkdir(prefix + bio_web.rstrip("/") + "/admin")
    sftp.safeMkdir(prefix + bio_web.rstrip("/") + "/includes web")

    # Upload 'includes web/' (CSS, banner, category images).
    # sv1MakeReposity does this for regular trips; virtual trips skip sv1 so the
    # banner is missing until this step runs.
    dist_includes = Path(__file__).parent.parent / "dist" / "includes web"
    if dist_includes.is_dir():
        sftp.put_r_portable(str(dist_includes),
                            prefix + bio_web.rstrip("/") + "/includes web",
                            preserve_mtime=True)
        logger.info("  Uploaded 'includes web/' (banner/CSS).")

    admin_dir = os.path.join(project.getRepositoryPath(), "admin")
    os.makedirs(admin_dir, exist_ok=True)
    location_cache = os.path.join(admin_dir, "FTPcache.json")
    sftp.openFTPCache(location_cache)

    # 1. Upload species HTML pages + full-size JPGs from frozen/biota/
    frozen_biota = os.path.join(frozen, "biota")
    if os.path.isdir(frozen_biota):
        sftp.put_r_portable(frozen_biota, prefix + bio_web + "biota", preserve_mtime=True)
        logger.info("  Uploaded biota tree (species pages + JPGs).")
    else:
        logger.warning(f"  frozen/biota/ not found — species pages may not have been built: {frozen_biota}")

    # 2. Upload all-species overview pages from web/summaries/
    for lang in ("nl", "en"):
        src = os.path.join(biota_dir, "summaries", f"all biota species-{lang}.html")
        if os.path.isfile(src):
            sftp.cachedUpload(src, prefix + bio_web + f"summaries/all biota species-{lang}.html",
                              preserve_mtime=True)
    logger.info("  Uploaded all-species summary pages.")

    # 3. Resize and upload small JPGs, matching sv7WriteCommit.putRecursiveTreeWeb
    allspecies_csv = os.path.join(frozen, "allspecies.csv")
    uploaded_small = 0
    if os.path.isfile(allspecies_csv):
        with open(allspecies_csv, "r", newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                source    = row[0].strip().strip('"')
                webtarget = row[1].strip().strip('"').replace("\\", "/")
                try:
                    jpgResizeCopy(source)
                    small_src = getSmallJPG(source)
                    small_web = prefix + getSmallJPG(webtarget)
                    if os.path.isfile(small_src):
                        if not sftp.cachedUpload(small_src, small_web, preserve_mtime=True):
                            logger.info(f"  Small JPG: {os.path.basename(small_src)}")
                        uploaded_small += 1
                except Exception as e:
                    logger.warning(f"  Small JPG upload failed for {source}: {e}")
    logger.info(f"  Uploaded {uploaded_small} small JPGs.")

    sftp.closeFTPCache(location_cache)
    sftp.disconnect()

    logger.info("✅ svv4BuildWebsite complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BuildWebsite")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG", "svconfig.ini"))
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"svv4BuildWebsite failed: {e}")
    else:
        logger.info("svv4BuildWebsite succeeded.")
