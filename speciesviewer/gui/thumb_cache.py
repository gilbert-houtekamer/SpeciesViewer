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

from collections import OrderedDict

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


def cached_thumb_request(cache: ThumbCache, loader, key, target_w, sig,
                         produce, on_ready) -> None:
    """Serve `key` at `target_w` from `cache` if present (synchronous), else run
    `produce` on `loader` and cache the scaled result.

    `target_w` falsy -> straight through with no caching (full-size / crop-tool
    requests, which must stay uncropped / unscaled)."""
    if not target_w:
        loader.request(produce, on_ready, target_w=target_w)
        return
    hit = cache.get(key, target_w, sig)
    if hit is not None:
        on_ready(hit)
        return
    loader.request(
        produce, on_ready, target_w=target_w,
        on_cache=lambda px, k=key, w=target_w, s=sig: cache.put(k, w, s, px))
