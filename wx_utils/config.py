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
