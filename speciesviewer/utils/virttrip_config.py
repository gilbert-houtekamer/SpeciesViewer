import configparser
import os
import re

_SECTION_RE = re.compile(r'^\s*\[.+\]\s*$')
_KEYVAL_RE  = re.compile(r'^[^\s\[;#][^=:]*[=:]')


def _coerce_orphan_continuations(text: str) -> str:
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


def read_virttrip_ini(trip_root: str):
    ini_path = os.path.join(trip_root, "virttrip.ini")
    if not os.path.isfile(ini_path):
        return None

    with open(ini_path, "r", encoding="utf-8") as f:
        content = f.read()
    parser = configparser.ConfigParser()
    parser.read_string(_coerce_orphan_continuations(content))

    return {
        "trip_type":   parser.get("trip_config", "trip_type",   fallback="virtual"),
        "trip_banner": parser.get("trip_config", "trip_banner", fallback=""),
        "trip_picture":parser.get("trip_config", "trip_picture",fallback=""),
        "trip_name":   parser.get("trip_config", "trip_name",   fallback=""),
        # WP4 explicit identities + link back to the origin trip (absent in
        # pre-WP4 virttrip.ini → "" → resolver falls back to today's derivation)
        "trip_id":     parser.get("trip_config", "trip_id",     fallback=""),
        "web_slug":    parser.get("trip_config", "web_slug",    fallback=""),
        "source_trip_id": parser.get("trip_config", "source_trip_id", fallback=""),
        "fromdate":    parser.get("trip_config", "fromdate",    fallback=""),
        "todate":      parser.get("trip_config", "todate",      fallback=""),
        "title_nl":    parser.get("trip_text",   "title_nl",    fallback=""),
        "message_nl":  parser.get("trip_text",   "message_nl",  fallback=""),
        "title_en":    parser.get("trip_text",   "title_en",    fallback=""),
        "message_en":  parser.get("trip_text",   "message_en",  fallback=""),
    }
