"""Delete a trip or album completely — its folder, its row on the trip list and
its published pages (docs/plans/force_delete_plan.md, part A).

ONE JOB: find every trip/album NAME and what exists for it, then remove the
parts the user ticked. No Qt: the two dialogs (Delete Trip Completely, Delete
Album) show the scan and call the actions.

INVARIANTS
- Pictures are never deleted; the master is never touched.
- The website parts (trip-list row, published pages) only on a computer that
  OWNS the website: a website on this computer, or a server website from the
  computer with the full master (BigBoy). Never from a read-only SmallMaster
  (the laptop): deleting a laptop's trip there would wipe what BigBoy
  published (Gilbert's Brazil example, 2026-10-12). website_parts_allowed()
  is the one check; the actions refuse on their own as well.
- A trip an album was made from: its web address belongs to the album, so its
  website parts are never offered.
- No backup unless asked; the size is always reported.
"""

from __future__ import annotations

import csv
import logging
from configparser import ConfigParser
from pathlib import Path

logger = logging.getLogger("speciesviewer")


# ── who may touch the website ────────────────────────────────────────────────

def _full_master() -> bool:
    """BigBoy's full master — not a SmallMaster install (readonly_locked)."""
    from utils.config import _config
    return bool(_config.get("master", "masterbiotadatabase", fallback="").strip()) and \
        _config.get("master", "readonly_locked", fallback="no").strip().lower() != "yes"


def website_parts_allowed() -> bool:
    from utils import site_config
    if site_config.is_local():
        return True             # each master's own website on this computer
    return site_config.publishing_set_up() and _full_master()


def website_refusal() -> str:
    """Why the website parts are not offered — "" when they are."""
    from utils import site_config
    if website_parts_allowed():
        return ""
    if not site_config.publishing_set_up():
        return "No website is set up, so there is nothing to remove from it."
    return ("This computer has a read-only copy of the master. Your website is "
            "published from the computer with the full master — remove it from "
            "the website there.")


# ── reading ──────────────────────────────────────────────────────────────────

def _ini(path: Path) -> dict:
    cp = ConfigParser(interpolation=None)
    for enc in ("utf-8", "cp1252"):
        try:
            cp.read(path, encoding=enc)
            break
        except Exception:
            cp = ConfigParser(interpolation=None)
    out = {}
    for section in cp.sections():
        out.update(dict(cp.items(section)))
    return out


def folder_size(path) -> int:
    from engine.album_delete import _dir_size
    p = Path(path) if path else None
    return _dir_size(p) if p and p.is_dir() else 0


def human(nbytes: int) -> str:
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if nbytes >= size:
            return f"{nbytes / size:.1f} {unit}"
    return f"{nbytes} bytes"


def _local_listed(master_db: str) -> set[str]:
    """Names in the master's own trip_admin.csv."""
    path = Path(master_db) / "admin" / "trip_admin.csv" if master_db else None
    names: set[str] = set()
    if not path or not path.is_file():
        return names
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter=";"):
                n = (row.get("web_slug") or row.get("trip_name") or "").strip()
                if n:
                    names.add(n)
    except Exception as e:
        logger.info(f"force_delete: could not read {path}: {e}")
    return names


def website_state(master_db: str):
    """(listed names, published names, problem) of the website this computer
    may change. published is None when it could not be read (or may not be
    touched); problem says why, in words for the dialog."""
    from utils import site_config
    from utils.config import _config
    if not website_parts_allowed():
        return _local_listed(master_db), None, website_refusal()
    if site_config.is_local():
        trips = site_config.local_root() / "trips"
        published = {p.name for p in trips.iterdir() if p.is_dir()} if trips.is_dir() else set()
        return _local_listed(master_db), published, ""
    # A server website, from the full master: the LIVE list is the truth.
    from engine.publish_index import advertised_names
    master_web = _config.get("master", "masterbiotawebroot", fallback="/nature/")
    listed = advertised_names(site_config.site_base(), master_web)
    try:
        from engine import sftplib
        site = sftplib.open_site(password=_config.get("websettings", "Sftppwd",
                                                      fallback="").strip("'"))
        site.connect()
        try:
            published = set(site.listdir(site.remotePrefix() + "/trips"))
        finally:
            site.disconnect()
    except Exception as e:
        logger.info(f"force_delete: could not list the website: {e}")
        return listed or set(), None, "The website cannot be reached — try again when it can."
    if listed is None:
        return set(), published, "The website's trip list cannot be read — try again later."
    return listed, published, ""


def _other_master(owner: str, master_db: str) -> bool:
    """True when the ini names ANOTHER master that lives on this computer. A
    trip or album imported from BigBoy names BigBoy's master path, which does
    not exist on the laptop — that is not "another master's"."""
    if not owner or not master_db:
        return False
    if str(Path(owner)).lower() == str(Path(master_db)).lower():
        return False
    return Path(owner).is_dir()


