"""Per-viewer CARD CORNER SETS + FRAME COLOUR — a user Look & Feel setting.

A framed viewer shows up to four corner badges over the photo. Which datum sits
in each corner, and whether the thick frame is the colour label or plain olive,
is configurable per viewer and defaults to the values the app has always used.

Unlike the colour scheme / theme (baked at import → restart), these are read at
CARD-BUILD time, so a change applies the next time a viewer is opened.
"""

from utils.config import get_setting, set_setting

# corner datum key → dialog label (list order = dialog order).
CORNER_OPTIONS = [
    ("none",     "— nothing —"),
    ("format",   "Format (RAW/JPG)"),
    ("date",     "Capture date"),
    ("rate",     "Rate score"),
    ("camera",   "Camera"),
    ("lock",     "Committed lock"),
    ("colour",   "Colour dot"),
    ("filename", "Filename (UPN)"),
]

# thick-frame colour: the label floods it (today's editing look) or always olive.
FRAME_OPTIONS = [("colour", "Colour label"), ("olive", "Olive (always)")]

# viewers whose corners are configurable (the framed PictureCard hosts). The
# large-viewer / map strips join when maps are built.
VIEWERS = [
    ("species", "Trip / Master / Album species"),
    ("preview", "Species image viewer"),
]

POSITIONS = ("tl", "tr", "bl", "br")
POSITION_LABELS = {"tl": "Top-left", "tr": "Top-right", "bl": "Bottom-left", "br": "Bottom-right"}

# the app's long-standing sets — the defaults (frame = colour label).
DEFAULTS = {
    "species": {"tl": "format", "tr": "lock", "bl": "date", "br": "rate", "frame": "colour"},
    "preview": {"tl": "format", "tr": "lock", "bl": "date", "br": "rate", "frame": "colour"},
}
_VALID = {k for k, _ in CORNER_OPTIONS}
_VALID_FRAME = {k for k, _ in FRAME_OPTIONS}


def corner_config(viewer_key: str) -> dict:
    """The 4 corner datums + frame colour for a viewer, from config (defaults
    when unset or invalid)."""
    d = DEFAULTS.get(viewer_key, DEFAULTS["species"])
    cfg = {}
    for k in POSITIONS:
        v = (get_setting("cardcorners", f"{viewer_key}_{k}", fallback=d[k]) or d[k]).strip()
        cfg[k] = v if v in _VALID else d[k]
    fr = (get_setting("cardcorners", f"{viewer_key}_frame", fallback=d["frame"]) or d["frame"]).strip()
    cfg["frame"] = fr if fr in _VALID_FRAME else d["frame"]
    return cfg


def set_corner_config(viewer_key: str, cfg: dict) -> None:
    for k in POSITIONS + ("frame",):
        if k in cfg:
            set_setting("cardcorners", f"{viewer_key}_{k}", cfg[k])


def render_datum(datum: str, item) -> str | None:
    """Text for a corner datum from a PictureItem, or None to show nothing.
    'colour', 'lock' and 'rate' are rendered specially by the card itself."""
    if datum == "format":
        exts = getattr(item, "file_exts", None) or []
        primary = next((e for e in exts if e.upper() not in ("XMP", "DOP", "DNG")), None)
        return ("JPG" if item.has_jpg else (primary or "RAW")).upper()
    if datum == "date":
        return (item.date or "")[:10].replace(":", "-") or None
    if datum == "camera":
        return (getattr(item.exif, "model", "") or "") or None
    if datum == "filename":
        return item.key
    return None
