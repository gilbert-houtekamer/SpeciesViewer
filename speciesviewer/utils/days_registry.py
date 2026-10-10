"""Per-machine registry of "Days" — ad-hoc single-date picture folders reviewed
and sent straight to the Master (no trip website). See working_days_plan.md.

A Day is always a YYYY-MM-DD folder; its real state (ratings/colours/species)
lives in the folder's XMP sidecars, so this registry only tracks the small list
of folders and their status. Stored as JSON next to the machine config so it is
per-machine (like Recent Trips), never shared through the master.
"""
import json
import logging
import os
import re
from pathlib import Path

from utils.config import STATE_DIR, MACHINE_NAME

logger = logging.getLogger("speciesviewer")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

STATUS_ACTIVE = "active"
STATUS_DONE = "done"


def registry_path():
    return STATE_DIR / f"days_{MACHINE_NAME}.json"


def is_day_folder_name(name):
    """True when a folder name is a valid YYYY-MM-DD date (the only shape a Day
    may take — KISS)."""
    return bool(DATE_RE.match(name or ""))


def date_of(folder):
    """The YYYY-MM-DD date a folder represents, or '' if its name isn't a date."""
    name = Path(folder).name
    return name if is_day_folder_name(name) else ""


def _load_raw():
    """The whole registry document, or {} — so every writer can preserve keys it
    does not own. save_days() used to rewrite the file as {"days": ...}, which
    would silently drop the active-day marker added below."""
    path = registry_path()
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"days_registry: could not read {path}: {e}")
        return {}


def _save_raw(data):
    path = registry_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"days_registry: could not write {path}: {e}")


def get_active_day():
    """The day folder last made active, or "". Persisted so the Days lane
    remembers its selection across restarts, the way a trip and an album do —
    it used to live only as an attribute on the main window."""
    return _load_raw().get("active", "") or ""


def set_active_day(folder):
    """Remember (or, with "", forget) the active day."""
    data = _load_raw()
    data["active"] = str(folder or "")
    _save_raw(data)


def load_days():
    """Return the list of registered days: [{folder, date, status}], newest date
    first. Silently returns [] when the registry is absent or unreadable."""
    days = _load_raw().get("days", [])
    if not isinstance(days, list):
        return []
    # Drop malformed rows defensively.
    clean = [d for d in days if isinstance(d, dict) and d.get("folder")]
    clean.sort(key=lambda d: d.get("date", ""), reverse=True)
    return clean


def save_days(days):
    # Merge into the existing document — writing {"days": days} would drop the
    # "active" marker every time a day was added, renamed or removed.
    data = _load_raw()
    data["days"] = days
    _save_raw(data)


def _norm(folder):
    return os.path.normcase(os.path.normpath(str(folder)))


def find_day(folder):
    target = _norm(folder)
    for d in load_days():
        if _norm(d["folder"]) == target:
            return d
    return None


def registered_folders():
    """Every registered day folder, normalised for comparison.

    Exists so other lanes can ask "is this folder already claimed as a Day?"
    without each growing its own copy of _norm — the unfiled inbox subtracts
    this set so a day tracked where it lies stops being listed as unclaimed.
    """
    return {_norm(d["folder"]) for d in load_days()}


def is_registered(folder):
    return _norm(folder) in registered_folders()


def add_day(folder):
    """Register an existing YYYY-MM-DD folder (no copying). Returns (ok, message).
    Idempotent — re-registering an existing folder just returns it."""
    folder = str(folder)
    name = Path(folder).name
    if not is_day_folder_name(name):
        return False, f"Folder name must be a date (YYYY-MM-DD), got '{name}'."
    if not os.path.isdir(folder):
        return False, f"Folder does not exist: {folder}"
    days = load_days()
    target = _norm(folder)
    if any(_norm(d["folder"]) == target for d in days):
        return True, "Day is already in the list."
    days.append({"folder": folder, "date": name, "status": STATUS_ACTIVE})
    save_days(days)
    return True, f"Registered day {name}."


def set_status(folder, status):
    days = load_days()
    target = _norm(folder)
    changed = False
    for d in days:
        if _norm(d["folder"]) == target:
            d["status"] = status
            changed = True
    if changed:
        save_days(days)
    return changed


def remove_day(folder):
    """Remove a Day from the list. Never deletes any picture data on disk."""
    days = load_days()
    target = _norm(folder)
    kept = [d for d in days if _norm(d["folder"]) != target]
    if len(kept) != len(days):
        save_days(kept)
        return True
    return False