def scan(kind: str, trips_root: str, albums_root: str, master_db: str,
         listed: set[str] | None = None, published: set[str] | None = None) -> list[dict]:
    """Every trip (kind="trip") or album (kind="album") name and what exists
    for it. Read-only. A row:
        name, kind, folder (Path|None), size, listed, pages (bool),
        owner (the master its ini names), other_master (bool),
        album_from_trip (album folder name|""), pictures (str)
    listed / published: the website's names (website_state). For kind="trip"
    this includes names that are ONLY on the trip list or only published —
    the madagascar case (folder deleted by hand)."""
    listed = listed or set()
    published = published or set()
    rows: dict[str, dict] = {}
    root = Path(trips_root if kind == "trip" else albums_root or "")
    ini_name = "trip.ini" if kind == "trip" else "virttrip.ini"

    # Albums made from a trip, keyed by that trip's folder name; and every
    # album's name, so a trip-list row of an album is not taken for a trip.
    from_trip: dict[str, str] = {}
    album_names: set[str] = set()
    if albums_root and Path(albums_root).is_dir():
        for a in Path(albums_root).iterdir():
            if (a / "virttrip.ini").is_file():
                data = _ini(a / "virttrip.ini")
                src = (data.get("source_trip_id") or "").strip()
                if src:
                    from_trip[src.lower()] = a.name
                album_names.add((data.get("web_slug") or data.get("trip_name")
                                 or a.name).strip().lower())

    if str(root) not in ("", ".") and root.is_dir():
        for folder in sorted(root.iterdir()):
            ini = folder / ini_name
            if not ini.is_file():
                continue
            data = _ini(ini)
            name = (data.get("web_slug") or data.get("trip_name") or folder.name).strip()
            owner = (data.get("master_repository_disk") or "").strip()
            pictures = ""
            if kind == "trip":
                try:
                    from utils.trip_identity import resolve_trip_picture_root
                    pictures = resolve_trip_picture_root(str(folder), "trip",
                                                         active_picturedirectory="") or ""
                except Exception:
                    pictures = ""
            rows[name.lower()] = {
                "name": name, "kind": kind, "folder": folder,
                "size": folder_size(folder), "listed": False, "pages": False,
                "owner": owner, "other_master": _other_master(owner, master_db),
                "album_from_trip": from_trip.get(folder.name.lower(), "") if kind == "trip" else "",
                "pictures": pictures,
            }

    def _row(name):
        return rows.setdefault(name.lower(), {
            "name": name, "kind": kind, "folder": None, "size": 0,
            "listed": False, "pages": False, "owner": "", "other_master": False,
            "album_from_trip": "", "pictures": ""})

    # Names without a folder only appear in the TRIP dialog: the trip list does
    # not say whether a row was a trip or an album, and a folder-less album is
    # the rarer case.
    def _ours(name):
        return name.lower() in rows or (kind == "trip" and name.lower() not in album_names)

    for name in listed:
        if _ours(name):
            _row(name)["listed"] = True
    for name in published:
        if name.lower() in rows or (_ours(name) and name in listed):
            _row(name)["pages"] = True
    return sorted(rows.values(), key=lambda r: r["name"].lower())


# ── the actions ──────────────────────────────────────────────────────────────

def delete_folder(folder, backup: bool = False) -> str:
    """Remove a trip or album folder; zip it into .trash beside it first only
    when asked. Returns "" on success, else the reason."""
    from engine import album_delete
    folder = Path(folder)
    if not folder.is_dir():
        return ""
    album_delete.sweep_deleting(folder.parent)
    if backup:
        album_delete.backup_album(folder)
    return album_delete.delete_local(folder)


def _require_website():
    if not website_parts_allowed():
        raise RuntimeError(website_refusal())


def remove_from_trip_list(name: str, kind: str = "trip") -> str:
    """Take `name` off the website's trip list."""
    _require_website()
    from engine.publish_index import unadvertise_name
    from utils import site_config
    from utils.config import _config
    cfg = ConfigParser(interpolation=None)
    cfg["master"] = {
        "MasterBiotaDatabase": _config.get("master", "masterbiotadatabase", fallback=""),
        "MasterBiotaWebRoot": _config.get("master", "masterbiotawebroot", fallback="/nature/"),
    }
    # A website on this computer reads the master's own list (no base_url);
    # a server website is changed from its LIVE list.
    cfg["websettings"] = {
        "Sftppwd": _config.get("websettings", "Sftppwd", fallback=""),
        "base_url": "" if site_config.is_local() else site_config.site_base(),
    }
    return unadvertise_name(cfg, name, kind)


def remove_pages(name: str) -> None:
    """Remove /trips/<name>/ from the website."""
    _require_website()
    import re
    # The server's rmtree deletes whatever it is given: only a plain name may
    # reach it, so the target is always <site>/trips/<name> — never /trips
    # itself, never a path with ".." or a slash.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name or ""):
        raise RuntimeError(f"“{name}” is not a plain web name — nothing removed.")
    from engine import sftplib
    from utils.config import _config
    site = sftplib.open_site(password=_config.get("websettings", "Sftppwd",
                                                  fallback="").strip("'"))
    site.connect()
    try:
        site.rmtree(site.remotePrefix() + "/trips/" + name)
    finally:
        site.disconnect()
