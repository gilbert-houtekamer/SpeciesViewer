"""Async, cancellable image loader shared by the picture providers.

A trip can be hundreds of pictures and the user switches view constantly, so
image work runs off the GUI thread and is cancellable. Cancellation is by
generation counter (the proven pattern already used in the Rating Editor):
cancel_all() bumps the generation; results produced under an older generation
are dropped instead of painted. We don't try to kill a running task mid-decode
(you can't, cleanly) — we just ignore its result.

A `producer` is any callable that returns a QImage (or None) and runs on a
worker thread — so even slow work like exiftool RAW-preview extraction stays
off the GUI thread. The optional target width is applied (downscale) on the
worker too. on_ready is always invoked on the GUI thread.

The generation counter is not enough on its own: a widget can be DESTROYED while
its image is still loading, without anything bumping the generation (the map
rebuilds its filmstrip and no-GPS tray on every refresh, and a dialog can be closed
mid-load). Delivering into a deleted C++ object raises
"RuntimeError: wrapped C/C++ object of type QLabel has been deleted", so _deliver
checks the callback's owner is still alive — see below.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt6 import sip
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

logger = logging.getLogger("speciesviewer")

ImageProducer = Callable[[], Optional[QImage]]
PixmapCallback = Callable[[QPixmap], None]


class _LoaderSignals(QObject):
    # callback, QImage, generation, on_cache (or None)
    done = pyqtSignal(object, object, int, object)


class _LoadTask(QRunnable):
    def __init__(self, producer, target_w, on_ready, gen, signals, on_cache=None,
                 is_current=None):
        super().__init__()
        # Asked just before the work starts: a load cancelled while it waited in
        # the pool skips the work instead of doing it and having the result
        # dropped. Scrolling fast through a big day queued every card passed on
        # the way, and the cards you stopped at waited behind all of them.
        self._is_current = is_current
        self._producer = producer
        self._target_w = target_w
        self._on_ready = on_ready
        self._gen = gen
        self._signals = signals
        self._on_cache = on_cache

    def run(self):
        if self._is_current is not None and not self._is_current():
            return                       # cancelled before it started: nothing to do
        try:
            img = self._producer()
        except Exception:
            img = None
        if img is not None and not img.isNull() and self._target_w:
            if img.width() > self._target_w:
                img = img.scaledToWidth(self._target_w, Qt.TransformationMode.SmoothTransformation)
        self._signals.done.emit(self._on_ready, img if img is not None else QImage(),
                                self._gen, self._on_cache)


class AsyncImageLoader(QObject):
    """One per host (grid/viewer). Owns a generation counter for cancellation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gen = 0
        self._pool = QThreadPool.globalInstance()
        self._signals = _LoaderSignals()
        self._signals.done.connect(self._deliver)

    def cancel_all(self) -> None:
        """Drop every outstanding load — call when the visible set changes."""
        self._gen += 1

    def request(self, producer: ImageProducer, on_ready: PixmapCallback,
                target_w: int | None = None, on_cache=None) -> None:
        """`on_cache`, if given, is called on the GUI thread with the finished
        QPixmap BEFORE the target-widget-alive check — so the result is stored
        in a shared cache even if the widget that asked for it is already gone.
        It must not touch a widget (capture the collection / cache, not a card)."""
        gen = self._gen
        task = _LoadTask(producer, target_w, on_ready, gen, self._signals, on_cache,
                         is_current=lambda: self._gen == gen)
        self._pool.start(task)

    def _deliver(self, on_ready, qimage, gen, on_cache=None):
        if gen != self._gen:
            return  # stale — the view moved on

        # The widget that asked for this image may be GONE by now: the map rebuilds
        # its filmstrip and no-GPS tray on every refresh, and closing a dialog
        # destroys its cards mid-load. Neither bumps the generation, so the guard
        # above does not catch it, and painting into a deleted C++ object raises
        # RuntimeError. Drop the result instead — the widget it was for no longer
        # exists, so there is nothing to show and nothing to fix.
        px = QPixmap.fromImage(qimage) if (qimage is not None and not qimage.isNull()) else QPixmap()

        # Cache the finished pixmap first — the callback owner (a card) may be
        # gone, but the cache is not, and re-decoding on the next rebuild is
        # exactly what this avoids.
        if on_cache is not None and not px.isNull():
            try:
                on_cache(px)
            except Exception as e:
                logger.debug(f"thumb cache put skipped: {e}")

        # The widget that asked for this image may be GONE by now: the map rebuilds
        # its filmstrip and no-GPS tray on every refresh, and closing a dialog
        # destroys its cards mid-load. Neither bumps the generation, so the guard
        # above does not catch it, and painting into a deleted C++ object raises
        # RuntimeError. Drop the result instead — the widget it was for no longer
        # exists, so there is nothing to show and nothing to fix.
        owner = getattr(on_ready, "__self__", None)
        if isinstance(owner, QObject) and sip.isdeleted(owner):
            return

        try:
            on_ready(px)
        except RuntimeError as e:
            # Same race, lost between the check above and the call (or a callback
            # that is not a bound method of a QObject). Not actionable: log at debug
            # and carry on rather than taking the whole loader down.
            logger.debug(f"Image delivery skipped — target widget went away: {e}")
