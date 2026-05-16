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

# 🔄 Optional: Upgrade logic
def upgrade_config_if_needed(latest_version=1.1):
    current_version = float(_config.get("Global", "config_version", fallback="0.0"))
    if current_version < latest_version:
        print(f"🔄 Upgrading config from {current_version} to {latest_version}")
        _config.set("Global", "config_version", str(latest_version))
        persist_config()
