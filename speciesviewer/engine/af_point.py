"""AF point — where the camera says it focused (OM System ORF, Nikon NEF).

ONE JOB: read the camera's selected AF point and its subject-detection state for
a batch of pictures, as a box in fractions of the UPRIGHT frame. Read-only.

INVARIANT: a picture without a usable AF record gets None — never a guess.

  - OM System / Olympus ORF: `AFPointSelected` is a position (it moves with
    subject tracking; `AFAreas` is the fixed target frame — useless), and
    `AISubjectTrackingMode` says whether the camera found a subject. Exact.
  - Nikon NEF (D7100, D7500, D500): only the NAME of the point in the AF grid
    (`PrimaryAFPoint` "D6"; the D500 also `AFPointsInFocus`). NIKON_GRIDS
    places the grid on the frame: the centre point in the middle of the frame,
    the spacing MEASURED from the master (the median sharpest spot of ~1600
    pictures per named point, 2026-11-22) — so the position is an ESTIMATE
    (`estimated`), and the true spacing is probably a little wider. The D90's
    11 named points were too noisy to place; it is left out.

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


# model -> (centre column, centre row (0 = A), column step, row step) as
# fractions of the frame width / height.
NIKON_GRIDS = {
    "NIKON D7100": (6, 2, 0.040, 0.062),      # 51 points, A–E × 1–11, centre C6
    "NIKON D7500": (6, 2, 0.040, 0.062),      # same module; too few pictures to measure
    "NIKON D500": (9, 4, 0.030, 0.037),       # 153 points, A–I × 1–17, centre E9
}
_NAMED = re.compile(r"([A-I])(\d{1,2})")


def nikon_box(model: str, name: str):
    """The estimated box of a named Nikon AF point (sensor frame), or None."""
    grid = NIKON_GRIDS.get((model or "").strip())
    m = _NAMED.fullmatch((name or "").split(" (")[0].strip())
    if grid is None or m is None:
        return None
    c0, r0, dx, dy = grid
    x = 0.5 + (int(m.group(2)) - c0) * dx
    y = 0.5 + (ord(m.group(1)) - ord("A") - r0) * dy
    return (x - dx * 0.45, y - dy * 0.45, x + dx * 0.45, y + dy * 0.45)


def _key(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def parse_info(row: dict) -> dict:
    """Everything about the AF point of one exiftool -j row: box (upright
    frame) | None, found (subject detected) | None, in_focus [boxes] (D500),
    name (the camera's name for the point), estimated (a Nikon grid guess)."""
    model = str(row.get("Model", ""))
    orient = 1
    try:
        orient = int(row.get("Orientation") or 1)
    except (TypeError, ValueError):
        pass
    if model in NIKON_GRIDS:
        name = str(row.get("PrimaryAFPoint", "")).split(" (")[0].strip()
        box = nikon_box(model, name)
        focus = []
        for n in re.split(r"[,\s]+", str(row.get("AFPointsInFocus", ""))):
            b = nikon_box(model, n)
            if b and n != name:
                focus.append(_upright(b, orient))
        return {"box": _upright(box, orient) if box else None, "found": None,
                "in_focus": focus, "name": name if box else "", "estimated": True}
    box, found = parse(row)
    return {"box": box, "found": found, "in_focus": [], "name": "", "estimated": False}


def parse(row: dict):
    """(box | None, found | None) from one exiftool -j row (OM System)."""
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


def read(paths) -> dict[str, dict]:
    """{normalised path: parse_info(...)} for the ORFs and NEFs among `paths`,
    in ONE batched exiftool run. Other files are left out."""
    from engine.exif_writer import run_exiftool_files_parallel
    raws = [Path(p) for p in paths if str(p).lower().endswith((".orf", ".nef"))]
    if not raws:
        return {}
    out = {}
    res = run_exiftool_files_parallel(
        ["-j", "-Model", "-AFAreas", "-AFPointSelected", "-AISubjectTrackingMode",
         "-PrimaryAFPoint", "-AFPointsInFocus", "-Orientation#"], raws)
    for cp in res:
        try:
            rows = json.loads(cp.stdout or "[]")
        except ValueError:
            continue
        for r in rows:
            out[_key(r.get("SourceFile", ""))] = parse_info(r)
    return out


def lookup(found: dict, path) -> tuple:
    """(box, found) — what the score tool uses."""
    info = found.get(_key(path))
    return (info["box"], info["found"]) if info else (None, None)


def lookup_info(found: dict, path) -> dict:
    return found.get(_key(path)) or {"box": None, "found": None, "in_focus": [],
                                     "name": "", "estimated": False}
