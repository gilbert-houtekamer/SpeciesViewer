"""Full-resolution (1:1) decode + cache for the culling 100% view.

The embedded RAW preview is only ~3200x2400, so true pixel-level sharpness — the
whole point of the 100% view — needs the full sensor (e.g. 5220x3912). That means
decoding the RAW with rawpy (libraw), ~1s/file. Results are cached as full-res
JPEGs in the per-user app cache (NEVER in the data tree, same rule as the preview
cache), so the cost is paid once and later viewing is instant.

Callers skip Blue/Purple-labelled pictures (already-rejected) before asking for a
decode, and run batches on a worker thread (a group of 400 ≈ a coffee break).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("speciesviewer")


def fullres_cache_path(raw_path, cache_root) -> Path:
    """Where the cached full-res JPEG for `raw_path` lives."""
    return Path(cache_root) / (Path(raw_path).stem + ".fullres.jpg")


def ensure_fullres_jpeg(raw_path, cache_root, quality=90):
    """Return a cached full-res JPEG Path for `raw_path`, decoding the RAW once
    via rawpy if not already cached. Returns None on failure (missing file,
    decode error, or rawpy unavailable)."""
    raw_path = Path(raw_path)
    out = fullres_cache_path(raw_path, cache_root)
    if out.exists():
        return out
    if not raw_path.exists():
        return None
    try:
        import rawpy
        from PIL import Image
        out.parent.mkdir(parents=True, exist_ok=True)
        with rawpy.imread(str(raw_path)) as raw:
            rgb = raw.postprocess()  # default AHD demosaic, sRGB
        Image.fromarray(rgb).save(str(out), "JPEG", quality=quality)
        return out
    except Exception as e:
        logger.warning(f"Full-res decode failed for {raw_path.name}: {e}")
        return None
