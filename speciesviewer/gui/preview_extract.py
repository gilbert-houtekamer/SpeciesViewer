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
import re
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
#
# This is now only a SAFETY NET. embed_lengths() asks exiftool what a file actually
# carries instead of consulting this list, because a typed-out list of tag names drifts
# away from what real cameras write in exactly the way a typed-out list of file
# extensions did (see utils/raw_extensions.py). The G9 II is the proof: it puts a
# 1920x1440 preview in JpgFromRaw and its real 5776x4336 one in JpgFromRaw2, a tag
# that was never in this tuple, so we cached the small one and never knew.
_EMBED_TAGS = ("JpgFromRaw", "PreviewImage", "OtherImage", "ThumbnailImage")

# exiftool prints every embedded image as "Tag : (Binary data N bytes, use -b ...)".
# That line is the ONE place a byte count is available for every format.
_BINARY_LINE = re.compile(r"^(\w+)\s*:\s*\(Binary data (\d+) bytes", re.M)
_ORIENT_LINE = re.compile(r"^Orientation\s*:\s*(\d+)\s*$", re.M)

# A camera turned on its side does NOT rotate the pixels it embeds. It writes the
# preview the way the sensor read it — landscape — and records the turn in the file's
# Orientation tag. Nikon, Canon and (presumably) the rest all behave this way: a
# portrait D500 frame carries Orientation 6 with a landscape 5568x3712 JpgFromRaw,
# and a portrait R5 II frame carries Orientation 8 with a landscape 8192x5464 one.
# Nothing in SpeciesViewer read that tag, so every vertical photograph rendered 90
# degrees sideways. It stayed invisible only because almost nothing in the library is
# shot vertically.
#
# PIL's own table, the same one ImageOps.exif_transpose uses. Orientation 1 (and a
# missing tag) means upright, so it is deliberately absent.
def _transpose_for(orientation: int):
    from PIL import Image
    return {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }.get(orientation)


# Orientations that turn the frame a quarter turn, i.e. the ones that swap width and
# height. Used to spot an embed on which the rotation has ALREADY been applied.
_QUARTER_TURNS = (5, 6, 7, 8)


def _upright(data: bytes, orientation: int) -> bytes | None:
    """Rotate an embedded JPEG to how the photograph should be seen. Returns None when
    there is nothing to do, so the caller keeps the original bytes untouched and the
    common case — every landscape photograph — costs no decode and no re-encode.

    Refuses to rotate an embed that is ALREADY portrait while the file asks for a
    quarter turn. That combination means something has rotated the pixels before us (a
    RAW converter re-writing the file, say), and applying the tag again would leave the
    picture on its other side. Never double-rotate is the whole safety rule here."""
    op = _transpose_for(orientation)
    if op is None:
        return None
    from io import BytesIO
    from PIL import Image
    try:
        with Image.open(BytesIO(data)) as im:
            if orientation in _QUARTER_TURNS and im.height > im.width:
                return None           # already upright; the tag is stale, not a job
            out = BytesIO()
            # 95, not the preview quality: this re-encode is not the place to lose
            # detail, and whatever caches the result normalises it afterwards anyway.
            im.transpose(op).convert("RGB").save(out, "JPEG", quality=95,
                                                 optimize=True)
            return out.getvalue()
    except Exception as e:
        logger.warning(f"Could not straighten a rotated preview: {e}")
        return None

# Not every embed is a JPEG. The Canon EOS R5 Mark II, shooting the HDR-PQ style CR3
# (CompressorVersion CanonCR3_003), embeds its preview and thumbnail as an HEVC
# bitstream instead — the bytes start with a `CISZ`/`hvcC` box, not FF D8 FF. We used
# to hand those straight to Qt/PIL, which decode nothing, so the card came up blank
# with no log line saying why; worse, extract_preview_to() then wrote the HEVC into
# the preview cache under a .jpg name and reported success, so the blank card STUCK.
# Checking three bytes costs nothing and keeps every consumer's assumption true: what
# this module returns is always a JPEG.
_JPEG_MAGIC = b"\xff\xd8\xff"


def _is_jpeg(data: bytes) -> bool:
    return bool(data) and data[:3] == _JPEG_MAGIC


# A body that embeds no JPEG embeds none for the whole shoot, so saying it per
# picture would put 400 identical lines in the log for one folder. Say it ONCE per
# camera+format; the per-file detail is still there under debug logging.
_announced_no_embed = set()


