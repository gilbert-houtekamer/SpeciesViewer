"""One focus distance for a picture, whatever the maker chose to call it.

ONE JOB: turn the assorted tags five camera makers use into the single
``MakerNotes:FocusDistance`` value the rest of SpeciesViewer already reads — the
blog's ``projectlib.getDistance``, the info panel's ``picture_provider``, and the
CSV column of that name. Nothing else in the tree should know that the makers
disagree.

INVARIANT: this does NOT add a CSV column. ``COLUMN_NAMES.values()`` is the master
CSV header, so a new column is a schema change to every data_pictures.csv in the
library; merging into the column that already exists costs nothing and reaches both
consumers for free.

Where each maker keeps it (measured on the ch.21 corpus, see docs/camera_support.md):

    Nikon      MakerNotes:FocusDistance        11.82 m   a single value
    Olympus    MakerNotes:FocusDistance        inf       a single value
    Sony       Composite:FocusDistance2        1.974 m   derived by exiftool
    Canon      MakerNotes:FocusDistanceUpper   1.14 m    a RANGE, both ends
               MakerNotes:FocusDistanceLower   0.99 m
    Panasonic  — nothing. Only HyperfocalDistance, which is a property of the lens
               and aperture, not of what the camera focused on, and would be a lie
               in this field.

Canon's range is averaged. The true subject distance is not the arithmetic mean of
the two bounds — depth of field is asymmetric about the plane of focus, so it sits
slightly nearer than the midpoint — but the bounds are a few centimetres apart at
the distances that matter here, and a deliberately simple number beats a formula
nobody can check. Called deliberately, not by accident.
"""

from __future__ import annotations

import math


def _metres(text) -> float | None:
    """The number out of an exiftool distance like "1.14 m". None for an empty
    value, for anything that does not parse, and for "inf" — which is a real
    reading (focused at infinity) but not a number to average.

    ``math.isfinite`` and not a string test, because ``float("inf")`` parses
    happily: without it the infinity case averaged to inf and formatted as the
    nonsense "inf m"."""
    if not text:
        return None
    s = str(text).strip().removesuffix("m").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 0 else None


def focus_distance(d: dict) -> str:
    """The picture's focus distance as a display string, or "" if the file has none.

    ``d`` is one exiftool result dict read with ``-G`` (group 0), i.e. the shape the
    master EXIF scan already works in. Order of preference is by directness: a value
    the camera recorded beats one exiftool derived, which beats one we averaged."""
    direct = d.get("MakerNotes:FocusDistance") or d.get("EXIF:SubjectDistance")
    if direct:
        return str(direct)

    sony = d.get("Composite:FocusDistance2")
    if sony:
        return str(sony)

    # Canon: a range. Take min/max rather than trusting the names — exiftool has
    # been seen reporting Upper below Lower on the same body, and an average does
    # not care which end is which anyway.
    ends = [v for v in (_metres(d.get("MakerNotes:FocusDistanceUpper")),
                        _metres(d.get("MakerNotes:FocusDistanceLower")))
            if v is not None]
    if ends:
        mid = sum(ends) / len(ends)
        # Two decimals matches what the makers write; getDistance() trims further
        # for display, and does its own thing above 10 m.
        return f"{mid:.2f} m"

    # Both Canon bounds "inf" means focused at infinity, which is a reading, not a
    # gap — report it rather than leaving the field blank.
    if d.get("MakerNotes:FocusDistanceUpper") or d.get("MakerNotes:FocusDistanceLower"):
        return "inf"
    return ""
