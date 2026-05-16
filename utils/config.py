import configparser
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "sv_config.ini")

config = configparser.ConfigParser()
config.read(CONFIG_PATH)

def get_setting(section, key, fallback=None):
    return config.get(section, key, fallback=fallback)

def get_int_setting(section, key, fallback=0):
    return config.getint(section, key, fallback=fallback)

def set_setting(section, key, value):
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, key, str(value))
    with open(CONFIG_PATH, "w") as f:
        config.write(f)
