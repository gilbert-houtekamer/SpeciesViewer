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


def _seed_empty_kingdom_stubs(project) -> int:
    """Create a stub node page for each top-level kingdom that is present in
    the master species collection but has no pictures in this virtual trip.

    makeWebDirectories("biota", "New") skips kingdoms with iPicturesWithJPGsThisRun==0,
    so e.g. fungi/fungi-nl.html is never written for a trip without fungi.
    The all-species summary still links to it (from the collection data),
    causing a 404.  A stub page — showing 0 species for this trip — avoids that.

    A stub is created for every direct kingdom child of "biota" with zero
    trip pictures — no collection-count check, because the all-species summary
    links to all kingdoms in the classification tree regardless of master photos.

    Must be called AFTER makeWebDirectories so AllOrNew is already "New"
    (makeWebLevel uses getBiotaAllorNewDir() which maps "New" → frozen/).
    """
    biota = project.sp.species_dict.get("biota")
    if biota is None:
        return 0
    count = 0
    for leaf in sorted(biota.get("Leaves", [])):
        sp = project.sp.species_dict.get(leaf)
        if sp is None:
            continue
        # Seed a stub for every top-level kingdom that has no trip pictures.
        # No collection-count check: the all-species summary links to all kingdoms
        # in the classification tree regardless of whether the master has photos,
        # so we need a page even for kingdoms with iPicturesWithJPGsCollection==0.
        if int(sp.get("iPicturesWithJPGsThisRun", 0)) == 0:
            project.makeWebLevel(sp, "New")
            count += 1
    return count


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


def _read_species_best_picks(species_csv: str) -> dict:
    """Map ScientificName -> {BestJPG, BestUPN, BestJPGPathName, TargetPath} from a
    species.csv. Used to restore the master's per-species best-pick after the
    reset-load blanks it, so the all-biota grid can still show species whose JPG
    file is not in the virtual-trip per-day extract (the image is then referenced
    from the shared master web tree by writeOneSpecies)."""
    best = {}
    if not os.path.isfile(species_csv):
        return best
    with open(species_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            if r.get("Level") != "Level.SPECIES":
                continue
            name = (r.get("ScientificName") or "").strip()
            if name and (r.get("BestJPG") or "").strip():
                best[name] = {
                    "BestJPG": r["BestJPG"].strip(),
                    "BestUPN": (r.get("BestUPN") or "").strip(),
                    "BestJPGPathName": (r.get("BestJPGPathName") or "").strip(),
                    "TargetPath": (r.get("TargetPath") or "").strip(),
                }
    return best


def _restore_master_best_picks(sp, master_best: dict) -> int:
    """For species the recompute left without a best-pick (their JPG file was not
    in the virtual-trip extract) but which DO have pictures this run, restore the
    master's best-pick. Without this the all-biota grid suppresses the card
    (counter > 0 but BestJPG empty). The image then resolves against the shared
    master web tree in writeOneSpecies. Returns the number restored."""
    restored = 0
    for name, spec in sp.species_dict.items():
        if spec.get("BestJPG"):
            continue
        mb = master_best.get(name)
        if not mb:
            continue
        thisrun = int(spec.get("iPicturesWithJPGsThisRun", 0) or 0)
        coll = int(spec.get("iPicturesWithJPGsCollection", 0) or 0)
        if (thisrun + coll) <= 0:
            continue
        spec["BestJPG"] = mb["BestJPG"]
        spec["BestUPN"] = mb["BestUPN"]
        spec["BestJPGPathName"] = mb["BestJPGPathName"]
        if not spec.get("TargetPath"):
            spec["TargetPath"] = mb["TargetPath"]
        restored += 1
    return restored


def main(args, config):
    virttrip_root = config.get("repository", "BiotaDatabase")
    web_root      = config.get("repository", "BiotaWebRoot")
    master_db     = config.get("master", "MasterBiotaDatabase", fallback="")

    if not master_db or not os.path.isdir(master_db):
        raise SVException(f"Master database not found: {master_db!r}")

    logger.info(f"svv4BuildWebsite: {virttrip_root}")

    project = engine.projectlib.Project(virttrip_root, "", web_root)
    project.initMasterAndTrip(config, inittrip=False)
    # Load the trip registry (trip_banner/trip_picture) read-only from the master
    # trip_admin.csv so the banner token resolves to the trip's own banner instead
    # of the keerkringvogel default. inittrip=True is unsafe for a virttrip: it has
    # no trip.ini, so readTrip() fabricates keerkringvogel defaults and writes them
    # back into the master trip_admin.csv (corrupting the real row).
    if project.Trip is not None:
        project.Trip.read_TripAdmin()
        # Fallback for a retroactive virttrip not yet added to master: seed its
        # own banner/picture from virttrip.ini so the banner still resolves.
        from utils.virttrip_config import read_virttrip_ini
        project.Trip.ensureTripRegistered(read_virttrip_ini(virttrip_root))
    master_web = config.get("master", "MasterBiotaWebRoot", fallback="/nature/")
    project.master_biota_web_root = master_web
    if project.Trip is not None:
        project.Trip.master_repository_web = master_web

    sc = engine.speciesclassification.SpeciesClassification(project)
    project.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(project)
    project.registerSpecies(sp)

    master_sc_csv      = os.path.join(master_db, "metadata", "active", "speciescollection.csv")
    master_species_csv = os.path.join(master_db, "metadata", "active", "species.csv")
    sc.readSpeciesCollection(master_sc_csv, False)
    # Capture the master's per-species best-pick BEFORE the reset-load below
    # blanks BestJPG (reset_all_counters=True), so RAW-only-in-extract species
    # can be restored after the recompute (see _restore_master_best_picks).
    master_best = _read_species_best_picks(master_species_csv)
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

    # Restore master best-picks for species whose JPG file was not in the extract
    # (writeOneSpecies will reference these from the shared master web tree).
    restored = _restore_master_best_picks(sp, master_best)
    if restored:
        logger.info(f"  Restored master best-pick for {restored} species not in the local extract.")

    # Reset the all-species file tracking before building (class-level dict)
    project.resetAllSpeciesFiles()

    # Build web pages in both languages.
    # Use "New" so species HTML lands in ReviewFrozen() alongside the JPG copies,
    # enabling a single put_r_portable upload instead of two separate trees.
    for language in ("nl", "en"):
        project.setLanguage(language)
        project.makeWebDirectories("biota", "New")
        seeded = _seed_empty_kingdom_stubs(project)
        if seeded:
            logger.info(f"  Seeded {seeded} kingdom stub page(s) for kingdoms not in this trip ({language.upper()}).")
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

    admin_dir = os.path.join(project.getRepositoryPath(), "admin")
    os.makedirs(admin_dir, exist_ok=True)
    location_cache = os.path.join(admin_dir, "FTPcache.json")
    sftp.openFTPCache(location_cache)

    # Upload 'includes web/' (CSS, banner, category images).
    # sv1MakeReposity does this for regular trips; virtual trips skip sv1 so the
    # banner is missing until this step runs.
    dist_includes = Path(__file__).parent.parent / "dist" / "includes web"
    if dist_includes.is_dir():
        sftp.put_r_portable(str(dist_includes),
                            prefix + bio_web.rstrip("/") + "/includes web",
                            preserve_mtime=True)
        logger.info("  Uploaded 'includes web/' (banner/CSS).")

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
