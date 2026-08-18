#!/usr/bin/env python3
"""
svrRebuildAllWebsites — re-render EVERY album's website (and optionally the
master's) with the current templates and code.

WHY
The site is generated. When a template, a token or a bit of render code is
fixed, nothing on the server changes until each site is rebuilt — and the
existing tools rebuild exactly one thing: the ACTIVE album, or the master. After
a fix like "%%90%% resolved to the wrong web root" or "the English blog index
said Titel", there was no way to sweep the fix across all the albums short of
selecting each one by hand and clicking through the dialogs.

WHAT IT DOES NOT DO
It never changes which album is active. The GUI's config is read for the master
paths and the SFTP password only; each album is rebuilt through a config built
in memory for that album alone. Nothing is persisted, so a sweep cannot leave
the app pointing somewhere the user did not put it.

SAFETY
  * Dry run is the DEFAULT: it lists what it would rebuild and stops.
  * One album failing does not stop the sweep — it is reported and the run
    continues, with a non-zero exit if anything failed.

USAGE  (from the speciesviewer/ directory, with it on the import path)
  set PYTHONPATH=.        (PowerShell: $env:PYTHONPATH = ".")
  python batch/svrRebuildAllWebsites.py                  # dry run
  python batch/svrRebuildAllWebsites.py --apply
  python batch/svrRebuildAllWebsites.py --apply --only costa
  python batch/svrRebuildAllWebsites.py --apply --blogs  # blogs too, not just species pages
  python batch/svrRebuildAllWebsites.py --apply --master # albums AND the master
  python batch/svrRebuildAllWebsites.py --apply --master --no-albums   # master only
"""
import argparse
import configparser
import logging
import os
import posixpath
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

logger = logging.getLogger("speciesviewer")

# What a full album rebuild consists of, in order. Species pages are the point;
# the blogs are opt-in because they are slower and re-upload every blog picture.
STEP_WEBSITE = "website"
STEP_BLOGS = "blogs"


def _album_dirs(virttrips_root):
    """Every album under the albums root — a directory holding a virttrip.ini."""
    if not virttrips_root or not os.path.isdir(virttrips_root):
        return []
    out = []
    for name in sorted(os.listdir(virttrips_root)):
        path = os.path.join(virttrips_root, name)
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "virttrip.ini")):
            out.append(path)
    return out


def _album_web_root(album_dir, trips_folder):
    """The album's web root: <trips folder>/<web identity>/.

    The web identity is the album's own `web_slug` when it has one (WP4's
    explicit identity), else `trip_name`, else the folder name — the same
    precedence the rest of the app uses, so an album whose folder name differs
    from its published slug ('costa-ricadb' -> /trips/costa-rica/) still lands
    on its real site. The folder name alone would publish to the wrong place."""
    from utils.virttrip_config import read_virttrip_ini
    try:
        ini = read_virttrip_ini(album_dir) or {}
    except Exception:
        ini = {}
    slug = ((ini.get("web_slug") or "").strip()
            or (ini.get("trip_name") or "").strip()
            or Path(album_dir).name)
    return f"{trips_folder.rstrip('/')}/{slug}/"


def _trips_folder(gui_cfg):
    """The web folder every trip/album site sits in ('/trips'). Taken from the
    active album's (or trip's) configured web root rather than hard-coded, so a
    site laid out differently still works."""
    # posixpath directly, not engine.websupport.webParent: importing websupport
    # here would drag in engine.projectlib, which imports websupport back.
    for section, key in (("virttrip", "virttripbiotawebroot"),
                         ("trip", "tripbiotawebroot"),
                         ("repository", "biotawebroot")):
        root = (gui_cfg.get(section, key, fallback="") or "").strip()
        parent = posixpath.dirname(root.replace("\\", "/").rstrip("/")) if root else ""
        if parent and parent != "/":
            return parent
    return "/trips"


