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
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")

ALBUM_PICTURE_NAME = "tripimage.jpg"
ALBUM_PICTURE_SIZE = (600, 450)          # 4:3, matches virttrip.ini trip_picture
ALBUM_BANNER_NAME = "tripbanner.jpg"


def cover_name_for(root) -> str:
    """The cover FILENAME this collection uses, read from its own ini.

    Albums ship "tripimage.jpg", but a trip names its own — kretadb has
    `trip_picture = kretaimage.jpg`. Assuming the album name would have written a
    second, ignored file into the trip folder.
    """
    import configparser
    root = Path(root)
    for ini_name in ("virttrip.ini", "trip.ini"):
        ini = root / ini_name
        if not ini.is_file():
            continue
        try:
            cp = configparser.ConfigParser()
            read_ini(cp, ini)
            name = (cp.get("trip_config", "trip_picture", fallback="") or "").strip()
            if name:
                return name
        except Exception as e:                      # a broken ini must not block
            logger.info(f"cover_name_for: could not read {ini}: {e}")
    return ALBUM_PICTURE_NAME


def target_path(album_root, name=None) -> Path:
    return Path(album_root) / (name or cover_name_for(album_root))


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


def is_seeded_default(album_root, dist_dir=None, name=None) -> bool:
    """True when the album still carries the shipped placeholder cover.

    Used to decide whether replacing it needs a warning: overwriting the generic
    tern/egret placeholder is nothing, overwriting a picture the user chose (or
    made in Photoshop) deserves a question.
    """
    target = target_path(album_root, name)
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


def set_album_picture(source, album_root, *, backup: bool = True, name=None) -> Path:
    """Write `source` as the collection's cover. Returns the path written.

    Raises ValueError if the source IS the target — the rule that came out of the
    incident where 142 master JPGs were destroyed by their own thumbnail: a
    derived file must never be written over its source.
    """
    import os
    from datetime import datetime

    src = Path(source)
    target = target_path(album_root, name)
    if src.resolve() == target.resolve():
        raise ValueError("refusing to write the cover picture over its own source")
    target.parent.mkdir(parents=True, exist_ok=True)

    if backup and target.is_file() and not is_seeded_default(album_root, name=name):
        # Dated, and still a REAL .jpg — "tripimage.jpg.previous" could not be
        # previewed in Explorer or picked in the file dialog, so restoring it
        # meant renaming by hand. "tripimage-20260720-2102.jpg" can simply be
        # chosen with Browse.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        kept = target.with_name(f"{target.stem}-{stamp}{target.suffix}")
        try:
            shutil.copy2(target, kept)
            logger.info(f"Previous cover kept as {kept.name}")
        except OSError as e:
            logger.warning(f"cover picture: could not back up {target.name}: {e}")

    # Render first, then swap into place. Encoding straight onto the target holds
    # it open for the whole write, which fails outright if anything else has the
    # file (the app itself displays a trip's picture) — and a failure halfway
    # through would leave a truncated cover. The temp file sits in the same
    # folder so the replace stays on one filesystem.
    tmp = target.with_name(f".{target.stem}.new{target.suffix}")
    try:
        render_cover(src).save(tmp, "JPEG", quality=90)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise OSError(f"could not render the cover from {src.name}: {e}") from e
    try:
        os.replace(tmp, target)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise OSError(
            f"could not replace {target.name}: {e}. "
            f"Close anything that has it open (PhotoLab, an image viewer) and retry."
        ) from e
    logger.info(f"Cover picture set from {src.name} -> {target}")
    return target
