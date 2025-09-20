# util/trip_config.py

import configparser

CONFIG_FILE = "sv_config.ini"

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

def read_trip_ini(trip_root: str):
    import os
    from configparser import ConfigParser

    ini_path = os.path.join(trip_root, "trip.ini")
    if not os.path.isfile(ini_path):
        return None  # No ini file yet

    parser = ConfigParser()
    parser.read(ini_path, encoding="utf-8")

    result = {
        "trip_banner": parser.get("trip_config", "trip_banner", fallback=""),
        "trip_picture": parser.get("trip_config", "trip_picture", fallback=""),
        "trip_name": parser.get("trip_config", "trip_name", fallback=""),
        "fromdate": parser.get("trip_config", "fromdate", fallback=""),
        "todate": parser.get("trip_config", "todate", fallback=""),
        "title_nl": parser.get("trip_text", "title_nl", fallback=""),
        "message_nl": parser.get("trip_text", "message_nl", fallback=""),
        "title_en": parser.get("trip_text", "title_en", fallback=""),
        "message_en": parser.get("trip_text", "message_en", fallback=""),
    }
    return result  

