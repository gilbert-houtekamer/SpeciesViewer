# trip_registry.py: build a per-machine lifecycle registry of every (virt)trip.
#
# Phase 1 (this file): a purely LOCAL scan. For each trip/virttrip repository on
# this machine it reads trip.ini (name + date range) and walks
# metadata/review/<day>/statemachine.ini to count how far each day has progressed
# through sv1-sv7. The result is one row per trip, written to a
# trip_registry_<machine>.csv shard. No network here; the master species count
# (time-range query) and FTP sync come in later phases.
#
# Design + rationale: see memory project_trip_registry. Identity is the trip
# name (globally unique); the record key is trip_name+machine, so a trip held on
# both laptop and master yields two rows that the overview later merges.

import os
import csv
import bisect
import configparser
import logging
from datetime import datetime
from pathlib import Path

from engine.statemachine import StateMachine

logger = logging.getLogger("speciesviewer")

# Per-day statemachine task -> the registry "days_*" column it feeds.
# Order matters: it mirrors the sv2-sv7 pipeline so a status can be derived.
_DAY_STEP_COLUMNS = [
    ("ReadDirExif",   "days_read"),
    ("WritePictures", "days_pictures"),
    ("WriteBlog",     "days_blog"),
    ("BuildWebsite",  "days_website"),
    ("Freeze",        "days_frozen"),
    ("WriteCommit",   "days_committed"),
]

REGISTRY_FIELDS = [
    "machine", "trip_name", "type",
    "fromdate", "todate", "created",
    "num_days",
    "days_read", "days_pictures", "days_blog", "days_website",
    "days_frozen", "days_committed",
    "species_files_in_master",   # real (identified) species in the trip's date range
    "placeholder_in_master",     # "? name" placeholder pictures — cleanup candidates
    "status",
    "repo_path", "scanned_at",
]

# A day folder is named like "2025-11-05"; used to derive a date range when
# trip.ini doesn't carry fromdate/todate, and to ignore non-day subdirs.
_DAY_RE = "%Y-%m-%d"


def _looks_like_day(name):
    try:
        datetime.strptime(name, _DAY_RE)
        return True
    except ValueError:
        return False


def _clean(val):
    """Strip an inline ';' comment and surrounding whitespace from a trip.ini
    value. configparser keeps inline comments verbatim, and hand-edited
    trip.ini files use them (e.g. 'oneblog = day ; hardcoded')."""
    return val.split(";", 1)[0].strip()


def _read_trip_ini(repo_path):
    """Read trip.ini read-only (never mutate it — triplib.readTrip rewrites
    'created' on every read, which we must avoid here). Returns the flattened
    trip_config/trip_text dict, or {} when absent/unparseable."""
    ini = os.path.join(repo_path, "trip.ini")
    if not os.path.exists(ini):
        return {}
    cp = configparser.ConfigParser()
    try:
        with open(ini, "r", encoding="utf-8") as fh:
            cp.read_string(fh.read())
    except Exception as e:
        logger.info(f"trip_registry: could not parse {ini}: {e}")
        return {}
    flat = {}
    for section in cp.sections():
        for key, value in cp.items(section):
            flat[key] = value
    return flat