def _config_for(album_dir, web_root, gui_cfg):
    """A standalone config for rebuilding THIS album. Mirrors what
    utils.batch_config.generate_svconfig_ini writes for virttrip_mode, but in
    memory and for an album that is not the active one."""
    master_db = gui_cfg.get("master", "masterbiotadatabase", fallback="")
    master_web = gui_cfg.get("master", "masterbiotawebroot", fallback="/nature/")
    cfg = configparser.ConfigParser()
    cfg["repository"] = {
        "BiotaDatabase": album_dir,
        "BiotaWebRoot": web_root,
        "ReferenceSpecies": os.path.join(master_db, "metadata", "active", "species.csv"),
    }
    cfg["master"] = {"MasterBiotaDatabase": master_db, "MasterBiotaWebRoot": master_web}
    cfg["project"] = {"PictureDirectory": ""}
    cfg["debug"] = {"Classification": "0", "SpeciesFound": "0"}
    cfg["websettings"] = {
        "Sftppwd": gui_cfg.get("websettings", "Sftppwd", fallback=""),
        "base_url": gui_cfg.get("websettings", "base_url", fallback=""),
    }
    cfg["blogconfig"] = {
        "minimum_score": gui_cfg.get("blogconfig", "minimum_score", fallback="0"),
        "auto_create_blogs": "False",     # a sweep never invents new blog drafts
    }
    return cfg


def _hms(seconds):
    """'42s' / '3m12s' — a sweep of 17 albums runs into the minutes, and a bare
    second count stops being readable there."""
    seconds = int(round(seconds))
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m{seconds % 60:02d}s"


class _Args:
    """The batch modules take (args, config); only `config` is read inside
    main(). This stands in for the argparse namespace their CLI builds."""
    config = ""


def _run_step(step, cfg):
    if step == STEP_WEBSITE:
        import batch.svv4BuildWebsite as mod
    else:
        import batch.svv6RedoBlog as mod
    mod.main(_Args(), cfg)


def _master_species_count(config):
    """How many master species have a picture - the size of a master rebuild.

    Read straight from the master's committed species.csv rather than by loading
    the whole project: the listing has to stay instant, and the count from the
    very file the rebuild renders from is honest enough for a preview. Returns
    None when it cannot be read, and the caller then says "every species page"
    rather than inventing a number."""
    master_db = config.get("master", "masterbiotadatabase", fallback="")
    path = os.path.join(master_db, "metadata", "active", "species.csv")
    if not os.path.isfile(path):
        return None
    try:
        import csv
        with open(path, newline="", encoding="utf-8") as f:
            return sum(1 for row in csv.DictReader(f, delimiter=";")
                       if (row.get("Level") or "").strip().upper().endswith("SPECIES")
                       and (int(row.get("iPicturesWithJPGsThisRun") or 0)
                            + int(row.get("iPicturesWithJPGsCollection") or 0)) > 0)
    except Exception as e:
        logger.warning(f"could not count the master species in {path}: {e}")
        return None


