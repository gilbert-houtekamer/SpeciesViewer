import json
from pathlib import Path

CACHE_FILE = Path("sv_gui_cache.json")

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
