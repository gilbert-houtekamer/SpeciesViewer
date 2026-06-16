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
import random
import re
import sys
from collections import Counter
from datetime import date, datetime
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


_UNNAMED_ROADS = {'Vía Sin Nombre', 'Route sans nom', 'Unnamed Road'}


def _cap_name(name: str) -> str:
    """Capitalize the first letter of each space-separated word.

    Safer than str.title() which breaks apostrophes ("Cooper'S Hawk").
    Hyphens within a word are left as-is ("White-crowned Sparrow" → kept).
    """
    return ' '.join(w[0].upper() + w[1:] if w else '' for w in name.split(' '))


def _day_location_label(rows: list) -> tuple:
    """Return (loc_nl, loc_en) for a day, preferring XMP:Location (sublocation)
    over City/State.

    - Single distinct sublocation: "<Subloc>, <City>" (or just "<Subloc>" if city
      is the same or absent).
    - Two distinct top sublocations: "<Subloc1> en <Subloc2>" / "… and …".
    - No usable sublocation: "<City>, <State>" or "<City>" or "onbekend"/"unknown".

    Sublocations that duplicate the city/state or are unnamed-road placeholders
    are filtered out.
    """
    cities = [r.get("City",  "").strip() for r in rows if r.get("City",  "").strip()]
    states = [r.get("State", "").strip() for r in rows if r.get("State", "").strip()]
    city  = Counter(cities).most_common(1)[0][0] if cities else ""
    state = Counter(states).most_common(1)[0][0] if states else ""

    raw_locs = [r.get("Location", "").strip() for r in rows]
    valid_locs = [l for l in raw_locs
                  if l and l not in _UNNAMED_ROADS and l != city and l != state]

    if not valid_locs:
        if city and state and state != city:
            return f"{city}, {state}", f"{city}, {state}"
        return city or "onbekend", city or "unknown"

    top = [l for l, _ in Counter(valid_locs).most_common(2)]
    if len(top) == 1:
        loc = top[0]
        suffix = f", {city}" if city and city.lower() != loc.lower() else ""
        return f"{loc}{suffix}", f"{loc}{suffix}"
    return f"{top[0]} en {top[1]}", f"{top[0]} and {top[1]}"


