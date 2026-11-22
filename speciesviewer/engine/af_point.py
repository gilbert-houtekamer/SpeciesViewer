"""AF point — where the camera says it focused (OM System / Olympus ORF).

ONE JOB: read the camera's selected AF point and its subject-detection state for
a batch of pictures, as a box in fractions of the UPRIGHT frame. Read-only.

INVARIANT: a picture without a usable AF record gets None — never a guess. Only
OM System / Olympus ORFs record one usable here: `AFPointSelected` moves with
subject tracking (`AFAreas` is the fixed target frame — useless), and
`AISubjectTrackingMode` says whether the camera found a subject. The Nikons
record only a named AF point, not mapped (yet).

Shown, not counted: in the OM-1 pictures the AF point often sits on a twig or
in the sky beside the bird, so it is drawn for you to judge, while the score
looks at the sharpest tiles (2026-11-22).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_BOX = re.compile(r"\((\d+),(\d+)\)-\((\d+),(\d+)\)")
_PCT = re.compile(r"\((\d+)%,(\d+)%\)")


def _upright(box, orientation):
    """AF coordinates are on the sensor (landscape); the preview is turned upright."""
    x0, y0, x1, y1 = box
    if orientation == 6:                     # turned 90 degrees clockwise to view
        return (1 - y1, x0, 1 - y0, x1)
    if orientation == 8:                     # turned 270 degrees clockwise
        return (y0, 1 - x1, y1, 1 - x0)
    if orientation == 3:
        return (1 - x1, 1 - y1, 1 - x0, 1 - y0)
    return box


def _key(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def parse(row: dict):
    """(box | None, found | None) from one exiftool -j row."""
    box = None
    m = _PCT.search(str(row.get("AFPointSelected", "")))
    if m:
        x, y = int(m.group(1)) / 100, int(m.group(2)) / 100
        box = (x - 0.02, y - 0.02, x + 0.02, y + 0.02)
    else:
        boxes = [tuple(int(v) / 255 for v in b)
                 for b in _BOX.findall(str(row.get("AFAreas", "")))]
        if boxes:
            box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes))
    if box:
        try:
            box = _upright(box, int(row.get("Orientation") or 1))
        except (TypeError, ValueError):
            pass
    track = str(row.get("AISubjectTrackingMode", ""))
    return box, (("Object Found" in track) if track else None)


def read(paths) -> dict[str, tuple]:
    """{normalised path: (box | None, found | None)} for the ORFs among `paths`,
    in ONE batched exiftool run. Other files are left out."""
    from engine.exif_writer import run_exiftool_files_parallel
    orfs = [Path(p) for p in paths if str(p).lower().endswith(".orf")]
    if not orfs:
        return {}
    out = {}
    res = run_exiftool_files_parallel(
        ["-j", "-AFAreas", "-AFPointSelected", "-AISubjectTrackingMode", "-Orientation#"],
        orfs)
    for cp in res:
        try:
            rows = json.loads(cp.stdout or "[]")
        except ValueError:
            continue
        for r in rows:
            out[_key(r.get("SourceFile", ""))] = parse(r)
    return out


def lookup(found: dict, path) -> tuple:
    return found.get(_key(path), (None, None))