def format_camera_label(make: str, model: str) -> str:
    """"Canon EOS R5 Mark II" from a Make/Model pair — ONE definition of how a body
    is named, shared with the per-body camera checklist so a body cannot appear under
    two different names in the log and in the support table."""
    make, model = (make or "").strip(), (model or "").strip()
    # Canon writes Model "Canon EOS R5 Mark II" (make already in it); Sony writes
    # make "SONY" and model "ILCE-1M2" (it is not), so only prefix when it adds
    # something rather than stuttering.
    if make and model and not model.upper().startswith(make.split()[0].upper()):
        return f"{make} {model}"
    return model or make


def _camera_label(src: Path) -> str:
    """The body that took this file, for a log line a person has to read. Empty when
    exiftool cannot say. Only called on the (rare, already slow) decode path, so the
    extra metadata read costs nothing that matters."""
    with _lock:
        try:
            et = _get_helper()
            res = et.get_tags([str(src)], tags=["Make", "Model"])[0]
        except Exception:
            _drop_helper()
            return ""
    got = {}
    for k, v in res.items():
        name = k.split(":")[-1]
        if name in ("Make", "Model"):
            got[name] = str(v).strip()
    return format_camera_label(got.get("Make", ""), got.get("Model", ""))


def embed_lengths(src: Path) -> dict:
    """{tag: byte length} for the embedded images this RAW actually carries. ONE
    metadata call, no binary — cheap enough to ask before extracting.

    Asks ``-preview:all`` and reads the byte count out of exiftool's own
    "(Binary data N bytes)" line, rather than asking for ``<tag>Length`` on a list of
    tags we typed out ourselves. Two reasons, both measured on the corpus:

    * **CR3 and RW2 expose no ``*Length`` tags at all.** The old probe returned an
      empty dict for both, so "largest wins" silently degraded to first-tag-wins —
      the exact behaviour 3d0e490 was written to remove. It happened to pick right
      on Canon and wrong on Panasonic.
    * **It finds tags we never listed.** The G9 II's real 5776x4336 preview lives in
      ``JpgFromRaw2``; the Z 9 also reports ``PreviewTIFF`` and ``ThumbnailTIFF``.
      Discovering them costs nothing now that every candidate is magic-checked — the
      TIFFs are simply skipped like the R5 II's HEVC.

    ``-a`` keeps duplicates across groups; if a name appears twice we keep the larger,
    since that is the one worth extracting."""
    return _embed_info(src)[0]


def _embed_info(src: Path) -> tuple[dict, int]:
    """({tag: byte length}, orientation) in ONE metadata call.

    Orientation rides along with the embed listing because it costs nothing there and
    the caller needs both to hand back an upright picture. ``-n`` makes it the number
    (1..8) rather than "Rotate 270 CW"; 1 means upright, and a file that does not
    report it at all is treated as upright too."""
    src_s = str(src)
    with _lock:
        try:
            et = _get_helper()
            out = et.execute("-a", "-s", "-n", "-preview:all", "-Orientation", src_s)
        except Exception as e:
            logger.warning(f"Could not read embed lengths for {src.name}: {e}")
            _drop_helper()
            return {}, 1
    lengths = {}
    for tag, n in _BINARY_LINE.findall(out or ""):
        n = int(n)
        if n > lengths.get(tag, 0):
            lengths[tag] = n
    m = _ORIENT_LINE.search(out or "")
    return ({t: n for t, n in lengths.items() if n > 0},
            int(m.group(1)) if m else 1)


