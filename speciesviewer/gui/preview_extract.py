"""Shared RAW/embedded-preview extractor backed by ONE persistent exiftool.

Why this exists
---------------
The grid thumbnails used to spawn a fresh ``exiftool -b -PreviewImage`` process
per RAW — dozens at once when a species grid opened. Under that spawn-storm,
cold reads of ~19 MB ORF files intermittently returned an EMPTY preview
(rc=0, no stdout), so the caller silently fell back to the 160x120
ThumbnailImage — or, when that was empty too, to a blank "(no image)" card —
and cached the bad result permanently. Reading the same file singly always
returned the full ~1 MB preview, which is exactly why the large viewer showed
an image the grid had given up on.

The fix is to route every preview extraction through a single, long-lived
``-stay_open`` exiftool (pyexiftool ExifToolHelper) guarded by a lock. That
removes the process-spawn storm entirely *and* serialises extraction, so the
empty-output race can't happen. Binary is read with ``raw_bytes=True``;
pyexiftool frames each batch with a randomised end sentinel, so JPEG bytes
can't truncate the stream.

The same persistent helper that the master EXIF scan already uses
(engine/exifcache.py) — here applied to the GUI's thumbnail path.
"""

from __future__ import annotations

import atexit
import logging
import threading
from pathlib import Path

logger = logging.getLogger("speciesviewer")

# One persistent exiftool, serialised: extraction is off the GUI thread (on
# QThreadPool workers) so the lock only ever blocks worker threads, never the
# UI. Serial-but-reliable beats parallel-but-flaky here — and -stay_open has no
# per-call process-spawn cost, so it is not much slower in practice.
_lock = threading.Lock()
_helper = None  # exiftool.ExifToolHelper | None


def _get_helper():
    """Return a running ExifToolHelper, (re)starting it if the process died.
    Caller must hold ``_lock``."""
    global _helper
    if _helper is not None and _helper.running:
        return _helper
    import exiftool
    _helper = exiftool.ExifToolHelper(
        common_args=["-charset", "filename=utf8"],
        encoding="utf-8", check_execute=False)
    _helper.run()
    return _helper


def _drop_helper():
    """Tear down a possibly-wedged helper so the next call restarts clean.
    Caller must hold ``_lock``."""
    global _helper
    try:
        if _helper is not None and _helper.running:
            _helper.terminate()
    except Exception:
        pass
    _helper = None


# Where the different bodies hide their embedded JPEG. JpgFromRaw is the camera's
# own FULL-SIZE render on the Nikons; PreviewImage is the OM-1's 3200px preview (and
# a tiny ~600px one on Nikons); OtherImage is where the old D90 puts its embed (it has
# no PreviewImage or ThumbnailImage at all, which is why those files used to render
# black); ThumbnailImage is the 160x120 last resort.
_EMBED_TAGS = ("JpgFromRaw", "PreviewImage", "OtherImage", "ThumbnailImage")


def embed_lengths(src: Path) -> dict:
    """{tag: byte length} for the embedded images this RAW actually carries. ONE
    metadata call, no binary — cheap enough to ask before extracting."""
    src_s = str(src)
    with _lock:
        try:
            et = _get_helper()
            res = et.get_tags([src_s], tags=[t + "Length" for t in _EMBED_TAGS])[0]
        except Exception as e:
            logger.warning(f"Could not read embed lengths for {src.name}: {e}")
            _drop_helper()
            return {}
    out = {}
    for k, v in res.items():
        for t in _EMBED_TAGS:
            if k.endswith(t + "Length"):
                try:
                    out[t] = int(v)
                except (TypeError, ValueError):
                    pass
    return {t: n for t, n in out.items() if n > 0}


def extract_preview_bytes(src: Path) -> bytes | None:
    """The LARGEST embedded JPEG in a RAW, as bytes, or None if it has none.

    Largest by actual byte length, not by tag order. The order used to decide it,
    which quietly picked the wrong image whenever a body's tags did not happen to be
    listed biggest-first: a NEF with a 70 KB PreviewImage and a 434 KB OtherImage
    handed back the 70 KB one, and we cached a soft little preview while a far better
    image sat in the same file. Asking exiftool for the lengths first is one extra
    metadata call (no binary, through the same persistent process) and removes the
    guesswork entirely.

    Serialised through one persistent exiftool, so it is safe to call from many
    worker threads at once (they queue on the lock rather than each spawning a
    process)."""
    src_s = str(src)
    lengths = embed_lengths(src)
    order = ([max(lengths, key=lengths.get)] if lengths else []) + \
            [t for t in _EMBED_TAGS if t not in lengths]
    with _lock:
        try:
            et = _get_helper()
            for tag in order:
                data = et.execute("-b", f"-{tag}", src_s, raw_bytes=True)
                if data:
                    return data
            return None
        except Exception as e:
            logger.warning(f"Preview extraction failed for {src.name}: {e}")
            _drop_helper()
            return None


