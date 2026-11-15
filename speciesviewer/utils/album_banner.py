"""Make a trip's or album's web banner (`tripbanner.jpg`) from a photograph.

ONE JOB: cut a 2000:270 band out of a picture (where the person puts it, at the
size they choose), scale it to at most 2000 px wide, and write it as the
collection's `tripbanner.jpg`, its ini pointing at that name.

INVARIANT: the banner always has the same file name, so a new banner needs only
an upload, never pages written again; the banner it replaces (unless it is the
shipped default) is kept as a dated copy; the source picture is only read.

Pure logic, no Qt — the crop window is gui/banner_crop_dialog.py.
"""

from __future__ import annotations

import io
import logging
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger("speciesviewer")

BANNER_NAME = "tripbanner.jpg"
BANNER_SIZE = (2000, 270)
RATIO = BANNER_SIZE[0] / BANNER_SIZE[1]          # ≈ 7.4


def load_source(path) -> Image.Image:
    """The picture to cut from, upright. A RAW gives its camera JPEG (or a
    decode when it has none) — often the wider framing than the picture's JPG,
    which tends to be cropped tight on the bird."""
    from utils.raw_extensions import is_raw
    p = Path(path)
    if is_raw(p):
        from gui.preview_extract import preview_jpeg_bytes
        data = preview_jpeg_bytes(p)
        if not data:
            raise OSError(f"{p.name}: no picture could be read from this RAW")
        img = Image.open(io.BytesIO(data))
    else:
        img = Image.open(p)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def banner_box(w: int, h: int, cx: float, cy: float, size: float) -> tuple:
    """The crop box (left, top, right, bottom) in pixels of a w×h picture.

    size: the band's width as a share of the picture's width (0.2–1.0); the band
    is narrowed further when it would be taller than the picture. cx, cy: the
    band's centre as shares of width/height; the band is kept inside."""
    bw = max(1.0, min(w * max(0.05, min(1.0, size)), h * RATIO))
    bh = bw / RATIO
    left = min(max(cx * w - bw / 2, 0), w - bw)
    top = min(max(cy * h - bh / 2, 0), h - bh)
    return (int(round(left)), int(round(top)),
            int(round(left + bw)), int(round(top + bh)))


def output_size(box: tuple) -> tuple:
    """What will be written: the band scaled to 2000 wide, never enlarged."""
    bw = box[2] - box[0]
    w = min(BANNER_SIZE[0], bw)
    return w, max(1, int(round(w / RATIO)))


def render_banner(img: Image.Image, box: tuple) -> Image.Image:
    return img.crop(box).resize(output_size(box), Image.LANCZOS)


def _is_shipped_default(path: Path) -> bool:
    seed = Path(__file__).resolve().parent.parent / "dist" / "includes web" / BANNER_NAME
    try:
        return (seed.is_file() and path.stat().st_size == seed.stat().st_size
                and path.read_bytes() == seed.read_bytes())
    except OSError:
        return False


def set_banner(img: Image.Image, box: tuple, root, *, ini_name: str) -> Path:
    """Write the band as <root>/tripbanner.jpg and point <ini_name> at it.
    Returns the path written. Raises OSError when it can't be written."""
    from utils.atomic_replace import replace_with_retry
    from utils.trip_config import set_trip_flag

    root = Path(root)
    target = root / BANNER_NAME
    if target.is_file() and not _is_shipped_default(target):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        kept = target.with_name(f"{target.stem}-{stamp}{target.suffix}")
        shutil.copy2(target, kept)
        logger.info(f"Previous banner kept as {kept.name}")
    tmp = target.with_name(f".{target.stem}.new{target.suffix}")
    try:
        render_banner(img, box).save(tmp, "JPEG", quality=90)
        replace_with_retry(tmp, target)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise OSError(f"could not write {target.name}: {e}. Close anything that "
                      f"has it open and try again.") from e
    if not set_trip_flag(str(root), "trip_banner", BANNER_NAME, ini_name=ini_name):
        raise OSError(f"{BANNER_NAME} was written, but {ini_name} could not be "
                      f"changed to use it")
    logger.info(f"Banner written: {target} ({output_size(box)[0]}×{output_size(box)[1]})")
    return target