def extract_preview_bytes(src: Path) -> bytes | None:
    """The LARGEST embedded JPEG in a RAW, as bytes, or None if it has none.

    Largest by actual byte length, not by tag order. The order used to decide it,
    which quietly picked the wrong image whenever a body's tags did not happen to be
    listed biggest-first: a NEF with a 70 KB PreviewImage and a 434 KB OtherImage
    handed back the 70 KB one, and we cached a soft little preview while a far better
    image sat in the same file. Asking exiftool for the lengths first is one extra
    metadata call (no binary, through the same persistent process) and removes the
    guesswork entirely — see embed_lengths() for why it asks the file rather than a
    list of tag names, which is what let the same bug come back on CR3 and RW2.

    What comes back is ALWAYS a JPEG, and always the right way up: embeds that are not
    a JPEG are skipped and named in the log, so "no embedded JPEG" is a stated fact
    rather than a silent blank, and a frame shot with the camera on its side is
    rotated to how it should be seen (cameras embed the sensor's landscape view and
    record the turn in a tag — see _upright). A landscape photograph, which is nearly
    all of them, is returned byte-for-byte as the camera wrote it.
    Callers that want a picture regardless should use ``preview_jpeg_bytes``, which
    falls back to decoding the RAW.

    Serialised through one persistent exiftool, so it is safe to call from many
    worker threads at once (they queue on the lock rather than each spawning a
    process)."""
    src_s = str(src)
    lengths, orientation = _embed_info(src)
    # Every discovered embed, biggest first — then any classic tag exiftool did not
    # report, so a body whose embeds -preview:all somehow misses can still not do
    # worse than the old list did. Those extra reads only happen if everything
    # discovered has already failed, which on the whole corpus is never.
    order = sorted(lengths, key=lengths.get, reverse=True) + \
            [t for t in _EMBED_TAGS if t not in lengths]
    rejected = []
    found = None
    with _lock:
        try:
            et = _get_helper()
            for tag in order:
                data = et.execute("-b", f"-{tag}", src_s, raw_bytes=True)
                if not data:
                    continue
                if not _is_jpeg(data):
                    # Not a JPEG (the R5 II's HEVC embeds) — keep looking. A body
                    # can carry both: 7883.CR3 has JPEGs in every slot, 7881.CR3
                    # only HEVC in ThumbnailImage.
                    rejected.append(f"{tag} ({len(data)} bytes, "
                                    f"starts {data[:4].hex()})")
                    continue
                found = data
                break
        except Exception as e:
            logger.warning(f"Preview extraction failed for {src.name}: {e}")
            _drop_helper()
            return None
    if found is not None:
        # Straighten OUTSIDE the lock — a rotate is a decode and a re-encode, and
        # must not hold the one exiftool the rest of the grid is queueing on.
        straightened = _upright(found, orientation)
        if straightened is not None:
            logger.debug(f"{src.name}: rotated the preview upright "
                         f"(Orientation {orientation})")
        return straightened if straightened is not None else found
    if rejected:
        # Which tag held what is diagnostic detail — it means nothing to a person
        # reading the log, and the caller says the useful sentence. Debug only.
        logger.debug(f"{src.name}: skipped non-JPEG embed(s) {', '.join(rejected)}")
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


def decode_preview_jpeg(src: Path, max_edge: int, quality: int) -> bytes | None:
    """Render a JPEG by DECODING the RAW, for the files that carry no embedded JPEG
    at all. Returns None if LibRaw cannot read the file. ``max_edge = 0`` means full
    size — what Create JPG wants, since that file can end up on the website.

    This is the slow lane and deliberately so — seconds, not the ~30 ms of an embed
    read — but it only runs for a RAW with nothing to extract, and the caller caches
    the result, so it is paid once per file. The alternative is a blank card.

    ``half_size`` skips demosaicing (one RGB pixel per Bayer quad), which is several
    times faster and still lands well above ``max_edge`` on any modern body: the
    R5 II's 8192px sensor halves to 4096 against a 3200px target. It is only used
    when it actually stays above the target, so a small or heavily cropped sensor
    still gets a full decode rather than a preview softer than the OM-1 reference.

    Deliberately NOT routed through gui/fullres.py: that decodes at the user's
    Image-Processing settings (80% of the full sensor, optional denoise) into the
    full-res cache. This wants one small predictable JPEG in the preview cache.

    Runs OUTSIDE ``_lock`` — a multi-second decode must not hold the one exiftool
    that every other thumbnail in the grid is queueing on."""
    try:
        import rawpy
        from io import BytesIO
        from PIL import Image
        with rawpy.imread(str(src)) as raw:
            # sizes.width/height, not raw_width/raw_height: the visible image is
            # what postprocess returns, and the sensor's masked margins are not
            # pixels we would ever show.
            half = max_edge > 0 and max(raw.sizes.width,
                                        raw.sizes.height) >= max_edge * 2
            rgb = raw.postprocess(use_camera_wb=True, half_size=half,
                                  output_color=rawpy.ColorSpace.sRGB)
        im = Image.fromarray(rgb)
        if max_edge > 0:
            im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        out = BytesIO()
        im.save(out, "JPEG", quality=quality, optimize=True)
        logger.debug(f"{src.name}: decoded the RAW for a preview "
                     f"({im.size[0]}x{im.size[1]}, half_size={half})")
        key = (_camera_label(src), src.suffix.upper().lstrip("."))
        if key not in _announced_no_embed:
            _announced_no_embed.add(key)
            body = f"{key[0]} pictures" if key[0] else "Pictures"
            logger.info(f"{body} in {key[1]} format have no embedded JPEG — "
                        f"reading the photo from the RAW itself instead, "
                        f"at {im.size[0]}x{im.size[1]}.")
        return out.getvalue()
    except Exception as e:
        # Nikon High Efficiency NEF lands here (LibRaw cannot decode TicoRAW), but
        # those files all carry a full-size embedded JPEG, so they never get this far.
        logger.warning(f"Could not decode {src.name} for a preview: {e}")
        return None


