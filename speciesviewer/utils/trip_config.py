# util/trip_config.py

import configparser
import re
from pathlib import Path
from utils.ini_io import read_ini

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
    read_ini(config, CONFIG_FILE)
    return config.get("trips", "names", fallback="").split(",")

def add_trip(name):
    config = configparser.ConfigParser()
    read_ini(config, CONFIG_FILE)
    trips = config.get("trips", "names", fallback="").split(",")
    if name not in trips:
        trips.append(name)
    config["trips"] = {"names": ",".join(trips)}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        config.write(f)

def remove_trip(name):
    config = configparser.ConfigParser()
    read_ini(config, CONFIG_FILE)
    trips = config.get("trips", "names", fallback="").split(",")
    if name in trips:
        trips.remove(name)
    config["trips"] = {"names": ",".join(trips)}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        config.write(f)

def read_trip_ini(trip_root: str):
    import os
    from configparser import ConfigParser

    ini_path = os.path.join(trip_root, "trip.ini")
    if not os.path.isfile(ini_path):
        return None  # No ini file yet

    # Read tolerantly, like triplib.readTrip and read_virttrip_ini: the file is
    # UTF-8 today, but older builds wrote it back without an explicit encoding
    # (→ cp1252 on Windows), so a trip with an accent in its title can hold
    # cp1252 bytes. cp1252 maps every byte, so this never raises.
    with open(ini_path, "rb") as f:
        raw = f.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("cp1252")
    parser = ConfigParser()
    parser.read_string(coerce_orphan_continuations(content))

    result = {
        "trip_banner": parser.get("trip_config", "trip_banner", fallback=""),
        "trip_picture": parser.get("trip_config", "trip_picture", fallback=""),
        "trip_name": parser.get("trip_config", "trip_name", fallback=""),
        # WP4 explicit identities (absent in pre-WP4 trip.ini → "" → resolver falls back)
        "trip_id": parser.get("trip_config", "trip_id", fallback=""),
        "web_slug": parser.get("trip_config", "web_slug", fallback=""),
        # WP8: trip's picture root, relative to per-machine CurrentPictures root
        "picture_root": parser.get("trip_config", "picture_root", fallback=""),
        "fromdate": parser.get("trip_config", "fromdate", fallback=""),
        "todate": parser.get("trip_config", "todate", fallback=""),
        # Lifecycle milestone, stamped by Retire Trip to Album; read by the
        # menu-gating predicates (Delete Trip enables only once it is set).
        "retired_to_album": parser.get("trip_config", "retired_to_album", fallback=""),
        # Per-trip name-language override (empty = follow the global default)
        "name_profile": parser.get("trip_config", "name_profile", fallback=""),
        "title_nl": parser.get("trip_text", "title_nl", fallback=""),
        "message_nl": parser.get("trip_text", "message_nl", fallback=""),
        "title_en": parser.get("trip_text", "title_en", fallback=""),
        "message_en": parser.get("trip_text", "message_en", fallback=""),
    }
    return result


# A Trip day's blog.ini can carry default text from two different origins,
# depending on how it was first created — sv4WriteBlog.py's own fallback (run
# directly, no dialog yet opened) leaves the EN fields empty; CreateBlogDialog
# ._create_default_ini (trip_process.py, the dialog's first open for a day)
# seeds real EN placeholder text. Either can be sitting on disk untouched.
_TRIP_DEFAULT_TITLES_NL = ("Standaard titel", "Dag 2: Nederlandse Titel")


def trip_blog_review_state(ini_path) -> bool:
    """True if a Trip day's blog.ini still holds default/unedited text — the
    Trip-side equivalent of virttrip_config.blog_review_state(), which Trip
    cannot reuse: Trip has no auto-generator markers (svv3WriteBlog is
    Album-only) and never wrote a `reviewed` flag before this editor existed.

    `[blog_config] reviewed` is authoritative when present (this editor writes
    it going forward, exactly like Album's). Otherwise, fall back to comparing
    titel_nl against Trip's known placeholder strings — see
    _TRIP_DEFAULT_TITLES_NL above."""
    path = Path(ini_path)
    if not path.is_file():
        return True

    cfg = configparser.RawConfigParser()
    try:
        read_ini(cfg, path)
    except configparser.Error:
        return True

    if cfg.has_option("blog_config", "reviewed"):
        try:
            return not cfg.getboolean("blog_config", "reviewed")
        except ValueError:
            pass

    titel_nl = cfg.get("blog_text", "titel_nl", fallback="").strip()
    return titel_nl in _TRIP_DEFAULT_TITLES_NL


def set_trip_flag(trip_root: str, key: str, value: str) -> bool:
    """Set a single ``key = value`` in the [trip_config] section of
    <trip_root>/trip.ini, PRESERVING the rest of the file — its ``;`` comment and
    the multi-line [trip_text] values (a configparser rewrite would drop the
    comment and reflow the text). Replaces the line if the key already exists,
    else inserts it right under the [trip_config] header. Returns True on write,
    False when there's no trip.ini / no [trip_config] section.

    Used to stamp lifecycle milestones (e.g. retired_to_album) that the menu
    gating reads cheaply. Idempotent — safe to call again to refresh the value."""
    import os
    ini_path = os.path.join(trip_root, "trip.ini")
    if not os.path.isfile(ini_path):
        return False
    with open(ini_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    key_re = re.compile(r'^\s*' + re.escape(key) + r'\s*[=:]', re.IGNORECASE)
    new_line = f"{key} = {value}"
    in_trip_config = False
    header_idx = None
    for i, line in enumerate(lines):
        if _SECTION_RE.match(line):
            in_trip_config = line.strip().lower() == "[trip_config]"
            if in_trip_config:
                header_idx = i
            continue
        if in_trip_config and key_re.match(line):
            lines[i] = new_line
            with open(ini_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return True
    if header_idx is None:
        return False
    lines.insert(header_idx + 1, new_line)
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True

