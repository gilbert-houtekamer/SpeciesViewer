"""One trip name → the suggested folders and web address
(docs/plans/trip_create_naming_plan.md).

ONE JOB: from the name the user types (and the trip's start date) suggest the
trip's data folder, its pictures folder and its web name, and say which of
them already exist or clash. No Qt, no writing: the dialog shows the answers,
Create makes the folders.

    "madagascar", 2026-11-02 →
        data folder      <Trips folder>/madagascardb
        pictures folder  <Pictures folder>/2026/2026 madagascar
        web name         madagascar          (the site shows /trips/madagascar)

INVARIANT: a web name is used by at most one trip or album. The web address
of a published trip can never change, so a clash is refused before anything
is created.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from configparser import ConfigParser
from pathlib import Path

# Characters no folder name may hold on Windows (the strictest platform).
_BAD_FOLDER_CHARS = set('\\/:*?"<>|')
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def web_name(name: str) -> str:
    """The name as it goes into a web address: lowercase, accents dropped,
    anything else between letters and digits becomes one '-'.
    "Costa Rica" → "costa-rica", "Réunion" → "reunion"."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def name_problem(name: str) -> str:
    """Why `name` cannot be a trip name, or "" when it can."""
    name = name.strip()
    if not name:
        return "Type a name for the trip."
    bad = sorted(set(name) & _BAD_FOLDER_CHARS)
    if bad:
        return "A trip name cannot hold " + " ".join(bad) + "."
    if not web_name(name):
        return "The name needs at least one letter or digit."
    return ""


def suggest_data_folder(trips_root: str, name: str) -> Path:
    return Path(trips_root) / (web_name(name) + "db")


def suggest_pictures_folder(pictures_root: str, name: str, start_date: str) -> Path:
    """<Pictures folder>/<year>/<year> <name> — the year of the trip's START,
    not today: a trip planned now for January belongs to next year."""
    year = (start_date or "")[:4]
    return Path(pictures_root) / year / f"{year} {name.strip()}"


def day_folder_count(folder: Path) -> int:
    try:
        return sum(1 for d in Path(folder).iterdir()
                   if d.is_dir() and _DAY_RE.match(d.name))
    except OSError:
        return 0


def _ini_web_names(ini: Path) -> set[str]:
    cp = ConfigParser(interpolation=None)
    try:
        cp.read(ini, encoding="utf-8")
    except Exception:
        try:
            cp.read(ini, encoding="cp1252")
        except Exception:
            return set()
    if not cp.has_section("trip_config"):
        return set()
    return {cp.get("trip_config", k, fallback="").strip().lower()
            for k in ("web_slug", "trip_name")} - {""}


def used_web_names(trips_root: str = "", albums_root: str = "",
                   trip_admin_csv: str = "") -> dict[str, str]:
    """Every web name already taken → WHERE, in words the user can act on:
    trips and albums on disk (their trip.ini / virttrip.ini), and every trip
    on the master's trip list (trip_admin.csv) — a published trip may no
    longer be on this disk."""
    used: dict[str, str] = {}
    for root, ini_name, kind in ((trips_root, "trip.ini", "the trip"),
                                 (albums_root, "virttrip.ini", "the album")):
        if not root or not Path(root).is_dir():
            continue
        for folder in Path(root).iterdir():
            ini = folder / ini_name
            if ini.is_file():
                for name in _ini_web_names(ini):
                    used.setdefault(name, f"{kind} in {folder}")
    if trip_admin_csv and Path(trip_admin_csv).is_file():
        try:
            with open(trip_admin_csv, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter=";"):
                    name = (row.get("web_slug") or row.get("trip_name") or "").strip()
                    if name:
                        used.setdefault(name.lower(), "a trip on your website's trip list")
        except Exception:
            pass
    return used
