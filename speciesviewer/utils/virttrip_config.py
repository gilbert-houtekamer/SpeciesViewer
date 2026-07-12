import configparser
import os
import re
from pathlib import Path

_SECTION_RE = re.compile(r'^\s*\[.+\]\s*$')
_KEYVAL_RE  = re.compile(r'^[^\s\[;#][^=:]*[=:]')

# Review state of one day's blog ini. A day blog is either absent, an
# auto-generated draft nobody has looked at yet (amber), or text a human wrote
# or blessed (green).
BLOG_MISSING  = "missing"
BLOG_DRAFT    = "draft"
BLOG_REVIEWED = "reviewed"

# Auto-generated text has always carried this marker in its message. It is the
# only signal available for ini files written before `reviewed` existed.
_AUTO_MARKERS = ("(auto-tekst)", "(auto-generated)")


def blog_review_state(ini_path) -> str:
    """Classify one day's blog ini as missing / draft / reviewed.

    `[blog_config] reviewed` is authoritative when present: svv3WriteBlog writes
    `false` on every blog it auto-generates, and the blog editor writes `true`
    when you accept the text.

    Older ini files predate the flag. A blog migrated from a real trip is
    genuine hand-written text, so absence of the flag means reviewed — EXCEPT
    when the message still carries the auto-generator's marker, which means the
    old auto-writer produced it and nobody has been through it since.
    """
    path = Path(ini_path)
    if not path.is_file():
        return BLOG_MISSING

    cfg = configparser.RawConfigParser()
    try:
        cfg.read(str(path), encoding="utf-8")
    except (UnicodeDecodeError, configparser.Error):
        cfg = None
    if cfg is not None and cfg.has_option("blog_config", "reviewed"):
        try:
            return (BLOG_REVIEWED if cfg.getboolean("blog_config", "reviewed")
                    else BLOG_DRAFT)
        except ValueError:
            pass

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return BLOG_REVIEWED
    return BLOG_DRAFT if any(m in text for m in _AUTO_MARKERS) else BLOG_REVIEWED


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
