#!/usr/bin/env python3
"""
svv3WriteBlog — Virtual Trip: write blog posts for every day that has data.

For each day directory in <virttrip>/metadata/review/<YYYY-MM-DD>/:
  1. Auto-creates <virttrip>/metadata/blog/blog_YYYY-MM-DD.ini if absent
     (title + auto-text from species/location in that day's data_pictures.csv).
  2. Reads the (auto-created or hand-edited) ini.
  3. Loads only that day's CSVs into the project, rebuilds species counters,
     and generates + uploads blog HTML (nl + en) and slider pages.

After all days: writes blog_admin.csv, regenerates blog index, uploads both.

Re-running: skips ini creation if the file exists (preserving edits);
always regenerates and re-uploads HTML.
"""
import argparse
import configparser
import csv
import io
import logging
import os
import sys
from collections import Counter
from datetime import datetime
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

logger = logging.getLogger("speciesviewer")


# ---------------------------------------------------------------------------
# Blog INI helpers
# ---------------------------------------------------------------------------

def _read_day_pictures_raw(day_dir: Path) -> list:
    """Read data_pictures.csv from a day directory as plain dicts."""
    pics_csv = day_dir / "data_pictures.csv"
    if not pics_csv.exists():
        return []
    with pics_csv.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _generate_blog_ini(ini_path: Path, day_dir: Path, day_num: int) -> None:
    """Auto-generate a blog ini from the day's data_pictures.csv.
    Only called when the ini file does not yet exist."""
    rows = _read_day_pictures_raw(day_dir)
    if not rows:
        return

    # --- location ---
    cities  = [r.get("City",  "").strip() for r in rows if r.get("City",  "").strip()]
    states  = [r.get("State", "").strip() for r in rows if r.get("State", "").strip()]
    city  = Counter(cities).most_common(1)[0][0]  if cities  else ""
    state = Counter(states).most_common(1)[0][0]  if states  else ""

    loc_nl = f"{city}, {state}" if (city and state and state != city) else (city or "onbekend")
    loc_en = f"{city}, {state}" if (city and state and state != city) else (city or "unknown")

    # --- species count ---
    sp_nl = {r.get("NLSoortNaam",  "").strip() for r in rows if r.get("NLSoortNaam",  "").strip()}
    sp_en = {r.get("EngSoortNaam", "").strip() for r in rows if r.get("EngSoortNaam", "").strip()}
    n_species = max(len(sp_nl), len(sp_en))

    # --- top-2 species by Rate ---
    best: dict = {}   # scientific → (rate, nl_name, en_name)
    for r in rows:
        wet = r.get("WetSoortNaam", "").strip()
        if not wet:
            continue
        try:
            rate = float(r.get("Rate", "0") or "0")
        except ValueError:
            rate = 0.0
        if rate > best.get(wet, (-1, "", ""))[0]:
            best[wet] = (rate, r.get("NLSoortNaam", "").strip(), r.get("EngSoortNaam", "").strip())

    top2   = sorted(best.values(), reverse=True)[:2]
    t_nl   = [t[1] or t[2] for t in top2]
    t_en   = [t[2] or t[1] for t in top2]

    if len(t_nl) >= 2:
        phrase_nl = f"De foto's van {t_nl[0]} en {t_nl[1]} kwamen er mooi uit."
        phrase_en = f"The photos of {t_en[0]} and {t_en[1]} came out nicely."
    elif len(t_nl) == 1:
        phrase_nl = f"De foto's van {t_nl[0]} kwamen er mooi uit."
        phrase_en = f"The photos of {t_en[0]} came out nicely."
    else:
        phrase_nl = ""
        phrase_en = ""

    title_nl = f"Dag {day_num}: {loc_nl}"
    title_en = f"Day {day_num}: {loc_en}"
    msg_nl   = f"We zagen {n_species} soorten in {city or loc_nl}. {phrase_nl}".strip()
    msg_en   = f"We saw {n_species} species in {city or loc_en}. {phrase_en}".strip()

    text = (
        "[blog_config]\n"
        "only_new = False\n"
        "minimum_score = 25\n"
        "column_width = 300\n"
        "\n"
        "[blog_selection]\n"
        "species = biota\n"
        "\n"
        "[blog_text]\n"
        f"titel_nl = {title_nl}\n"
        f"message_nl = {msg_nl}\n"
        f"titel_en = {title_en}\n"
        f"message_en = {msg_en}\n"
    )
    ini_path.write_text(text, encoding="utf-8")
    logger.info(f"  Created {ini_path.name}")


