#!/usr/bin/env python3
"""
svv6RedoBlog — Virtual Trip: recreate blog HTML for every published day.

The virtual-trip analogue of svr9RedoBlog. It re-renders the blog pages that
'Write Virtual Trip Blogs' (svv3) already published, WITHOUT moving any JPGs:

  * complete Previous/Next navigation — svv3 writes each day inside a single
    forward pass, so at render time the later days are not yet in blog_dict and
    NO page ever gets a "Next" link. Here we run two passes: pass 1 loads and
    renders every day just to learn the real blog-day keys and populate
    blog_dict; then Blog.committed_blogdays is set; pass 2 re-renders with the
    full chain known and uploads. So every page links both ways.
  * species-reference hyperlinks in each card (same as a normal render).

Days are discovered exactly like svv3 — the extracted metadata/review/<day>/
directories that have a metadata/blog/blog_<day>.ini — so this does not depend
on blog_admin.csv (which may be missing locally). blog_admin.csv is rewritten
fresh at the end.

Only HTML is rewritten and re-uploaded — the day/<jpg> thumbnails already on the
web are left untouched (setSkipJPGs(True); uploadBlog(sftpPictures=False)).

Requires 'Write Virtual Trip Blogs' (or Edit Blogs) to have created the per-day
blog ini files and 'Extract Days from Master' for the per-day CSVs.
"""
import argparse
import configparser
import io
import logging
import os
import sys
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
import engine.triplib
from engine import SVException
from engine.handling import read_file, read_pictures
from batch.svv3WriteBlog import _read_blog_ini, _register_day_blog

logger = logging.getLogger("speciesviewer")


def _master_collection_counts(master_species_csv):
    """Map ScientificName -> iSpeciesCollectionCount from the master species.csv,
    for every taxon level (species, family, order, ...).

    The per-day load resets all counters (needed so each day's blog only counts
    that day's pictures), which also zeroes iSpeciesCollectionCount. But the blog
    card links a species/family name only when that count is > 0. Restoring the
    master's value after the reset is what makes the reference hyperlinks appear
    — and the master count is the right 'has a reference page' signal, since a
    species shown in the blog exists in the master (and thus in the built site)."""
    counts = {}
    try:
        with io.open(master_species_csv, mode="r", newline="", encoding="utf-8") as f:
            header = f.readline().rstrip("\n").split(";")
            try:
                i_name = header.index("ScientificName")
                i_cnt  = header.index("iSpeciesCollectionCount")
            except ValueError:
                return counts
            for line in f:
                cols = line.rstrip("\n").split(";")
                if len(cols) > max(i_name, i_cnt):
                    try:
                        counts[cols[i_name]] = int(cols[i_cnt])
                    except ValueError:
                        pass
    except OSError:
        pass
    return counts


def _load_day(project, sp, master_species_csv, day_files, day_pics, blog,
              collection_counts=None):
    """Load one day's extracted CSVs into the project — identical to svv3 so the
    rendered cards match a normal write. Returns True if the day has data."""
    project.files_dict             = {}
    project.UniquePictureName_dict = {}
    sp.readSpeciesInfo(master_species_csv, True)

    _, from_time, last_time = read_file(project, str(day_files), silent=True)
    read_pictures(project, str(day_pics))
    blog.from_time = from_time
    blog.last_time = last_time
    if not project.files_dict:
        return False

    # Save master JPG paths before addSpeciesInformation rewrites them.
    _master_best = {
        k: (upn.get("BestJPG", ""), upn.get("BestJPGPathName", ""))
        for k, upn in project.UniquePictureName_dict.items()
    }
    project.addSpeciesInformation(sp, 0)
    for k, upn in project.UniquePictureName_dict.items():
        if not upn.get("BestJPG", ""):
            master_jpg, master_path = _master_best.get(k, ("", ""))
            if master_jpg:
                upn["BestJPG"] = master_jpg
                upn["BestJPGPathName"] = master_path
    project.addPicturesToSpecies(sp, 0, 0)

    # Restore the collection count the per-day reset zeroed, so the blog cards
    # hyperlink species/family reference pages (the point of "recreate").
    if collection_counts:
        for name, spec in sp.species_dict.items():
            spec["iSpeciesCollectionCount"] = collection_counts.get(name, 0)
    return True


def _init_blog_for_day(blog, ini_path):
    """Configure the blog object from a day's ini and set its blog-day key."""
    bc = _read_blog_ini(ini_path)
    blog.initBlog(bc["titel_nl"], bc["titel_en"], bc["message_nl"], bc["message_en"],
                  bc["blogjpg"], bc["species"], bc["only_new"], bc["minimum_score"])
    blog.setBlogDay(blog.getDateFromPictures(0))
    blog.files    = 0
    blog.pictures = 0
    blog.setColumnWidth(bc["column_width"])


