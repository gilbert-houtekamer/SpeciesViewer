import configparser
import os
import sys
import os

CONFIG_FILE = "wx_config.ini"

config = configparser.ConfigParser()

# Load or create
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'w') as f:
        f.write('[Global]\nusername=\n[Repositories]\nrepo_paths=\n')

config.read(CONFIG_FILE)

def get_global_settings():
    return dict(config.items("Global"))

def get_repo_settings():
    return config.get("Repositories", "repo_paths").split(";")

def save_global_settings(username):
    config.set("Global", "username", username)
    with open(CONFIG_FILE, 'w') as f:
        config.write(f)

def save_repo_settings(repo_paths):
    joined = ";".join(repo_paths)
    config.set("Repositories", "repo_paths", joined)
    with open(CONFIG_FILE, 'w') as f:
        config.write(f)

def get_setting(section, key, fallback=None):
    return config.get(section, key, fallback=fallback)

def get_int_setting(section, key, fallback=0):
    return config.getint(section, key, fallback=fallback)

def set_setting(section, key, value):
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, key, str(value))
    with open(CONFIG_FILE, "w") as f:
        config.write(f)