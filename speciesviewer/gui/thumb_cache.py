"""Bounded in-RAM cache of scaled thumbnail QPixmaps.

Shared by a collection's grid + large-viewer filmstrip hosts so a colour-filter
rebuild re-attaches the pixmaps instead of re-reading and re-decoding every
preview JPG from the on-disk cache (the bulk of the ~40 s a big-day `h` took).

Keyed by (picture key, target width, image signature). The signature is
whatever the collection already tracks for "the image changed" (source +
mtimes); a re-crop / new render / Create JPG changes it, so the stale pixmap is
simply never hit again and ages out. No explicit invalidation needed.

Only meaningful once the grid card width is DETERMINISTIC (see
`ImagePreviewDialog._grid_card_width` — a fixed function of screen width, a
column count and a fill %), so the same width is asked for every rebuild and
across sessions.
"""

import glob as _glob
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtGui import QPixmap

# Evict by an estimated byte budget rather than an entry count, so it self-tunes
# to the screen size / column count. ~500 MB holds a full ~1200-picture day's
# grid + filmstrip; bigger days keep their most-recently-seen thumbnails and
# re-decode the rest (the real fix for those is viewport virtualization — step 4
# of docs/plans/culling_grid_performance.md).
_MAX_BYTES = 500 * 1024 * 1024


def _bytes(px: QPixmap) -> int:
    return max(1, px.width() * px.height() * 4)


class ThumbCache:
    def __init__(self, max_bytes: int = _MAX_BYTES):
        self._d: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        self._max = max_bytes
        self._bytes = 0

    def get(self, key, width, sig):
        k = (key, width, sig)
        px = self._d.get(k)
        if px is not None:
            self._d.move_to_end(k)
        return px

    def put(self, key, width, sig, px: QPixmap) -> None:
        if px is None or px.isNull():
            return
        k = (key, width, sig)
        old = self._d.pop(k, None)
        if old is not None:
            self._bytes -= _bytes(old)
        self._d[k] = px
        self._bytes += _bytes(px)
        while self._bytes > self._max and len(self._d) > 1:
            _, evicted = self._d.popitem(last=False)
            self._bytes -= _bytes(evicted)

    def invalidate(self, key) -> None:
        """Drop every cached width for `key` — call when the picture's image
        changes (a re-crop / new render / Create JPG). The (key, w, sig) scheme
        already handles a change the collection's image-signature catches; this
        covers a crop written to the .xmp, which the signature (mtimes of source
        + .dop) does not see."""
        for k in [k for k in self._d if k[0] == key]:
            px = self._d.pop(k)
            self._bytes -= _bytes(px)

    def clear(self) -> None:
        self._d.clear()
        self._bytes = 0


# ── persistent (on-disk) scaled thumbnails ──────────────────────────────────
# Step 2C: written as `<stem>@<width>.jpg` INSIDE the existing per-folder
# preview cache dir, so they inherit its 90-day LRU + "folder gone -> evicted"
# hygiene and show under "Preview thumbnails" in Manage Caches. Only meaningful
# because the grid card width is deterministic (same width asked every session).

def disk_thumb_path(cache_dir: Path, stem: str, width: int) -> Path:
    return Path(cache_dir) / f"{stem}@{int(width)}.jpg"


def load_disk_thumb(cache_dir, stem, width):
    p = disk_thumb_path(cache_dir, stem, width)
    if not p.exists():
        return None
    px = QPixmap(str(p))
    if px.isNull():
        return None
    try:
        from utils import preview_cache
        preview_cache.touch(p)   # share the big-preview LRU stamp
    except Exception:
        pass
    return px


def save_disk_thumb(cache_dir, stem, width, px: QPixmap) -> None:
    if px is None or px.isNull():
        return
    try:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        px.save(str(disk_thumb_path(cache_dir, stem, width)), "JPEG", 90)
    except Exception:
        pass


def drop_disk_thumbs(cache_dir, stem) -> None:
    """Delete every `<stem>@*.jpg` — call when a picture's image changes
    (re-crop / new render / Create JPG)."""
    try:
        for f in _glob.glob(str(Path(cache_dir) / (_glob.escape(str(stem)) + "@*.jpg"))):
            try:
                Path(f).unlink()
            except OSError:
                pass
    except Exception:
        pass


def cached_thumb_request(cache: ThumbCache, loader, key, target_w, sig,
                         produce, on_ready, disk_dir=None, stem=None) -> None:
    """Serve `key` at `target_w` from RAM, then disk, else run `produce` on
    `loader` and cache the scaled result in both.

    `target_w` falsy -> straight through with no caching (full-size / crop-tool
    requests, which must stay uncropped / unscaled). `disk_dir`+`stem` enable
    the persistent layer (2C); omit them for RAM-only."""
    if not target_w:
        loader.request(produce, on_ready, target_w=target_w)
        return
    hit = cache.get(key, target_w, sig)
    if hit is not None:
        on_ready(hit)
        return
    if disk_dir is not None and stem:
        dpx = load_disk_thumb(disk_dir, stem, target_w)
        if dpx is not None:
            cache.put(key, target_w, sig, dpx)
            on_ready(dpx)
            return

    def _store(px, k=key, w=target_w, s=sig, dd=disk_dir, st=stem):
        cache.put(k, w, s, px)
        if dd is not None and st:
            save_disk_thumb(dd, st, w, px)

    loader.request(produce, on_ready, target_w=target_w, on_cache=_store)
