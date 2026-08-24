# trip_thumbs.py: generate drill-down thumbnails for the trip-registry console.
#
# For each picture that's missing from the master we want to SEE it, to judge
# whether it's worth keeping. The source may be a rendered JPG or a RAW with
# only an embedded preview, so:
#   - resolve the picture's file from the trip's per-day data_files.csv
#     (WindowsFileName), preferring a JPG, else the RAW;
#   - JPG  -> downscale with Pillow;
#   - RAW  -> pull the embedded preview via the shared persistent exiftool
#     (gui.preview_extract) and downscale that.
# Thumbnails are written into <console>/thumbs/ so the console folder stays
# self-contained. Heavy deps (Pillow, exiftool) are imported lazily, and the
# whole step is opt-in (runner --thumbs), so the default scan stays fast.

import os
import io
import csv
import logging
from utils.raw_extensions import RAW

logger = logging.getLogger("speciesviewer")

_JPG_EXT = (".jpg", ".jpeg")
_RAW_EXT = tuple(RAW)  # NOTE: no reader found in this module — see plan  # canonical table — see utils/raw_extensions.py


def _safe(name):
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in name) or "upn"


def _build_upn_file_map(repo_path, wanted):
    """{UPN -> best source file path} for the wanted UPNs, read from the trip's
    per-day data_files.csv. Prefers an existing JPG, else an existing RAW."""
    out = {}
    review = os.path.join(repo_path, "metadata", "review")
    if not os.path.isdir(review):
        return out
    for day in os.listdir(review):
        fp = os.path.join(review, day, "data_files.csv")
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace", newline="") as fh:
                reader = csv.DictReader(fh, delimiter=";")
                for row in reader:
                    upn = (row.get("UniquePictureName") or "").strip()
                    if upn not in wanted:
                        continue
                    path = (row.get("WindowsFileName") or row.get("File:Directory", "")).strip()
                    if not path or not os.path.exists(path):
                        continue
                    ext = os.path.splitext(path)[1].lower()
                    cur = out.get(upn)
                    # Prefer JPG over RAW; otherwise keep the first found.
                    if cur is None or (ext in _JPG_EXT and os.path.splitext(cur)[1].lower() not in _JPG_EXT):
                        out[upn] = path
        except Exception as e:
            logger.info(f"trip_thumbs: failed reading {fp}: {e}")
    return out


def _make_thumb(src, dest, max_px=1000):
    """Write a downscaled JPEG of `src` to `dest`. RAW sources go through the
    embedded-preview extractor. Returns True on success."""
    from PIL import Image
    ext = os.path.splitext(src)[1].lower()
    try:
        if ext in _JPG_EXT or ext == ".png":
            im = Image.open(src)
        else:
            # Embed-or-decode: a website thumbnail is "show this picture", so a
            # body that embeds no JPEG (R5 II HDR-PQ CR3) must not leave a hole in
            # the trip console.
            from gui.preview_extract import preview_jpeg_bytes
            from pathlib import Path
            data = preview_jpeg_bytes(Path(src))
            if not data:
                return False
            im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        im.save(dest, "JPEG", quality=85)
        return True
    except Exception as e:
        logger.info(f"trip_thumbs: thumb failed for {src}: {e}")
        return False


def generate_thumbs(rows, console_dir, max_thumbs=300, per_trip=60, max_px=1000):
    """Generate thumbnails for missing pictures. Allocated PER TRIP (new-species
    first within each, up to per_trip) so small trips like marokko aren't starved
    by huge in-progress ones; total is then bounded by max_thumbs. Returns
    {UPN -> relative thumb path} for embedding in the drill-down HTML."""
    by_repo = {}
    selected = 0
    for row in rows:
        # _missing is already sorted new-species first by build_registry.
        for m in row.get("_missing", [])[:per_trip]:
            if selected >= max_thumbs:
                break
            by_repo.setdefault(row["repo_path"], set()).add(m["upn"])
            selected += 1
        if selected >= max_thumbs:
            break

    thumbs_dir = os.path.join(console_dir, "thumbs")
    thumb_map = {}
    made = 0
    for repo, upns in by_repo.items():
        file_map = _build_upn_file_map(repo, upns)
        for upn in upns:
            src = file_map.get(upn)
            if not src:
                continue
            rel = "thumbs/" + _safe(upn) + ".jpg"
            if _make_thumb(src, os.path.join(console_dir, rel), max_px):
                thumb_map[upn] = rel
                made += 1
    logger.info(f"trip_thumbs: generated {made} thumbnails (cap {max_thumbs})")
    return thumb_map
