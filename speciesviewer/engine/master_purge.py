"""Delete a test master's DATA — everything except its pictures
(docs/plans/force_delete_plan.md, part B).

ONE JOB: from a master's OWN settings file, list what belongs to it — its
collection, its trips, its albums — with sizes, and delete that on request.
Removing the master's settings, caches and local website stays with
utils.masters.remove; this module only adds the data folders.

INVARIANTS
- Read from the master's own config file, never from the open master's
  utils.config._config: the master being deleted is never the open one.
- Pictures are never deleted (the pictures folder is listed, not touched).
- "Belongs to it" = the trip.ini / virttrip.ini names this master's
  collection. A trip of another master in the same folder stays.
- Nothing another master still uses is deleted: a collection, trips folder
  or albums folder that another master's settings also name is skipped.
- The main master is refused, as utils.masters.remove already refuses it.
- No backup (decided 2026-10-12): the settings zip that remove() makes is
  the only one.
"""

from __future__ import annotations

import configparser
import logging
from pathlib import Path

logger = logging.getLogger("speciesviewer")


def _read_config(config_file: Path) -> configparser.ConfigParser:
    from utils.ini_io import read_ini
    cfg = configparser.ConfigParser()
    if Path(config_file).is_file():
        read_ini(cfg, config_file)
    return cfg


def _get(cfg, section, key) -> str:
    return (cfg.get(section, key, fallback="") or "").strip().strip('"\'')


def _norm(p) -> str:
    try:
        return str(Path(p).resolve()).lower()
    except OSError:
        return str(Path(p)).lower()


def _config_file(entry: dict) -> Path:
    from utils import masters
    from utils.config import CONFIG_FILENAME
    return masters.folder_of(entry) / "config" / CONFIG_FILENAME


def _paths_of(cfg) -> dict:
    trips = _get(cfg, "trip", "tripsrootdirectory")
    if not trips and _get(cfg, "trip", "tripbiotadatabase"):
        trips = str(Path(_get(cfg, "trip", "tripbiotadatabase")).parent)
    return {
        "collection": _get(cfg, "master", "masterbiotadatabase"),
        "trips": trips,
        "albums": _get(cfg, "virttrip", "virttriprootdirectory"),
        "pictures": _get(cfg, "currentpictures", "rootdirectory"),
    }


def _used_by_others(name: str) -> dict:
    """Every collection / trips / albums folder the OTHER masters name, and
    the trip and album each of them has OPEN ({folder: master name}) — with
    shared folders, master B can have master A's trip open (found
    2026-10-12: SVlocal3 had SVlocal2's 'autumn' open)."""
    from utils import masters
    used = {"collection": set(), "trips": set(), "albums": set(), "open": {}}
    for entry in masters.load()["masters"]:
        if entry["name"] == name:
            continue
        cfg = _read_config(_config_file(entry))
        paths = _paths_of(cfg)
        for k in ("collection", "trips", "albums"):
            if paths[k]:
                used[k].add(_norm(paths[k]))
        for section, key in (("trip", "tripbiotadatabase"),
                             ("virttrip", "virttripbiotadatabase")):
            folder = _get(cfg, section, key)
            if folder:
                used["open"][_norm(folder)] = entry["name"]
    return used


def _owned(root: str, ini_name: str, collection: str) -> tuple[list, list]:
    """(folders that belong to this collection, folders of another master)."""
    from engine.force_delete import _ini
    mine, others = [], []
    if not root or not Path(root).is_dir() or not collection:
        return mine, others
    for folder in sorted(Path(root).iterdir()):
        ini = folder / ini_name
        if not ini.is_file():
            continue
        owner = (_ini(ini).get("master_repository_disk") or "").strip()
        (mine if owner and _norm(owner) == _norm(collection) else others).append(folder)
    return mine, others


def plan(name: str) -> dict:
    """What deleting master `name`'s data would delete. Read-only.
    {collection, trips: [...], albums: [...], skipped: [(path, why)],
     other_trips, pictures, size, roots_to_remove: [...]}"""
    from engine.force_delete import folder_size
    from utils import masters
    entry = masters.find(masters.load(), name)
    if entry.get("main"):
        raise masters.MasterError(f"“{name}” is the main master — its data is never deleted here.")
    paths = _paths_of(_read_config(_config_file(entry)))
    others = _used_by_others(name)
    out = {"collection": None, "trips": [], "albums": [], "skipped": [],
           "other_trips": [], "pictures": paths["pictures"], "roots_to_remove": []}

    col = paths["collection"]
    if col and Path(col).is_dir():
        if _norm(col) in others["collection"]:
            out["skipped"].append((col, "another master uses this collection too"))
        else:
            out["collection"] = Path(col)
    out["trips"], out["other_trips"] = _owned(paths["trips"], "trip.ini", col)
    out["albums"], other_albums = _owned(paths["albums"], "virttrip.ini", col)
    out["other_trips"] += other_albums
    # Never what another master has open.
    for key in ("trips", "albums"):
        keep = []
        for folder in out[key]:
            other = others["open"].get(_norm(folder))
            if other:
                out["skipped"].append((str(folder), f"open in the master “{other}”"))
            else:
                keep.append(folder)
        out[key] = keep

    # The trips / albums folder itself: only when nothing else is left in it
    # afterwards and no other master names it.
    for key, owned in (("trips", out["trips"]), ("albums", out["albums"])):
        root = paths[key]
        if not root or not Path(root).is_dir():
            continue
        # A .trash holds backups the user asked for: then the folder stays.
        rest = [p for p in Path(root).iterdir() if p not in owned]
        if not rest and _norm(root) not in others[key]:
            out["roots_to_remove"].append(Path(root))

    folders = ([out["collection"]] if out["collection"] else []) + out["trips"] + out["albums"]
    out["sizes"] = {str(f): folder_size(f) for f in folders}
    out["size"] = sum(out["sizes"].values())
    # Picture files in each folder's biota/ — COPIES for a trip, but the
    # master's collection is the backup: once the originals are cleared from
    # the pictures folder, its copies are the only ones (2026-10-12).
    out["pictures_in"] = {str(f): picture_count(f) for f in folders}
    return out


def picture_count(folder) -> int:
    from utils.raw_extensions import PICTURE
    biota = Path(folder) / "biota"
    if not biota.is_dir():
        return 0
    return sum(1 for p in biota.rglob("*") if p.is_file() and p.suffix.lower() in PICTURE)


def delete(p: dict) -> list[str]:
    """Delete what plan() listed. Returns report lines (✅ / ❌)."""
    from engine.force_delete import delete_folder, human
    report = []
    for folder in p["trips"] + p["albums"] + ([p["collection"]] if p["collection"] else []):
        err = delete_folder(folder, backup=False)
        size = human(p["sizes"].get(str(folder), 0))
        report.append(f"❌ {folder}: {err}" if err else f"✅ Deleted {folder} ({size})")
    for root in p["roots_to_remove"]:
        try:
            if not any(root.iterdir()):          # truly empty only — never a .trash
                root.rmdir()
                report.append(f"✅ Deleted the empty folder {root}")
        except OSError as e:
            report.append(f"❌ {root}: {e}")
    for line in report:
        (logger.warning if line.startswith("❌") else logger.info)(f"Delete master data — {line}")
    return report