def _read_blog_ini(ini_path: Path) -> dict:
    """Read a blog ini file and return a flat dict of relevant keys."""
    cfg = configparser.RawConfigParser()
    try:
        cfg.read(str(ini_path), encoding="utf-8")
    except UnicodeDecodeError:
        cfg.read(str(ini_path), encoding="latin-1")

    def _u8(section, key, fallback=""):
        val = cfg.get(section, key, fallback=fallback)
        try:
            val = val.encode("latin1").decode("utf-8")
        except Exception:
            pass
        val = val.replace("<female>", "♀").replace("<male>", "♂")
        return val

    return {
        "only_new":      cfg.getboolean("blog_config", "only_new",     fallback=False),
        "minimum_score": cfg.getint    ("blog_config", "minimum_score", fallback=25),
        "column_width":  cfg.getint    ("blog_config", "column_width",  fallback=300),
        "species":       cfg.get       ("blog_selection", "species",    fallback="biota"),
        "blogjpg":       _u8("blog_text", "blogjpg"),
        "titel_nl":      _u8("blog_text", "titel_nl"),
        "message_nl":    _u8("blog_text", "message_nl"),
        "titel_en":      _u8("blog_text", "titel_en"),
        "message_en":    _u8("blog_text", "message_en"),
    }


# ---------------------------------------------------------------------------
# Per-day blog entry helper (avoids the class-level new_blog_row alias bug)
# ---------------------------------------------------------------------------

def _register_day_blog(blog, project) -> None:
    """Register the current blog day into blog_dict after makeRunSummaryPost.

    Must be called AFTER both makeRunSummaryPost("biota","nl") and ("biota","en")
    so that blog.BestJPGs is already populated.  blog.setBlogDay() must have
    been called before makeRunSummaryPost (it sets the day dir/filename names).

    Avoids the class-level new_blog_row aliasing issue in prepareBlogCreation."""
    blogday = blog.getBlogDay()   # already set before makeRunSummaryPost was called

    entry = {
        "blog":            blogday,
        "title_nl":        blog.TitleNl,
        "title_en":        blog.TitleEn,
        "message_nl":      blog.MessageNl,
        "message_en":      blog.MessageEn,
        "blogjpg":         blog.blogjpg,
        "Project":         project.ProjectName(),
        "CreatedTime":     datetime.today().strftime("%Y-%m-%d %H-%M"),
        "SelectedGroup":   blog.SelectedSpeciesGroup,
        "OnlyNewSpecies":  blog.OnlyNewSpecies,
        "MinimumScore":    blog.MinimumScore,
        "BestJPGs":        list(blog.BestJPGs),   # populated now
    }

    if blog.existBlog(blogday):
        existing = blog.getBlog(blogday)
        existing.update(entry)
    else:
        blog.addBlog(entry)


# ---------------------------------------------------------------------------
# Debug helper
# ---------------------------------------------------------------------------

