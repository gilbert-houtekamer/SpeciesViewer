import json
import os
import time
from pathlib import Path

from utils.config import CONFIG_DIR

# Beside the machine config, never relative to the working directory: a bare
# relative path silently started a second, empty cache whenever the app was
# launched from another folder.
CACHE_FILE = CONFIG_DIR / "sv_gui_cache.json"

def load_gui_cache():
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_gui_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)

# add/update value for key in section
def update_cache_gui_section(section, key, value):
    cache = load_gui_cache()
    if section not in cache:
        cache[section] = {}
    cache[section][key] = value
    save_gui_cache(cache)

# get value for this key, if it there is.
def get_cache_gui_section(section, key):
    cache = load_gui_cache()
    if section not in cache:
       return ""
    if key in cache[section]:
        return cache[section][key]
    return ""

def add_recent_gui_project(path):
    cache = load_gui_cache()
    recent = cache.get("recent_projects", [])
    if path in recent:
        recent.remove(path)
    recent.insert(0, path)
    cache["recent_projects"] = recent[:10]  # Keep only last 10
    save_gui_cache(cache)


def usage_key(path):
    """Normalize a path so the same folder always maps to the same key,
    regardless of case or separator style."""
    return os.path.normcase(os.path.normpath(str(path)))


def record_gui_usage(path):
    """Bump last-opened timestamp + open count for `path` — backs the
    Recent/Frequent sort modes on the Days/Trips/Albums lists. Call this only
    from a genuine user click, not from startup auto-restore, so counts
    reflect actual use."""
    key = usage_key(path)
    cache = load_gui_cache()
    section = cache.setdefault("usage_stats", {})
    stats = section.get(key, {})
    stats["last_opened"] = time.time()
    stats["open_count"] = stats.get("open_count", 0) + 1
    section[key] = stats
    save_gui_cache(cache)


def get_all_gui_usage():
    """The whole usage_stats section in one read, so sorting a list of N
    items costs one file read rather than N."""
    return load_gui_cache().get("usage_stats", {})
