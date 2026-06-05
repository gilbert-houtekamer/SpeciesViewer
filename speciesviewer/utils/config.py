import configparser
import os
import platform
from pathlib import Path

# 🖥️ Get machine name
MACHINE_NAME = platform.node().lower()

# 📁 Config file path
CONFIG_DIR = Path(__file__).resolve().parent.parent.parent  # Goes from config.sys → utils → qt → SpeciesViewer 
CONFIG_FILENAME = f"sv_config_{MACHINE_NAME}.ini"
CONFIG_PATH = CONFIG_DIR / CONFIG_FILENAME

# 🧠 Singleton config object
_config = configparser.ConfigParser()

# 🛠️ Load or create config
if not os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write('[Global]\nconfig_version=1.0\nusername=\n[Repositories]\nrepo_paths=\n')
_config.read(CONFIG_PATH)

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

def save_window_state(key, *, x=None, y=None, w=None, h=None, cols=None):
    if not _config.has_section(_WINDOW_SECTION):
        _config.add_section(_WINDOW_SECTION)
    for sub, val in (("x", x), ("y", y), ("w", w), ("h", h), ("cols", cols)):
        if val is not None:
            _config.set(_WINDOW_SECTION, f"{key}_{sub}", str(int(val)))
    persist_config()

def get_window_state(key):
    """Return {x, y, w, h, cols} for `key`; missing values are None."""
    def _get(sub):
        opt = f"{key}_{sub}"
        if _config.has_option(_WINDOW_SECTION, opt):
            try:
                return _config.getint(_WINDOW_SECTION, opt)
            except ValueError:
                return None
        return None
    return {sub: _get(sub) for sub in ("x", "y", "w", "h", "cols")}

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

# 🔄 Optional: Upgrade logic
def upgrade_config_if_needed(latest_version=1.1):
    current_version = float(_config.get("Global", "config_version", fallback="0.0"))
    if current_version < latest_version:
        print(f"🔄 Upgrading config from {current_version} to {latest_version}")
        _config.set("Global", "config_version", str(latest_version))
        persist_config()
