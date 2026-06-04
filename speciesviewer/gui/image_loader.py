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
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

ImageProducer = Callable[[], Optional[QImage]]
PixmapCallback = Callable[[QPixmap], None]


class _LoaderSignals(QObject):
    # callback, QImage, generation
    done = pyqtSignal(object, object, int)


class _LoadTask(QRunnable):
    def __init__(self, producer, target_w, on_ready, gen, signals):
        super().__init__()
        self._producer = producer
        self._target_w = target_w
        self._on_ready = on_ready
        self._gen = gen
        self._signals = signals

    def run(self):
        try:
            img = self._producer()
        except Exception:
            img = None
        if img is not None and not img.isNull() and self._target_w:
            if img.width() > self._target_w:
                img = img.scaledToWidth(self._target_w, Qt.TransformationMode.SmoothTransformation)
        self._signals.done.emit(self._on_ready, img if img is not None else QImage(), self._gen)


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
                target_w: int | None = None) -> None:
        task = _LoadTask(producer, target_w, on_ready, self._gen, self._signals)
        self._pool.start(task)

    def _deliver(self, on_ready, qimage, gen):
        if gen != self._gen:
            return  # stale — the view moved on
        px = QPixmap.fromImage(qimage) if (qimage is not None and not qimage.isNull()) else QPixmap()
        on_ready(px)