# One reference size for every camera body. The embedded JPEG we cache varies
# wildly — the OM-1 embeds 3200x2400 (~0.9 MB), the Nikons a FULL-SIZE 5568-6000px
# (1.5-2 MB), and the D90 a 4288x2848 monster of ~9.9 MB. The largest-first fix that
# made the D90 render at all (3d0e490) is exactly what made us start caching that
# 9.9 MB. Normalising to the OM-1's long edge gives every body one predictable
# preview instead of a per-body lottery; the OM-1 is untouched because it already IS
# the reference. Cache size is a side benefit — the point is predictability.
#
# NOTE this only normalises the CACHED PREVIEW. The full-size JPEG stays in the RAW
# and can be extracted whenever we want it (see the backlog item about using the
# Nikons' embedded full-size render for the 100% view instead of a rawpy decode).
PREVIEW_MAX_LONG_EDGE = 3200
PREVIEW_QUALITY = 90


def preview_limits() -> tuple[int, int]:
    """(max_long_edge, jpeg_quality) from [preview] in the machine config.
    max_long_edge = 0 turns normalisation off (cache the embed verbatim)."""
    try:
        from utils.config import _config
        return (_config.getint("preview", "max_long_edge",
                               fallback=PREVIEW_MAX_LONG_EDGE),
                _config.getint("preview", "quality", fallback=PREVIEW_QUALITY))
    except Exception:
        return PREVIEW_MAX_LONG_EDGE, PREVIEW_QUALITY


# An embedded preview above this is near-lossless and pointless AS A PREVIEW. Real
# ones sit around 1 bit/pixel (OM-1: 3200x2400 in 1.1 MB = 1.15 bpp; D7500: 0.89 bpp),
# but the D90 writes some of its embeds at 6.5-9.3 bpp — 2.3 MB for a 1929x1286 image,
# which is FATTER than our normalised 3200px previews while being a sixth of the
# pixels. Those are within the pixel cap, so a size-only rule would cache them
# verbatim and miss the worst offenders in the whole library. 4.0 leaves a wide margin
# over anything a camera writes normally, so we only ever re-encode the pathological.
_MAX_BITS_PER_PIXEL = 4.0


def normalise_jpeg_bytes(data: bytes, max_edge: int, quality: int) -> bytes | None:
    """Normalise an embedded JPEG: at most `max_edge` on the long side, and not
    absurdly heavy for its pixel count. Returns None when the preview is already fine
    as it is — the caller then writes the original bytes untouched, so the common case
    (the OM-1, most of the library) costs no decode, no re-encode, no generation loss.

    Uses Image.draft() first: on a JPEG that is a DCT-scaled decode (1/2, 1/4, 1/8),
    several times faster and far lighter on memory than decoding the D90's 4288x2848
    in full only to throw most of it away. draft() only lands on powers of two, so
    thumbnail() still does the final exact step."""
    from io import BytesIO
    from PIL import Image

    if max_edge <= 0:
        return None                       # normalisation switched off
    with Image.open(BytesIO(data)) as im:
        w, h = im.size
        too_big = max(w, h) > max_edge
        too_fat = (len(data) * 8) / max(w * h, 1) > _MAX_BITS_PER_PIXEL
        if not too_big and not too_fat:
            return None                   # already a sane preview — keep the original
        if too_big:
            im.draft("RGB", (max_edge, max_edge))
        im = im.convert("RGB")
        if too_big:
            im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        out = BytesIO()
        im.save(out, "JPEG", quality=quality, optimize=True)
        return out.getvalue()


def extract_preview_to(src: Path, cache_path: Path) -> bool:
    """Extract ``src``'s embedded preview, normalise it to one size, and write it to
    ``cache_path``. Returns True on success; nothing is written (so a later call can
    retry) when the file has no extractable preview.

    The resize lives HERE and not in extract_preview_bytes, for two reasons:
    engine/trip_thumbs.py calls the bytes function and does its own downscale to
    1000px (it would resize twice), and gui/rating_provider.py writes into the same
    preview cache through THIS function — so putting it here covers both writers and
    nothing can slip verbatim bytes into the cache behind our back."""
    data = extract_preview_bytes(src)
    if not data:
        return False
    max_edge, quality = preview_limits()
    try:
        smaller = normalise_jpeg_bytes(data, max_edge, quality)
        if smaller is not None:
            data = smaller
    except Exception as e:
        # A preview of the wrong SIZE beats a blank card: fall back to the original
        # bytes rather than failing the extraction outright.
        logger.warning(f"Preview downscale failed for {src.name} "
                       f"(caching it full size): {e}")
    try:
        cache_path.write_bytes(data)
        return True
    except Exception as e:
        logger.warning(f"Could not write preview cache {cache_path.name}: {e}")
        return False


@atexit.register
def _shutdown():
    with _lock:
        _drop_helper()
