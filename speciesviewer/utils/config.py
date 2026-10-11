import configparser
import os
import platform
import shutil
from pathlib import Path
from utils.ini_io import read_ini
from utils import masters

# 🖥️ Get machine name
MACHINE_NAME = platform.node().lower()

# 📁 Per-machine files live in the per-OS app-data folder, never in the source
# tree (docs/plans/config_out_of_repo.md), in the ACTIVE MASTER's folder
# (utils/masters.py — each master has its own; the first master's is the
# app-data folder itself):
#   config/  settings — what you would set up again on a new machine
#   state/   remembered state — what the app re-learns by being used
# The repository root is where they used to live; it is only read once, to
# migrate, and is the fallback when the app-data folder cannot be created —
# both only for the master that existed before masters.json.
LEGACY_DIR = Path(__file__).resolve().parent.parent.parent  # utils → speciesviewer → repo root
CONFIG_FILENAME = f"sv_config_{MACHINE_NAME}.ini"

# What a migration did, for SpeciesViewer.py to log once the logger exists
# (this module is imported long before that).
MIGRATION_NOTES = []


def _app_dir(sub):
    d = masters.ACTIVE_DIR / sub
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError as e:
        if not masters.IS_LEGACY_FOLDER:
            # A newer master has no repo-root files to fall back on; the repo
            # root would be the legacy master's — refuse rather than mix.
            raise
        MIGRATION_NOTES.append(f"⚠ Could not create {d} ({e}); using {LEGACY_DIR}")
        return LEGACY_DIR


CONFIG_DIR = _app_dir("config")
STATE_DIR = _app_dir("state")
CONFIG_PATH = CONFIG_DIR / CONFIG_FILENAME


def _migrate(name, new_dir):
    """One-time move of `name` from the repository root to `new_dir`: copy it,
    then rename the old file to `<name>.moved-to-appdata`. Nothing is deleted,
    and the rename means a hand-edit of the old file cannot silently go to a
    file nobody reads any more. Does nothing once the new file exists."""
    old, new = LEGACY_DIR / name, new_dir / name
    if not masters.IS_LEGACY_FOLDER or new_dir == LEGACY_DIR or new.exists() or not old.is_file():
        return
    try:
        shutil.copy2(old, new)
        old.rename(old.with_name(name + ".moved-to-appdata"))
        MIGRATION_NOTES.append(f"Moved {name} → {new_dir}")
    except OSError as e:
        MIGRATION_NOTES.append(f"⚠ Could not move {name} to {new_dir}: {e}")


_migrate(CONFIG_FILENAME, CONFIG_DIR)
_migrate("photolab_denoise.ini", CONFIG_DIR)
_migrate(f"days_{MACHINE_NAME}.json", STATE_DIR)
_migrate("sv_gui_cache.json", STATE_DIR)

# 🧠 Singleton config object
_config = configparser.ConfigParser()

# 🛠️ Load or create config
if not os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write('[Global]\nconfig_version=1.0\nusername=\n[Repositories]\nrepo_paths=\n')
# UTF-8 both ways — persist_config writes UTF-8, so anything else here would
# corrupt accented paths on a machine whose locale is not UTF-8 (Windows).
read_ini(_config, CONFIG_PATH)

# 🧱 INVARIANT: these sections and keys always exist — empty meaning "not set
# up yet". Code all over reads them as _config["trip"]["tripbiotadatabase"],
# which raises KeyError on a fresh config (a new user, a new master). Filling
# them here once is the fix for all those reads; existing values are untouched.
_ALWAYS_PRESENT = {
    "Global": (),
    "master": ("masterbiotadatabase", "masterbiotawebroot"),
    "trip": ("tripbiotadatabase", "tripbiotawebroot"),
    "virttrip": ("virttripbiotadatabase", "virttripbiotawebroot"),
    "project": ("picturedirectory",),
    "layout": (),
    "utilities": (),
    "avibase": (),
}
for _section, _keys in _ALWAYS_PRESENT.items():
    if not _config.has_section(_section):
        _config.add_section(_section)
    for _key in _keys:
        if not _config.has_option(_section, _key):
            _config.set(_section, _key, "")

# The master's web root is never empty: about twenty readers ask for it with
# fallback="/nature/", and the line above made the key always PRESENT — so an
# empty value no longer reached that fallback. A trip created in such a master
# recorded master_repository_web = "" and its "Trips" link pointed into the trip
# (found 2026-10-11). "/nature/" is the layout those fallbacks already assume.
if not _config.get("master", "masterbiotawebroot").strip():
    _config.set("master", "masterbiotawebroot", "/nature/")

# ✅ Centralized write function
def persist_config():
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        _config.write(f)

# 🔍 Accessors
def get_global_settings():
    return dict(_config.items("Global"))

def get_repo_settings():
    return _config.get("Repositories", "repo_paths", fallback="").split(";")

def get_setting(section, key, fallback=None):
    return _config.get(section, key, fallback=fallback)

def debug_enabled():
    """True when 'Enable debug functionality' is on (Global Settings). Gates the
    File ▸ Admin diagnostics, verbose DEBUG logging, and the splash/DB diag prints."""
    return _config.get("Global", "debug_tools", fallback="no") == "yes"