def _generate_blog_ini(ini_path: Path, day_num: int, rows: list,
                       loc_nl: str, loc_en: str) -> None:
    """Auto-generate a blog ini from pre-read picture rows.
    Only called when the ini file does not yet exist."""
    if not rows:
        return

    # Top sublocation(s) for opening phrase (raw, without city suffix)
    raw_locs = [r.get("Location", "").strip() for r in rows]
    valid_locs_all = [l for l in raw_locs if l and l not in _UNNAMED_ROADS]
    top_locs = [l for l, _ in Counter(valid_locs_all).most_common(2)]
    cities = [r.get("City", "").strip() for r in rows if r.get("City", "").strip()]
    city   = Counter(cities).most_common(1)[0][0] if cities else ""
    loc1   = top_locs[0] if top_locs else (city or loc_nl)

    # Species count
    sp_nl = {r.get("NLSoortNaam",  "").strip() for r in rows if r.get("NLSoortNaam",  "").strip()}
    sp_en = {r.get("EngSoortNaam", "").strip() for r in rows if r.get("EngSoortNaam", "").strip()}
    n_species = max(len(sp_nl), len(sp_en))

    # Best rate per scientific name
    best: dict = {}   # sci → (rate, nl_name, en_name)
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

    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])  # [(sci,(rate,nl,en)), …]
    top_score = ranked[0][1][0] if ranked else 0

    # Quiet-day: very few species or nothing photographed well
    quiet = n_species < 3 or top_score < 20

    # Deterministic variety: each day date seeds a different template pick
    rng = random.Random(ini_path.stem)

    # --- Opening sentence ---
    if len(top_locs) >= 2:
        loc2     = top_locs[1]
        open_nl  = f"{loc1} en {loc2} leverden samen {n_species} soorten op."
        open_en  = f"{loc1} and {loc2} together yielded {n_species} species."
    elif quiet:
        open_nl  = f"Een rustige dag in {loc1}: {n_species} soorten genoteerd."
        open_en  = f"A quiet day at {loc1}: {n_species} species recorded."
    else:
        pool_nl  = [
            f"Een dag in {loc1} leverde {n_species} soorten op.",
            f"Vandaag telden we {n_species} soorten in {loc1}.",
            f"We bezochten {loc1} en noteerden {n_species} soorten.",
        ]
        pool_en  = [
            f"A day at {loc1} yielded {n_species} species.",
            f"Today we counted {n_species} species in {loc1}.",
            f"We visited {loc1} and recorded {n_species} species.",
        ]
        idx      = rng.randrange(len(pool_nl))
        open_nl  = pool_nl[idx]
        open_en  = pool_en[idx]

    # --- Species callout ---
    def _fmt_nl(sci: str, nl: str, en: str) -> str:
        name  = _cap_name(nl) if nl else (_cap_name(en) if en else None)
        sci_i = f"<i>{sci.capitalize()}</i>"
        return f"de {name} ({sci_i})" if name else sci_i

    def _fmt_en(sci: str, nl: str, en: str) -> str:
        name  = _cap_name(en) if en else (_cap_name(nl) if nl else None)
        sci_i = f"<i>{sci.capitalize()}</i>"
        return f"{name} ({sci_i})" if name else sci_i

    note_nl = note_en = ""
    if not quiet and ranked:
        sci1, (r1, nl1, en1) = ranked[0]
        r2 = ranked[1][1][0] if len(ranked) >= 2 else -1

        # One clear winner: score ≥ 85 and at least 10 pts above #2
        standout = r1 >= 85 and (r2 < 0 or r1 - r2 >= 10)
        # Two tied at the top: exact same high score (≥ 80)
        two_tied = not standout and len(ranked) >= 2 and r1 == r2 and r1 >= 80

        if standout:
            note_nl = f"Uitblinker van de dag: {_fmt_nl(sci1, nl1, en1)}, score {r1:.0f}."
            note_en = f"Standout of the day: {_fmt_en(sci1, nl1, en1)}, score {r1:.0f}."
        elif two_tied:
            sci2, (_, nl2, en2) = ranked[1]
            note_nl = (f"Hoogtepunt: {_fmt_nl(sci1, nl1, en1)} en "
                       f"{_fmt_nl(sci2, nl2, en2)}, beide met score {r1:.0f}.")
            note_en = (f"Highlight: {_fmt_en(sci1, nl1, en1)} and "
                       f"{_fmt_en(sci2, nl2, en2)}, both scoring {r1:.0f}.")
        elif len(ranked) >= 2:
            sci2, (_, nl2, en2) = ranked[1]
            note_nl = f"Mooiste opnames: {_fmt_nl(sci1, nl1, en1)} en {_fmt_nl(sci2, nl2, en2)}."
            note_en = f"Best shots: {_fmt_en(sci1, nl1, en1)} and {_fmt_en(sci2, nl2, en2)}."
        else:
            note_nl = f"Mooiste opname: {_fmt_nl(sci1, nl1, en1)}."
            note_en = f"Best shot: {_fmt_en(sci1, nl1, en1)}."

    msg_nl = " ".join(x for x in [open_nl, note_nl, "(auto-tekst)"] if x)
    msg_en = " ".join(x for x in [open_en, note_en, "(auto-generated)"] if x)

    title_nl = f"Dag {day_num}: {loc_nl}"
    title_en = f"Day {day_num}: {loc_en}"

    try:
        from utils.config import _config as _sv_config
        default_score = int(_sv_config.get("virttrip", "minimum_score", fallback="25"))
    except Exception:
        default_score = 25

    text = (
        "[blog_config]\n"
        "only_new = False\n"
        f"minimum_score = {default_score}\n"
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


def _patch_ini_titles(ini_path: Path, day_num: int, loc_nl: str, loc_en: str) -> None:
    """Update the full 'Dag N: <loc>' / 'Day N: <loc>' title lines in existing ini files.

    Only touches lines that match the auto-generated 'Dag N:' / 'Day N:' pattern,
    so hand-edited titles (no numbered-day prefix) are left untouched.
    """
    try:
        text = ini_path.read_text(encoding="utf-8")
        patched = re.sub(r'(?m)^(titel_nl\s*=\s*)Dag \d+:.*$',
                         rf'\g<1>Dag {day_num}: {loc_nl}', text)
        patched = re.sub(r'(?m)^(titel_en\s*=\s*)Day \d+:.*$',
                         rf'\g<1>Day {day_num}: {loc_en}', patched)
        if patched != text:
            ini_path.write_text(patched, encoding="utf-8")
    except Exception as e:
        logger.warning(f"  Could not patch titles in {ini_path.name}: {e}")


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

    first_date = date.fromisoformat(day_dirs[0].name)

    for day_dir in day_dirs:
        ini_path = blog_dir / f"blog_{day_dir.name}.ini"
        day_num  = (date.fromisoformat(day_dir.name) - first_date).days + 1
        rows     = _read_day_pictures_raw(day_dir)
        loc_nl, loc_en = _day_location_label(rows)
        if not ini_path.exists():
            _generate_blog_ini(ini_path, day_num, rows, loc_nl, loc_en)
        else:
            _patch_ini_titles(ini_path, day_num, loc_nl, loc_en)

    # --- set up blog object and read existing admin once ---
    password = config.get("websettings", "Sftppwd", fallback="").strip("'")
    blog = engine.bloglib.Blog(project)
    blog.read_BlogAdmin()
    blog.blog_dict = {}          # discard stale entries — rebuild fully from this run
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

        # Skip days with no recognized species (e.g. RAW-only with missing tags)
        species_count = sum(
            1 for p in project.UniquePictureName_dict.values()
            if p.get("WetSoortNaam", "")
        )
        if species_count == 0:
            logger.info(f"  {day_dir.name}: 0 species recognized — skipping")
            continue

        # Issue 1: skip days where no UPN has a renderable JPEG (RAW-only with no
        # DxO/Lightroom export).  makeRunSummaryPost would produce a blank page.
        has_any_jpg = any(
            upn.get("BestJPG", "") for upn in project.UniquePictureName_dict.values()
        )
        if not has_any_jpg:
            logger.warning(f"  {day_dir.name}: no JPEG files found — skipping blog page")
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

        # Issue 2: if all species were below the score threshold, BestJPGs is empty.
        # Don't lower the threshold — warn and skip so no blank page is published.
        if not blog.BestJPGs:
            blog.logExcludedSpecies()
            below = len(blog.excluded_species.get("below_score", []))
            logger.warning(
                f"  {day_dir.name}: {below} species below score threshold "
                f"({bc['minimum_score']}) — skipping blog page"
            )
            continue

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
