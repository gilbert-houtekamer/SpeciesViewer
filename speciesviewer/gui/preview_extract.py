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


def extract_preview_bytes(src: Path) -> bytes | None:
    """The largest embedded JPEG preview for a RAW — PreviewImage, else the
    small ThumbnailImage — as bytes, or None if the file has neither.

    Serialised through one persistent exiftool, so it is safe to call from many
    worker threads at once (they queue on the lock rather than each spawning a
    process)."""
    src_s = str(src)
    with _lock:
        try:
            et = _get_helper()
            for tag in ("-PreviewImage", "-ThumbnailImage"):
                data = et.execute("-b", tag, src_s, raw_bytes=True)
                if data:
                    return data
            return None
        except Exception as e:
            logger.warning(f"Preview extraction failed for {src.name}: {e}")
            _drop_helper()
            return None


def extract_preview_to(src: Path, cache_path: Path) -> bool:
    """Extract ``src``'s embedded preview and write it to ``cache_path``.
    Returns True on success; nothing is written (so a later call can retry) when
    the file has no extractable preview."""
    data = extract_preview_bytes(src)
    if not data:
        return False
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