def _debug_ratings(project, sp, day_label: str) -> None:
    """Log rating diagnostics to help trace why BestJPGs stays empty."""
    upn_dict  = project.UniquePictureName_dict
    file_dict = project.files_dict

    # 1. UPN-level rates
    rates = [int(p.get("Rate", 0) or 0) for p in upn_dict.values()]
    nonzero = [r for r in rates if r > 0]
    logger.info(f"  DBG {day_label}: {len(upn_dict)} UPNs, "
                f"{len(nonzero)} with Rate>0 "
                f"(max={max(rates) if rates else 0})")

    # 2. Sample the first UPN's Files list vs files_dict presence
    for upn_name, upn in list(upn_dict.items())[:3]:
        files = upn.get("Files", [])
        present = [f for f in files if f in file_dict]
        missing = [f for f in files if f not in file_dict]
        xmp_present = [f for f in present
                       if file_dict[f].get("WindowsExtension","").lower() == "xmp"]
        xmp_ratings  = [file_dict[f].get("XMP:Rating","?") for f in xmp_present]
        logger.info(f"    UPN {upn_name}: Files={files}, "
                    f"inDict={len(present)}/{len(files)}, "
                    f"missing={missing}, "
                    f"xmpRatings={xmp_ratings}, "
                    f"Rate={upn.get('Rate','?')}")

    # 3. Species currentHighestRating
    sp_rated = [(n, d.get("currentHighestRating", 0))
                for n, d in sp.species_dict.items()
                if d.get("currentHighestRating", 0) > 0]
    logger.info(f"  DBG {day_label}: {len(sp_rated)} species with currentHighestRating>0")
    for name, r in sorted(sp_rated, key=lambda x: -x[1])[:5]:
        logger.info(f"    species {name}: rating={r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(args, config):
    # Optional minimum_score override from the dialog (injected via extra_config)
    min_score_override = config.getint("blogconfig", "minimum_score", fallback=None)

    virttrip_root = config.get("repository", "BiotaDatabase")
    web_root      = config.get("repository", "BiotaWebRoot")
    master_db     = config.get("master",     "MasterBiotaDatabase", fallback="")

    if not master_db or not os.path.isdir(master_db):
        raise SVException(f"Master database not found: {master_db!r}")

    logger.info(f"svv3WriteBlog: {virttrip_root}")

    project = engine.projectlib.Project(virttrip_root, "", web_root)
    project.initMasterAndTrip(config, inittrip=False)
    master_web = config.get("master", "MasterBiotaWebRoot", fallback="/nature/")
    project.master_biota_web_root = master_web
    if project.Trip is not None:
        project.Trip.master_repository_web = master_web

    if not project.online:
        raise SVException("svv3WriteBlog: blog cannot be created without internet access")

    sc = engine.speciesclassification.SpeciesClassification(project)
    project.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(project)
    project.registerSpecies(sp)

    master_sc_csv      = os.path.join(master_db, "metadata", "active", "speciescollection.csv")
    master_species_csv = os.path.join(master_db, "metadata", "active", "species.csv")
    sc.readSpeciesCollection(master_sc_csv, False)

    # --- discover day directories ---
    review_root = Path(virttrip_root) / "metadata" / "review"
    if not review_root.is_dir():
        raise SVException("No review directory found — run 'Extract Days from Master' first.")

    day_dirs = sorted(d for d in review_root.iterdir() if d.is_dir()
                      and (d / "data_files.csv").exists())
    if not day_dirs:
        raise SVException("No day data found — run 'Extract Days from Master' first.")

    # --- ensure blog directory and auto-create missing ini files ---
    blog_dir = Path(virttrip_root) / "metadata" / "blog"
    blog_dir.mkdir(parents=True, exist_ok=True)

    for idx, day_dir in enumerate(day_dirs, start=1):
        ini_path = blog_dir / f"blog_{day_dir.name}.ini"
        if not ini_path.exists():
            _generate_blog_ini(ini_path, day_dir, idx)

    # --- set up blog object and read existing admin once ---
    password = config.get("websettings", "Sftppwd", fallback="").strip("'")
    blog = engine.bloglib.Blog(project)
    blog.blog_dict = {}          # ensure fresh instance dict (avoid class-level sharing)
    blog.read_BlogAdmin()
    sftp = blog.openSFTP(password)

    # Open FTP cache BEFORE the day loop — uploadBlog → put_r_portable →
    # cachedUpload all access self.openFTPCache, which is the method itself
    # until openFTPCache() is called and replaces it with a dict.
    admin_dir = os.path.join(project.getRepositoryPath(), "admin")
    os.makedirs(admin_dir, exist_ok=True)
    location_cache = os.path.join(admin_dir, "FTPcache.json")
    if sftp is not None:
        sftp.openFTPCache(location_cache)

    # Ensure the remote trip directories exist.  Regular trips get these created
    # by sv7WriteCommit; virtual trips need them on first blog upload.
    if sftp is not None:
        prefix   = sftp.remotePrefix()
        bio_web  = project.getBiotaWebRoot()    # e.g. /trips/Tucson_2023/
        blog_web = project.getBiotaBlogWeb()    # e.g. /trips/Tucson_2023/biotablog/
        sftp.safeMkdir(prefix + bio_web.rstrip("/"))
        sftp.safeMkdir(prefix + blog_web.rstrip("/"))
        sftp.safeMkdir(prefix + bio_web.rstrip("/") + "/admin")
        logger.info(f"Remote directories ensured: {bio_web}")

    processed = 0
    for idx, day_dir in enumerate(day_dirs, start=1):
        ini_path = blog_dir / f"blog_{day_dir.name}.ini"
        if not ini_path.exists():
            logger.warning(f"  {day_dir.name}: no blog ini — skipping")
            continue

        logger.info(f"  Processing day {idx}/{len(day_dirs)}: {day_dir.name}")

        # Reset per-day project data
        project.files_dict             = {}
        project.UniquePictureName_dict = {}

        # Reset species counters so only this day's pictures contribute
        sp.readSpeciesInfo(master_species_csv, True)

        # Load this day's data; capture time range for the blog's picture filter
        _, from_time, last_time = read_file(project, str(day_dir / "data_files.csv"), silent=True)
        read_pictures(project, str(day_dir / "data_pictures.csv"))

        # collectBestJPGs filters by Blog.from_time / Blog.last_time.
        # Without these, last_time == "" so every DateTimeOriginal comparison
        # fails and no pictures are selected for the blog.
        blog.from_time = from_time
        blog.last_time = last_time

        if not project.files_dict:
            logger.warning(f"  {day_dir.name}: empty after load — skipping")
            continue

        # Save master BestJPG/BestJPGPathName before addSpeciesInformation
        # overwrites them with the virtual trip's target path.  For virtual
        # trips the per-day data_pictures.csv is a direct copy of master rows,
        # so these fields point at the actual sorted JPGs in the master.
        _master_best = {
            k: (upn.get("BestJPG", ""), upn.get("BestJPGPathName", ""))
            for k, upn in project.UniquePictureName_dict.items()
        }

        # Build species info for this day
        project.addSpeciesInformation(sp, 0)

        # Restore master JPG paths for UPNs where no JPG was found in
        # files_dict (RAW-only UPNs have no in-camera or DxO JPG in the
        # per-day data_files.csv, so addSpeciesInformation leaves BestJPG="").
        for k, upn in project.UniquePictureName_dict.items():
            if not upn.get("BestJPG", ""):
                master_jpg, master_path = _master_best.get(k, ("", ""))
                if master_jpg:
                    upn["BestJPG"] = master_jpg
                    upn["BestJPGPathName"] = master_path

        project.addPicturesToSpecies(sp, 0, 0)

        # DEBUG: diagnose why pictures may be excluded from the blog
        _debug_ratings(project, sp, day_dir.name)

        # Skip days with no recognized species (e.g. RAW-only with missing tags)
        species_count = sum(
            1 for p in project.UniquePictureName_dict.values()
            if p.get("WetSoortNaam", "")
        )
        if species_count == 0:
            logger.info(f"  {day_dir.name}: 0 species recognized — skipping")
            continue

        today = blog.getDateFromPictures(0)

        # Read blog ini; allow dialog to override minimum_score for this run
        bc = _read_blog_ini(ini_path)
        if min_score_override is not None:
            bc["minimum_score"] = min_score_override

        # initBlog must run first — it sets OnlyNewSpecies, which setBlogDay uses
        # to build the blogday key.  setBlogDay must run before makeRunSummaryPost,
        # which reads getBlogDay() for the output directory and HTML filename.
        blog.initBlog(
            bc["titel_nl"], bc["titel_en"],
            bc["message_nl"], bc["message_en"],
            bc["blogjpg"],
            bc["species"],
            bc["only_new"],
            bc["minimum_score"],
        )
        blog.setBlogDay(today)   # OnlyNewSpecies is now set, so this is safe
        blog.files    = 0
        blog.pictures = 0
        blog.setColumnWidth(bc["column_width"])

        # Generate HTML — this populates blog.BestJPGs via addOneSpeciesJPG
        blog.makeRunSummaryPost("biota", "nl")
        project.setSkipJPGs(True)
        blog.makeRunSummaryPost("biota", "en")
        project.setSkipJPGs(False)

        # Register AFTER summary posts so BestJPGs is captured correctly
        _register_day_blog(blog, project)

        blog.uploadBlog(sftp)
        logger.info(f"  {day_dir.name}: uploaded ({blog.getBlogDay()}, {len(blog.BestJPGs)} pictures)")
        processed += 1

    if processed == 0:
        raise SVException("No days were successfully processed.")

    # --- finalize ---
    blog.write_BlogAdmin()
    blog.updateIndexPage()

    blog.uploadBlogIndex(sftp)
    blog.uploadBlogAdmin(sftp)
    sftp.closeFTPCache(location_cache)
    sftp.disconnect()

    if project.ct:
        project.ct.writeTranslationCache()

    # Write the blog index URL so the GUI can open it in a browser after completion.
    blog_index_url = project.getBiotaBlogWeb() + "blog index-nl.html"
    with open("last_blog_url.txt", "w") as f:
        f.write(blog_index_url)

    logger.info(f"✅ svv3WriteBlog complete: {processed} day(s) published.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svv3WriteBlog")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG", "svconfig.ini"))
    args = parser.parse_args()
    config = configparser.ConfigParser()
    config.read(args.config)
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"svv3WriteBlog failed: {e}")
    else:
        logger.info("svv3WriteBlog succeeded.")
