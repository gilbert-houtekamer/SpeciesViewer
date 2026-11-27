"""The AI during a card read — it looks at each day as soon as it is copied.

ONE JOB: take batches of copied pictures (a day folder's files, handed over by
the copy thread through add()) and measure them for the AI, one batch at a
time, on this thread (engine.day_measure, names=False) — while the card copies
the next day. AI… on that day then finds everything measured and opens at once.

INVARIANTS
- Writes nothing next to the pictures: only the app-data caches.
- Never slows or stops the copy: add() only queues; an error in a batch is
  logged and the next batch goes on.
- A batch is a day, or a part of a long day (about 250 pictures, handed over
  while the rest of the day still copies: batch.sv0ReadSDCard.DAY_PART) —
  engine.day_measure takes either. A day counts once, at its last batch.
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
        self._parts: dict[str, int] = {}          # day → batches queued so far

    # ── called from other threads ────────────────────────────────────────────
    def add(self, day: str, paths, last: bool = True) -> None:
        """Pictures of a day are on disk: a part of it (last=False) or the rest
        of it (called on the COPY thread: only queues)."""
        n = self._parts[day] = self._parts.get(day, 0) + 1
        if n == 1:
            self.state.emit(day, "waiting")
        self._queue.put((day, list(paths), last, n))

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
        per_day: dict[str, list] = {}               # day → [pictures, animals]
        failed: set[str] = set()
        while not self._stop:
            item = self._queue.get()
            if item is None:
                break
            day, paths, last_part, part = item
            seen = per_day.setdefault(day, [0, 0])
            # "part 2: …" while a long day comes in parts (the copy goes on)
            tag = f"part {part}: " if part > 1 or not last_part else ""
            t_day = time.time()
            try:
                self.state.emit(day, f"{tag}reading the dates")
                pics = card_batch(paths)
                if not pics:
                    if last_part:
                        self._day_finished(day, seen, total)
                    continue
                shown = [0.0]

                def progress(what, i, n, day=day, tag=tag):
                    now = time.time()
                    if now - shown[0] >= 0.5 or i == n:      # the table, not a flood
                        shown[0] = now
                        self.state.emit(day, tag + (f"{what.lower()} {i}/{n}" if n
                                                    else what.lower()))
                measure_day(pics, "", self._master_root or None, names=False,
                            aggressive=self._aggressive, progress=progress,
                            stopped=lambda: self._stop)
                if self._stop:
                    self.state.emit(day, "stopped")
                    break
                animals = sum(1 for p in pics if p.get("box"))
                seen[0] += len(pics)
                seen[1] += animals
                total["pictures"] += len(pics)
                total["animals"] += animals
                logger.info(f"AI during the card read: {day} {tag}— {len(pics)} pictures, "
                            f"{animals} with an animal, {time.time() - t_day:.0f} s")
                if last_part:
                    self._day_finished(day, seen, total, day in failed)
                else:
                    self.state.emit(day, f"✓ {seen[0]} pictures so far — copying the rest")
            except Exception as e:
                logger.warning(f"AI during the card read: {day} {tag}failed: {e}",
                               exc_info=True)
                failed.add(day)
                self.state.emit(day, "⚠ failed (see the log)")
        total["failed"] = len(failed)
        total["stopped"] = self._stop
        total["seconds"] = time.time() - t0
        self.all_done.emit(total)

    def _day_finished(self, day: str, seen: list, total: dict, had_error: bool = False):
        total["days"] += 1
        self.state.emit(day, ("⚠ part failed (see the log); " if had_error else "✓ ")
                        + f"{seen[1]} of {seen[0]} with an animal")
