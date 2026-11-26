"""The AI during a card read — it looks at each day as soon as it is copied.

ONE JOB: take batches of copied pictures (a day folder's files, handed over by
the copy thread through add()) and measure them for the AI, one batch at a
time, on this thread (engine.day_measure, names=False) — while the card copies
the next day. AI… on that day then finds everything measured and opens at once.

INVARIANTS
- Writes nothing next to the pictures: only the app-data caches.
- Never slows or stops the copy: add() only queues; an error in a batch is
  logged and the next batch goes on.
- A batch is "a day" now; it may later be part of a long day (every ~250
  pictures) — engine.day_measure takes either.
"""
from __future__ import annotations

import logging
import queue
import time

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("speciesviewer")


class CardAiWorker(QThread):
    state = pyqtSignal(str, str)       # day, what to show in the day table
    all_done = pyqtSignal(dict)        # {"days", "pictures", "animals", "seconds", "stopped"}

    def __init__(self, master_root: str, aggressive: bool = False, parent=None):
        super().__init__(parent)
        self._master_root = master_root
        self._aggressive = aggressive
        self._queue: queue.Queue = queue.Queue()
        self._stop = False

    # ── called from other threads ────────────────────────────────────────────
    def add(self, day: str, paths) -> None:
        """A day is on disk (called on the COPY thread: only queues)."""
        self.state.emit(day, "waiting")
        self._queue.put((day, list(paths)))

    def no_more(self) -> None:
        """The copy is over: finish the queued days, then stop."""
        self._queue.put(None)

    def stop(self) -> None:
        self._stop = True
        self._queue.put(None)

    # ── this thread ──────────────────────────────────────────────────────────
    def run(self):
        from engine.day_measure import card_batch, measure_day
        t0 = time.time()
        total = {"days": 0, "pictures": 0, "animals": 0, "failed": 0, "stopped": False}
        while not self._stop:
            item = self._queue.get()
            if item is None:
                break
            day, paths = item
            t_day = time.time()
            try:
                self.state.emit(day, "reading the dates")
                pics = card_batch(paths)
                if not pics:
                    self.state.emit(day, "")
                    continue
                last = [0.0]

                def progress(what, i, n, day=day):
                    now = time.time()
                    if now - last[0] >= 0.5 or i == n:       # the table, not a flood
                        last[0] = now
                        self.state.emit(day, f"{what.lower()} {i}/{n}" if n else what.lower())
                measure_day(pics, "", self._master_root or None, names=False,
                            aggressive=self._aggressive, progress=progress,
                            stopped=lambda: self._stop)
                if self._stop:
                    self.state.emit(day, "stopped")
                    break
                animals = sum(1 for p in pics if p.get("box"))
                total["days"] += 1
                total["pictures"] += len(pics)
                total["animals"] += animals
                self.state.emit(day, f"✓ {animals} of {len(pics)} with an animal")
                logger.info(f"AI during the card read: {day} — {len(pics)} pictures, "
                            f"{animals} with an animal, {time.time() - t_day:.0f} s")
            except Exception as e:
                logger.warning(f"AI during the card read: {day} failed: {e}", exc_info=True)
                total["failed"] += 1
                self.state.emit(day, "⚠ failed (see the log)")
        total["stopped"] = self._stop
        total["seconds"] = time.time() - t0
        self.all_done.emit(total)