def get_int_setting(section, key, fallback=0):
    return _config.getint(section, key, fallback=fallback)

# 📝 Mutators
def set_setting(section, key, value):
    if not _config.has_section(section):
        _config.add_section(section)
    _config.set(section, key, str(value))
    persist_config()

def set_trip_settings(trip_root, trip_web_root, project_directory):
    _config.set("trip", "tripbiotadatabase", trip_root)
    _config.set("trip", "tripbiotawebroot", trip_web_root)
    _config.set("project", "picturedirectory", project_directory)
    persist_config()

def save_global_settings(username):
    _config.set("Global", "username", username)
    persist_config()

def save_repo_settings(repo_paths):
    joined = ";".join(repo_paths)
    _config.set("Repositories", "repo_paths", joined)
    persist_config()

# 🪟 Per-dialog window geometry + column count (machine-specific, so it lives
# in this per-machine .ini). Stored as plain ints under [WindowState] keyed
# "<dialog>_x/_y/_w/_h/_cols" so the values stay human-readable.
_WINDOW_SECTION = "WindowState"

def save_window_state(key, *, x=None, y=None, w=None, h=None, cols=None,
                      vsplit=None, hsplit=None, fsplit=None):
    if not _config.has_section(_WINDOW_SECTION):
        _config.add_section(_WINDOW_SECTION)
    for sub, val in (("x", x), ("y", y), ("w", w), ("h", h), ("cols", cols)):
        if val is not None:
            _config.set(_WINDOW_SECTION, f"{key}_{sub}", str(int(val)))
    for sub, sizes in (("vsplit", vsplit), ("hsplit", hsplit), ("fsplit", fsplit)):
        if sizes is not None:
            _config.set(_WINDOW_SECTION, f"{key}_{sub}", ",".join(str(int(v)) for v in sizes))
    persist_config()

def get_window_state(key):
    """Return {x, y, w, h, cols, vsplit, hsplit, fsplit} for `key`; missing values are None."""
    def _get(sub):
        opt = f"{key}_{sub}"
        if _config.has_option(_WINDOW_SECTION, opt):
            try:
                return _config.getint(_WINDOW_SECTION, opt)
            except ValueError:
                return None
        return None
    def _get_split(sub):
        opt = f"{key}_{sub}"
        if _config.has_option(_WINDOW_SECTION, opt):
            try:
                return [int(v) for v in _config.get(_WINDOW_SECTION, opt).split(",")]
            except ValueError:
                return None
        return None
    return ({sub: _get(sub) for sub in ("x", "y", "w", "h", "cols")}
            | {"vsplit": _get_split("vsplit"), "hsplit": _get_split("hsplit"),
               "fsplit": _get_split("fsplit")})

# 📂 Trips / VirtTrips root directories
# These return the explicit config key when present; otherwise derive via .parent
# of the active database path (backward-compatible fallback for old configs).

def get_trips_root() -> str:
    explicit = _config.get("trip", "tripsrootdirectory", fallback="")
    if explicit:
        return explicit
    active = _config.get("trip", "tripbiotadatabase", fallback="")
    return str(Path(active).parent) if active else ""

def get_virttrips_root() -> str:
    explicit = _config.get("virttrip", "virttriprootdirectory", fallback="")
    if explicit:
        return explicit
    active = _config.get("virttrip", "virttripbiotadatabase", fallback="")
    return str(Path(active).parent) if active else ""

def get_currentpictures_root() -> str:
    """The per-machine base folder that holds every trip's source pictures
    (e.g. D:\\CurrentPictures). A trip.ini stores its picture_root RELATIVE to
    this, so the shared trip.ini stays portable across machines/OSes where the
    absolute CurrentPictures path differs. Empty when not configured — callers
    then fall back to the active day's parent (today's behaviour). See WP8."""
    return _config.get("currentpictures", "rootdirectory", fallback="").strip()

def get_gpslogs_root() -> str:
    """The base folder holding one GPS tracklog per capture day, named
    `<root>/<year>/<yyyymmdd>.log` (or `.gpx`) — the convention
    engine.tracklog_geotag.find_day_tracklog looks for.

    `[gpslogs] rootdirectory` when set, else **`<CurrentPictures>/gpslogs`**,
    which is the convention the machines actually follow. The fallback exists
    because a machine with no [gpslogs] section had no tracklog folder at all:
    the backup then skipped the logs and the restore had nowhere to put them,
    both silently. Deriving it from a root that IS always configured means the
    logs have a home on every machine without a second per-machine key.

    Still "" only when neither is configured, in which case auto-discovery is
    unavailable and "Geotag from tracklog…" is used by browsing to a file."""
    configured = _config.get("gpslogs", "rootdirectory", fallback="").strip()
    if configured:
        return configured
    pictures = get_currentpictures_root()
    return str(Path(pictures) / "gpslogs") if pictures else ""

# 🔄 Optional: Upgrade logic
def upgrade_config_if_needed(latest_version=1.1):
    current_version = float(_config.get("Global", "config_version", fallback="0.0"))
    if current_version < latest_version:
        print(f"🔄 Upgrading config from {current_version} to {latest_version}")
        _config.set("Global", "config_version", str(latest_version))
        persist_config()