def scan_repo(repo_path, trip_type, machine):
    """Scan a single trip/virttrip repository into one registry row.
    `trip_type` is 'trip' or 'virttrip'. Returns None if the directory is not a
    recognizable repository (no metadata/review and no trip.ini)."""
    repo_path = os.path.normpath(repo_path)
    review_dir = os.path.join(repo_path, "metadata", "review")
    trip_ini = _read_trip_ini(repo_path)
    if not os.path.isdir(review_dir) and not trip_ini:
        return None

    # Per-day pipeline progress.
    day_names = []
    step_counts = {col: 0 for _, col in _DAY_STEP_COLUMNS}
    if os.path.isdir(review_dir):
        for entry in sorted(os.listdir(review_dir)):
            if not os.path.isdir(os.path.join(review_dir, entry)):
                continue
            day_names.append(entry)
            sm = StateMachine(repo_path, entry)
            taskstate = sm.Taskstate
            for task, col in _DAY_STEP_COLUMNS:
                try:
                    if taskstate[task][0]:
                        step_counts[col] += 1
                except (KeyError, IndexError, TypeError):
                    pass

    num_days = len(day_names)

    # Date range: prefer trip.ini, else derive from the dated day folders.
    dated_days = sorted(d for d in day_names if _looks_like_day(d))
    fromdate = _clean(trip_ini.get("fromdate", "")) or (dated_days[0] if dated_days else "")
    todate = _clean(trip_ini.get("todate", "")) or (dated_days[-1] if dated_days else "")

    # trip.ini sometimes carries the web path (e.g. "/trips/2017Oostzee/") plus
    # an inline comment in trip_name; reduce to the bare last segment so
    # duplicates surface as the same name instead of hiding behind a malformed
    # value.
    raw_name = _clean(trip_ini.get("trip_name", "")).strip("/")
    trip_name = (raw_name.rsplit("/", 1)[-1] if raw_name else "") or Path(repo_path).name

    row = {
        "machine": machine,
        "trip_name": trip_name,
        "type": trip_type,
        "fromdate": fromdate,
        "todate": todate,
        "created": _clean(trip_ini.get("created", "")),
        "num_days": num_days,
        "species_files_in_master": "",
        "placeholder_in_master": "",
        "repo_path": repo_path,
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    row.update(step_counts)
    row["status"] = derive_status(row)
    return row


def derive_status(row):
    """Lifecycle stage from the per-machine day counts. Master-side stages
    (Copied/Rebuilt/Integrated/Deleted) are added in a later phase once the
    species count and event markers exist."""
    num = row["num_days"]
    if num == 0:
        return "Empty"
    if row["days_committed"] == num:
        return "Write-committed"
    if row["days_committed"] > 0:
        return "Partially committed"
    if row["days_pictures"] > 0 or row["days_read"] > 0:
        return "In progress"
    return "Ingested"


# ── Master time-range species count (Phase 1b) ──────────────────────────────────
#
# The trip's date range is the attribution key (user decision): count master
# pictures whose capture date falls in [fromdate, todate] and that carry a
# species name. Read the canonical master active CSV once into a sorted list of
# YYYY-MM-DD capture dates, then range-count per trip with bisect. Trips with no
# date range (e.g. the ongoing month-grouped "waarneming" phone collection) get
# a blank count — a time-range query is meaningless there.

def _norm_date(s):
    """Normalize a DateTimeOriginal value to 'YYYY-MM-DD', or None.
    Handles the two formats exiftool/the pipeline emit (see triplib.tryJPGtime):
    'YYYY:MM:DD HH:MM:SS' and 'DD:MM:YYYY HH:MM:SS', plus already-dashed dates."""
    s = s.strip()
    if len(s) < 10:
        return None
    try:
        if s[4] == ":":        # YYYY:MM:DD
            return f"{s[0:4]}-{s[5:7]}-{s[8:10]}"
        if s[4] == "-":        # already YYYY-MM-DD
            return s[0:10]
        if s[2] == ":":        # DD:MM:YYYY
            return f"{s[6:10]}-{s[3:5]}-{s[0:2]}"
    except IndexError:
        return None
    return None


def master_active_csv(master_repo):
    return os.path.join(master_repo, "metadata", "active", "data_pictures.csv")


def load_master_species_dates(master_repo):
    """Two sorted lists of YYYY-MM-DD capture dates from the master active CSV:
    (real_species_dates, placeholder_dates). "? name" placeholders are split out
    because they're unidentified — excluded from the real count and surfaced
    separately as cleanup candidates. Returns (None, None) when the CSV is
    missing/unreadable (so callers leave the counts blank rather than report 0)."""
    if not master_repo:
        return None, None
    path = master_active_csv(master_repo)
    if not os.path.exists(path):
        logger.info(f"trip_registry: master CSV not found: {path}")
        return None, None
    real, placeholder = [], []
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh, delimiter=";")
            header = next(reader)
            idx = {h: i for i, h in enumerate(header)}
            di, si = idx.get("DateTimeOriginal"), idx.get("WetSoortNaam")
            if di is None or si is None:
                logger.info("trip_registry: master CSV missing DateTimeOriginal/WetSoortNaam")
                return None, None
            for row in reader:
                if si >= len(row) or di >= len(row):
                    continue
                wsn = row[si].strip()
                if not wsn:
                    continue
                d = _norm_date(row[di])
                if not d:
                    continue
                (placeholder if wsn.startswith("?") else real).append(d)
    except Exception as e:
        logger.info(f"trip_registry: failed reading master CSV: {e}")
        return None, None
    real.sort()
    placeholder.sort()
    return real, placeholder


def count_in_range(sorted_dates, lo, hi):
    """Inclusive count of dates with lo <= d <= hi (all 'YYYY-MM-DD')."""
    left = bisect.bisect_left(sorted_dates, lo)
    right = bisect.bisect_right(sorted_dates, hi)
    return right - left


def enumerate_repos(root, trip_type, machine):
    """Scan every immediate subdirectory of `root` as a candidate repository."""
    rows = []
    if not root or not os.path.isdir(root):
        if root:
            logger.info(f"trip_registry: root not found, skipping: {root}")
        return rows
    for entry in sorted(os.listdir(root)):
        repo_path = os.path.join(root, entry)
        if not os.path.isdir(repo_path):
            continue
        row = scan_repo(repo_path, trip_type, machine)
        if row:
            rows.append(row)
    return rows


def build_registry(machine, trips_root, virttrips_root, master_repo=None):
    """All trip + virttrip rows for this machine. When master_repo is given and
    its active CSV is readable, fill species_files_in_master per trip via the
    time-range count (trips without a date range are left blank)."""
    rows = enumerate_repos(trips_root, "trip", machine)
    rows += enumerate_repos(virttrips_root, "virttrip", machine)

    real_dates, placeholder_dates = load_master_species_dates(master_repo)
    if real_dates is not None:
        for row in rows:
            lo, hi = row["fromdate"], row["todate"]
            if lo and hi:
                row["species_files_in_master"] = count_in_range(real_dates, lo, hi)
                row["placeholder_in_master"] = count_in_range(placeholder_dates, lo, hi)
    return rows


def write_registry_csv(rows, path):
    """Write the shard atomically (temp + replace). If the target is locked
    (commonly: open in Excel), keep the temp file and warn instead of crashing.
    Returns the path actually written."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REGISTRY_FIELDS, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in REGISTRY_FIELDS})
    try:
        os.replace(tmp, path)
        return path
    except PermissionError:
        logger.warning(f"trip_registry: {path} is locked (open in Excel?); "
                       f"wrote {tmp} instead — close it and rerun to refresh the main file.")
        return tmp
