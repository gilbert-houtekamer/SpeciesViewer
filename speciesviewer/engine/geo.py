"""Tiny geo helpers shared by the map view and the master DB.

PyQt-free on purpose (the DB layer imports parse_gps_dms, and the standalone
tools can too). The map's Web-Mercator projection lives in gui/map_view.py; this
module only turns stored coordinate strings into signed decimal degrees.
"""

from __future__ import annotations

import re

# Matches one "32 deg 0' 55.66" N" component (minutes/seconds optional).
_DMS = re.compile(
    r"""(\d+(?:\.\d+)?)\s*deg\s*
        (?:(\d+(?:\.\d+)?)\s*'?\s*)?      # minutes (optional)
        (?:(\d+(?:\.\d+)?)\s*"?\s*)?      # seconds (optional)
        ([NSEW])""",
    re.IGNORECASE | re.VERBOSE,
)


def _one(m: "re.Match") -> float:
    deg = float(m.group(1))
    minutes = float(m.group(2) or 0.0)
    seconds = float(m.group(3) or 0.0)
    val = deg + minutes / 60.0 + seconds / 3600.0
    if m.group(4).upper() in ("S", "W"):
        val = -val
    return val


def parse_gps_dms(s: str):
    """Parse a stored GPSPosition into signed decimal ``(lat, lon)`` or None.

    Handles the exiftool DMS form ("32 deg 0' 55.66\" N, 110 deg 38' 4.01\" W")
    and a plain decimal fallback ("32.0154, -110.634"). Returns None on anything
    unparseable or out of range."""
    s = (s or "").strip()
    if not s:
        return None
    matches = list(_DMS.finditer(s))
    if len(matches) >= 2:
        lat, lon = _one(matches[0]), _one(matches[1])
    else:
        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", s)
        if len(nums) < 2:
            return None
        lat, lon = float(nums[0]), float(nums[1])
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return (lat, lon)
    return None