def main(args, config):
    from utils.config import get_virttrips_root

    # The two halves of an album refresh are independent. The species pages
    # move JPGs (cached, but it walks the whole tree); the blogs are HTML only.
    # After an HTML-only fix, re-running the pages costs a lot for no change.
    steps = []
    if getattr(args, "blogs", False):
        steps.append(STEP_BLOGS)        # blogs first, then the species pages
    if getattr(args, "website", True):
        steps.append(STEP_WEBSITE)

    virttrips_root = get_virttrips_root()
    trips_folder = _trips_folder(config)
    # Albums and the master are independent scopes: rebuilding only the master
    # is a normal thing to want, since it owns tripindex-nl/en.html.
    albums = (_album_dirs(virttrips_root)
              if getattr(args, "albums", True) and steps else [])
    only = (getattr(args, "only", "") or "").lower()
    if only:
        albums = [a for a in albums if only in Path(a).name.lower()]

    apply_it = getattr(args, "apply", False)
    do_master = getattr(args, "master", False)

    # Describe only the scopes actually in play. A master-only run used to print
    # the albums root, the web folder and "Albums: 0" above one bare path - three
    # lines about the half that was switched off, and none of them a sentence.
    album_scope = bool(albums or (getattr(args, "albums", True) and steps))
    if not album_scope and not do_master:
        print("Nothing to rebuild.")
        return 0

    if album_scope:
        what = " and ".join("species pages" if st == STEP_WEBSITE else "blog HTML"
                            for st in steps) or "nothing"
        print(f"Rebuilding {what} for {len(albums)} album(s) under {trips_folder}, "
              f"read from {virttrips_root or '(albums root not configured)'}:")
        for a in albums:
            print(f"  {Path(a).name:28} -> {_album_web_root(a, trips_folder)}")
    if do_master:
        master_web = config.get('master', 'masterbiotawebroot', fallback='/nature/')
        n = _master_species_count(config)
        how_many = f"all {n} species pages" if n else "every species page"
        print(f"{chr(10) if album_scope else ''}Re-rendering {how_many} of the master "
              f"and copying them to {master_web}, together with the all-species "
              f"overviews and the stylesheet.")

    if not apply_it:
        print("\nDRY RUN — nothing was rebuilt. Re-run with --apply.")
        return 0

    started = time.time()
    ok, failed = [], []
    for album_dir in albums:
        name = Path(album_dir).name
        web_root = _album_web_root(album_dir, trips_folder)
        print(f"\n=== {name} ({web_root}) ===")
        cfg = _config_for(album_dir, web_root, config)
        try:
            for step in steps:
                print(f"  {step} …")
                _run_step(step, cfg)
            ok.append(name)
        except Exception as e:
            # One bad album must not end the sweep — the whole point is to get
            # the fix onto every site that CAN take it.
            logger.warning(f"{name}: {step} failed: {e}", exc_info=True)
            print(f"  FAILED ({step}): {e}")
            failed.append(f"{name} [{step}]")

    if do_master:
        print("\n=== master site ===")
        try:
            import batch.svMasterRebuildWeb as mod
            mcfg = configparser.ConfigParser()
            master_db = config.get("master", "masterbiotadatabase", fallback="")
            master_web = config.get("master", "masterbiotawebroot", fallback="/nature/")
            mcfg["repository"] = {
                "BiotaDatabase": master_db,
                "BiotaWebRoot": master_web,
                "ReferenceSpecies": os.path.join(master_db, "metadata", "active", "species.csv"),
            }
            mcfg["master"] = {"MasterBiotaDatabase": "", "MasterBiotaWebRoot": master_web}
            mcfg["project"] = {"PictureDirectory": ""}
            mcfg["debug"] = {"Classification": "0", "SpeciesFound": "0"}
            mcfg["websettings"] = {
                "Sftppwd": config.get("websettings", "Sftppwd", fallback=""),
                "base_url": config.get("websettings", "base_url", fallback=""),
            }
            m0 = time.time()
            mod.main(_Args(), mcfg)
            print(f"  master finished in {_hms(time.time() - m0)}")
            ok.append("(master)")
        except Exception as e:
            logger.warning(f"master rebuild failed: {e}", exc_info=True)
            print(f"  FAILED: {e}")
            failed.append("(master)")

    print(f"\nRebuilt {len(ok)} of {len(ok) + len(failed)} in {time.time() - started:.0f}s.")
    if failed:
        print("Failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild every album website")
    parser.add_argument("--config", type=str,
                        default=os.environ.get("SVCONFIG", "svconfig.ini"))
    parser.add_argument("--apply", action="store_true",
                        help="actually rebuild (default is a dry run)")
    parser.add_argument("--only", default="",
                        help="only albums whose folder name contains this text")
    parser.add_argument("--blogs", action="store_true",
                        help="re-link the album blogs (HTML only, no pictures moved)")
    parser.add_argument("--no-website", dest="website", action="store_false",
                        help="skip the species pages (with --blogs: blogs only, "
                             "the cheap path after an HTML-only fix)")
    parser.add_argument("--master", action="store_true",
                        help="rebuild the master site as well")
    parser.add_argument("--no-albums", dest="albums", action="store_false",
                        help="skip the albums (with --master: rebuild ONLY the master)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")
    # The GUI's own config supplies the master paths + SFTP password; the album
    # paths are derived per album, never read from (or written to) the active one.
    from utils.config import _config
    sys.exit(main(args, _config))
