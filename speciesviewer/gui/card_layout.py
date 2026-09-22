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
    ("time",     "Capture time"),
    ("datetime", "Capture date + time"),
    ("rate",     "Rate score"),
    ("stars",    "Stars"),
    ("camera",   "Camera"),
    ("gps",      "GPS coordinates"),
    ("city",     "City"),
    ("place",    "Location (place name)"),
    ("lock",     "Committed lock"),
    ("colour",   "Colour dot"),
    ("filename", "Filename (UPN)"),
]

# Every datum above is served from the picture's ALREADY-LOADED row — the same
# rows the grid is built from — so turning one on costs a dict lookup per card
# and never a file read. That is the rule for anything added here: a corner
# badge is drawn for every visible card on every rebind, so a datum that has to
# open a file would turn a scroll into a stall.

# thick-frame colour: the label floods it (today's editing look) or always olive.
FRAME_OPTIONS = [("colour", "Colour label"), ("olive", "Olive (always)")]

# viewers whose corners are configurable. species/preview = framed PictureCard;
# strip/map_nogps/map_placed = the compact filmstrip cell (_StripItem).
VIEWERS = [
    ("species",    "Trip / Master / Album species"),
    ("preview",    "Species image viewer"),
    ("strip",      "Large-viewer filmstrip"),
    ("map_nogps",  "Map — without GPS (drag to place)"),
    ("map_placed", "Map — placed pictures"),
]

POSITIONS = ("tl", "tr", "bl", "br")
POSITION_LABELS = {"tl": "Top-left", "tr": "Top-right", "bl": "Bottom-left", "br": "Bottom-right"}

# the app's long-standing sets — the defaults (frame = colour label).
DEFAULTS = {
    "species":    {"tl": "format", "tr": "lock", "bl": "date", "br": "rate", "frame": "colour"},
    "preview":    {"tl": "format", "tr": "lock", "bl": "date", "br": "rate", "frame": "colour"},
    # compact strip cells — sparse by default.
    "strip":      {"tl": "none", "tr": "none", "bl": "stars", "br": "none", "frame": "colour"},
    "map_nogps":  {"tl": "none", "tr": "none", "bl": "filename", "br": "none", "frame": "colour"},
    "map_placed": {"tl": "none", "tr": "none", "bl": "date", "br": "none", "frame": "olive"},
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
    In the framed card, 'colour'/'lock'/'rate' are handled specially (dot / glyph
    / live label) BEFORE this is called; the strip cell uses this for all of them."""
    if datum == "format":
        exts = getattr(item, "file_exts", None) or []
        primary = next((e for e in exts if e.upper() not in ("XMP", "DOP", "DNG")), None)
        return ("JPG" if item.has_jpg else (primary or "RAW")).upper()
    # Capture date/time come from the same string, "2026:09:09 06:33:43".
    if datum == "date":
        return (item.date or "")[:10].replace(":", "-") or None
    if datum == "time":
        return (item.date or "")[11:19] or None
    if datum == "datetime":
        d = item.date or ""
        return (d[:10].replace(":", "-") + " " + d[11:19]).strip() or None
    if datum == "rate":
        return f"Rate {item.rate}" if getattr(item, "rate", None) is not None else None
    if datum == "stars":
        r = max(0, min(5, getattr(item, "rating", 0) or 0))
        return "★" * r + "☆" * (5 - r)
    if datum == "camera":
        return (getattr(item.exif, "model", "") or "") or None
    if datum == "gps":
        # Compact decimal where the provider offers it; otherwise the row's
        # stored DMS, parsed down to the same short form — a corner badge has no
        # room for 15 deg 9' 11.23" S, 56 deg 49' 6.00" W.
        txt = (getattr(item, "gps_text", "") or "").strip()
        if txt:
            return txt
        raw = (getattr(item.exif, "gps", "") or "").strip()
        if not raw:
            return None
        from engine.geo import parse_gps_dms
        ll = parse_gps_dms(raw)
        return f"{ll[0]:.5f}, {ll[1]:.5f}" if ll else raw
    if datum in ("city", "place"):
        # NO badge when the field is empty, which is the point of putting these
        # in a corner: on a day where most pictures have a place, the handful
        # that do not are the cards with a gap where the others have text.
        fields = getattr(item, "place_fields", None) or {}
        val = (fields.get("City" if datum == "city" else "Location") or "").strip()
        if not val:
            return None
        # A place name can be "Parque Estadual Encontro das Águas"; badges size
        # to their text and would run across the thumbnail.
        return val if len(val) <= 24 else val[:23] + "…"
    if datum == "filename":
        return item.key
    return None
