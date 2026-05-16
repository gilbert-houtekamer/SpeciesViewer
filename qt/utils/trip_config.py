# util/trip_config.py

import configparser
import re

CONFIG_FILE = "sv_config.ini"

_SECTION_RE = re.compile(r'^\s*\[.+\]\s*$')
# 'key = value' or 'key : value' starting at column 0; excludes comments.
_KEYVAL_RE  = re.compile(r'^[^\s\[;#][^=:]*[=:]')


def coerce_orphan_continuations(text: str) -> str:
    """Make multi-line ini values resilient to unindented continuation
    lines, which would otherwise raise configparser.ParsingError. Any
    line that is neither a [section] header nor a 'key = value'
    assignment — when encountered while inside a value — gets indented
    so configparser treats it as continuation. Blank lines inside a
    value stay blank and survive as paragraph breaks (the default
    empty_lines_in_values=True keeps them in the parsed string)."""
    out = []
    in_value = False
    for line in text.splitlines():
        if _SECTION_RE.match(line):
            in_value = False
            out.append(line)
        elif _KEYVAL_RE.match(line):
            in_value = True
            out.append(line)
        elif in_value and line.strip() and not line.startswith((' ', '\t')):
            out.append('    ' + line)
        else:
            out.append(line)
    return '\n'.join(out) + '\n'

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

    with open(ini_path, "r", encoding="utf-8") as f:
        content = f.read()
    parser = ConfigParser()
    parser.read_string(coerce_orphan_continuations(content))

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