def main(args, config):
    virttrip_root = config.get("repository", "BiotaDatabase")
    web_root      = config.get("repository", "BiotaWebRoot")
    master_db     = config.get("master",     "MasterBiotaDatabase", fallback="")

    if not master_db or not os.path.isdir(master_db):
        raise SVException(f"Master database not found: {master_db!r}")

    logger.info(f"svv6RedoBlog: {virttrip_root}")

    project = engine.projectlib.Project(virttrip_root, "", web_root)
    project.initMasterAndTrip(config, inittrip=False)
    master_web = config.get("master", "MasterBiotaWebRoot", fallback="/nature/")
    project.master_biota_web_root = master_web
    if project.Trip is not None:
        project.Trip.master_repository_web = master_web

    if not project.online:
        raise SVException("svv6RedoBlog: blogs cannot be recreated without internet access")

    sc = engine.speciesclassification.SpeciesClassification(project)
    project.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(project)
    project.registerSpecies(sp)

    master_sc_csv      = os.path.join(master_db, "metadata", "active", "speciescollection.csv")
    master_species_csv = os.path.join(master_db, "metadata", "active", "species.csv")
    sc.readSpeciesCollection(master_sc_csv, False)
    collection_counts  = _master_collection_counts(master_species_csv)

    review_root = Path(virttrip_root) / "metadata" / "review"
    blog_dir    = Path(virttrip_root) / "metadata" / "blog"

    # --- discover published days: extracted day dirs that carry a blog ini ---
    # (identical selection to svv3, so the same set of pages is targeted).
    published = [
        d for d in sorted(review_root.iterdir())
        if d.is_dir()
        and (d / "data_files.csv").exists()
        and (blog_dir / f"blog_{d.name}.ini").exists()
    ] if review_root.is_dir() else []
    if not published:
        raise SVException(
            "No day blogs found — run 'Write Virtual Trip Blogs' (or Edit Blogs) first.")

    blog = engine.bloglib.Blog(project)

    def _day_paths(day_dir):
        return (day_dir / "data_files.csv",
                day_dir / "data_pictures.csv",
                blog_dir / f"blog_{day_dir.name}.ini")

    # === PASS 1: render locally (no upload) just to learn every blog-day key ===
    # setBlogDay derives the key from the day's own pictures + ini, so the only
    # reliable way to know the full set up front is to load and render each day.
    blog.blog_dict = {}
    project.setSkipJPGs(True)
    for day_dir in published:
        day_files, day_pics, ini_path = _day_paths(day_dir)
        if not _load_day(project, sp, master_species_csv, day_files, day_pics, blog,
                         collection_counts):
            logger.warning(f"  {day_dir.name}: empty after load — skipping")
            continue
        _init_blog_for_day(blog, ini_path)
        blog.makeRunSummaryPost("biota", "nl")
        blog.makeRunSummaryPost("biota", "en")
        if not blog.BestJPGs:
            logger.info(f"  {day_dir.name}: no pictures above threshold — skipping")
            continue
        _register_day_blog(blog, project)
    project.setSkipJPGs(False)

    if not blog.blog_dict:
        raise SVException("No blogs produced any pictures — nothing to recreate.")

    # The complete forward+backward chain is now known.
    blog.committed_blogdays = set(blog.blog_dict)

    password = config.get("websettings", "Sftppwd", fallback="").strip("'")
    sftp = blog.openSFTP(password)
    admin_dir = os.path.join(project.getRepositoryPath(), "admin")
    os.makedirs(admin_dir, exist_ok=True)
    location_cache = os.path.join(admin_dir, "FTPcache.json")
    if sftp is not None:
        sftp.openFTPCache(location_cache)

        # Ensure the remote trip/blog directories exist. Regular trips get these
        # from sv7WriteCommit; a virtual trip needs them here or every upload
        # silently lands nowhere (blank index / 404). Same guard as svv3.
        prefix   = sftp.remotePrefix()
        bio_web  = project.getBiotaWebRoot()     # e.g. /virttrips/costa-rica/
        blog_web = project.getBiotaBlogWeb()     # e.g. /virttrips/costa-rica/biotablog/
        sftp.safeMkdir(prefix + bio_web.rstrip("/"))
        sftp.safeMkdir(prefix + blog_web.rstrip("/"))
        sftp.safeMkdir(prefix + bio_web.rstrip("/") + "/admin")
        logger.info(f"Remote directories ensured: {blog_web}")

    # === PASS 2: re-render with the full nav known, upload HTML only ===
    recreated = 0
    project.setSkipJPGs(True)
    for day_dir in published:
        day_files, day_pics, ini_path = _day_paths(day_dir)
        if not _load_day(project, sp, master_species_csv, day_files, day_pics, blog,
                         collection_counts):
            continue
        _init_blog_for_day(blog, ini_path)
        if blog.getBlogDay() not in blog.committed_blogdays:
            continue
        blog.makeRunSummaryPost("biota", "nl")
        blog.makeRunSummaryPost("biota", "en")
        if not blog.BestJPGs:
            continue
        blog.blog_dict[blog.getBlogDay()]["BestJPGs"] = list(blog.BestJPGs)
        blog.uploadBlog(sftp, sftpPictures=False)
        logger.info(f"  {day_dir.name}: recreated ({len(blog.BestJPGs)} pictures, HTML only)")
        recreated += 1
    project.setSkipJPGs(False)

    if recreated == 0:
        raise SVException("No blogs were recreated.")

    # Drop any day that lost all its pictures so the index stays clean.
    blog.blog_dict = {k: v for k, v in blog.blog_dict.items() if v.get("BestJPGs")}

    blog.updateIndexPage()
    blog.uploadBlogIndex(sftp)
    blog.write_BlogAdmin()
    blog.uploadBlogAdmin(sftp)
    if sftp is not None:
        sftp.closeFTPCache(location_cache)
        sftp.disconnect()

    if project.ct:
        project.ct.writeTranslationCache()

    blog_index_url = project.getBiotaBlogWeb() + "blog index-nl.html"
    with open("last_blog_url.txt", "w") as f:
        f.write(blog_index_url)

    logger.info(f"✅ svv6RedoBlog complete: {recreated} blog(s) recreated (HTML only).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svv6RedoBlog")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG", "svconfig.ini"))
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"svv6RedoBlog failed: {e}")
    else:
        logger.info("svv6RedoBlog succeeded.")
