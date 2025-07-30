# util/trip_config.py

import configparser

CONFIG_FILE = "wx_config.ini"

def get_trips():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return config.get("trips", "names", fallback="").split(",")

def add_trip(name):
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    trips = config.get("trips", "names", fallback="").split(",")
    if name not in trips:
        trips.append(name)
    config["trips"] = {"names": ",".join(trips)}
    with open(CONFIG_FILE, "w") as f:
        config.write(f)

def remove_trip(name):
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    trips = config.get("trips", "names", fallback="").split(",")
    if name in trips:
        trips.remove(name)
    config["trips"] = {"names": ",".join(trips)}
    with open(CONFIG_FILE, "w") as f:
        config.write(f)
