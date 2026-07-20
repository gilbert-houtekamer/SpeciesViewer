"""Make an album's cover picture (`tripimage.jpg`) from a chosen photograph.

The album needs a 600x450 image for the trip listings. Picking it by hand beats
picking it automatically: the best-RATED photograph is often a tight portrait
that survives a 4:3 crop badly, and a person sees that at a glance where a
heuristic cannot.

Pure logic, no Qt dialogs — so the crop can be previewed by the caller and
tested on its own.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PIL import Image

logger = logging.getLogger("speciesviewer")

ALBUM_PICTURE_NAME = "tripimage.jpg"
ALBUM_PICTURE_SIZE = (600, 450)          # 4:3, matches virttrip.ini trip_picture
ALBUM_BANNER_NAME = "tripbanner.jpg"


def target_path(album_root) -> Path:
    return Path(album_root) / ALBUM_PICTURE_NAME


def render_cover(source, size=ALBUM_PICTURE_SIZE) -> Image.Image:
    """Centre-crop `source` to the target aspect, then resize to exactly `size`.

    Centre-crop, not squash: a stretched bird is instantly wrong, whereas a
    slightly tight crop is merely a crop. A 3:2 original loses about 12% off each
    side, which is why the caller shows the result before writing it.
    """
    img = Image.open(source)
    img = img.convert("RGB")
    tw, th = size
    want = tw / th
    w, h = img.size
    have = w / h
    if have > want:                       # too wide → trim left/right
        new_w = int(round(h * want))
        left = (w - new_w) // 2
        box = (left, 0, left + new_w, h)
    else:                                 # too tall → trim top/bottom
        new_h = int(round(w / want))
        top = (h - new_h) // 2
        box = (0, top, w, top + new_h)
    return img.crop(box).resize(size, Image.LANCZOS)


def is_seeded_default(album_root, dist_dir=None) -> bool:
    """True when the album still carries the shipped placeholder cover.

    Used to decide whether replacing it needs a warning: overwriting the generic
    tern/egret placeholder is nothing, overwriting a picture the user chose (or
    made in Photoshop) deserves a question.
    """
    target = target_path(album_root)
    if not target.is_file():
        return True                        # nothing to lose
    if dist_dir is None:
        dist_dir = Path(__file__).resolve().parent.parent / "dist" / "includes web"
    seed = Path(dist_dir) / ALBUM_PICTURE_NAME
    if not seed.is_file():
        return False
    try:
        return (target.stat().st_size == seed.stat().st_size
                and target.read_bytes() == seed.read_bytes())
    except OSError:
        return False


def set_album_picture(source, album_root, *, backup: bool = True) -> Path:
    """Write `source` as the album's cover. Returns the path written.

    Raises ValueError if the source IS the target — the rule that came out of the
    incident where 142 master JPGs were destroyed by their own thumbnail: a
    derived file must never be written over its source.
    """
    src = Path(source)
    target = target_path(album_root)
    if src.resolve() == target.resolve():
        raise ValueError("refusing to write the album picture over its own source")
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup and target.is_file() and not is_seeded_default(album_root):
        # Keep exactly one previous cover — enough to undo a wrong pick without
        # accumulating clutter in a folder the user browses.
        try:
            shutil.copy2(target, target.with_suffix(".jpg.previous"))
        except OSError as e:
            logger.warning(f"album picture: could not back up {target.name}: {e}")
    render_cover(src).save(target, "JPEG", quality=90)
    logger.info(f"Album picture set from {src.name} → {target}")
    return target