def preview_jpeg_bytes(src: Path) -> bytes | None:
    """A JPEG for this RAW whatever it takes: the embedded one if there is one,
    otherwise a decode. None only when the file yields neither.

    Use this where the job is "show me this picture". Use ``extract_preview_bytes``
    where the job is specifically "the CAMERA's own JPEG" — the large viewer's
    Camera JPEG tab, which should keep saying the RAW has none rather than quietly
    substituting our render under a label that claims otherwise."""
    data = extract_preview_bytes(src)
    if data:
        return data
    max_edge, quality = preview_limits()
    # max_edge 0 means "cache the embed verbatim", which cannot apply when there is
    # no embed — fall back to the reference size rather than decoding full sensor.
    return decode_preview_jpeg(src, max_edge or PREVIEW_MAX_LONG_EDGE, quality)


def extract_preview_to(src: Path, cache_path: Path) -> bool:
    """Extract ``src``'s embedded preview, normalise it to one size, and write it to
    ``cache_path``. Returns True on success; nothing is written (so a later call can
    retry) when the file yields no preview at all.

    A RAW with no embedded JPEG falls back to a decode rather than to a blank card.
    That is the slow lane, but it lands in the cache like any other preview, so a
    body that needs it is slow once per picture rather than broken forever.

    The resize lives HERE and not in extract_preview_bytes, for two reasons:
    engine/trip_thumbs.py calls the bytes function and does its own downscale to
    1000px (it would resize twice), and gui/rating_provider.py writes into the same
    preview cache through THIS function — so putting it here covers both writers and
    nothing can slip verbatim bytes into the cache behind our back."""
    max_edge, quality = preview_limits()
    data = extract_preview_bytes(src)
    if data:
        try:
            smaller = normalise_jpeg_bytes(data, max_edge, quality)
            if smaller is not None:
                data = smaller
        except Exception as e:
            # A preview of the wrong SIZE beats a blank card: fall back to the
            # original bytes rather than failing the extraction outright. Safe only
            # because extract_preview_bytes guarantees a JPEG — this branch used to
            # be the route by which the R5 II's HEVC got written into the cache
            # under a .jpg name, turning one blank card into a permanent one.
            logger.warning(f"Preview downscale failed for {src.name} "
                           f"(caching it full size): {e}")
    else:
        # Nothing embedded to extract (the R5 II's HDR-PQ CR3). Decode the RAW so
        # the card shows the picture; cached, so the cost is paid once.
        data = decode_preview_jpeg(src, max_edge or PREVIEW_MAX_LONG_EDGE, quality)
        if not data:
            return False
    try:
        cache_path.write_bytes(data)
        return True
    except Exception as e:
        logger.warning(f"Could not write preview cache {cache_path.name}: {e}")
        return False


def extract_video_frame_bytes(src: Path, quality: int = 90) -> bytes | None:
    """A representative frame of a video, as JPEG bytes, or None if it cannot be
    read. Uses cv2 (already a project dependency — see gui/fullres.py) rather
    than ffmpeg or a new library: VideoCapture opens the container, reads the
    first decodable frame, and imencode does the JPEG — no temp file, no
    subprocess.

    Deliberately outside `_lock`: this is a different decoder entirely (not the
    shared exiftool), so it must not queue behind RAW preview extraction."""
    import cv2
    cap = cv2.VideoCapture(str(src))
    try:
        if not cap.isOpened():
            logger.warning(f"Could not open {src.name} as a video.")
            return None
        ok, frame = cap.read()
        if not ok or frame is None:
            logger.warning(f"{src.name}: no readable video frame.")
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return None
        data = buf.tobytes()
        # Obey the same cache rule as every other preview. A 4K movie hands back a
        # 3840x2160, ~1.5 MB frame, and this was the one path that wrote such a
        # thing to the preview cache verbatim while every RAW embed was capped at
        # max_long_edge. normalise_jpeg_bytes returns None when nothing needs
        # doing, so a small frame costs no re-encode.
        max_edge, q = preview_limits()
        return normalise_jpeg_bytes(data, max_edge, q) or data
    except Exception as e:
        logger.warning(f"Video frame extraction failed for {src.name}: {e}")
        return None
    finally:
        cap.release()


@atexit.register
def _shutdown():
    with _lock:
        _drop_helper()
