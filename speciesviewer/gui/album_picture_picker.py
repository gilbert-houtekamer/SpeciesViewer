"""Pick an album's picture from the album's own date range in the master.

ONE JOB: show the master pictures an album over [from, to] would hold — one per
species, its best — and return the JPG the person clicks. Nothing is written
here; the caller crops and saves (gui.album_picture_dialog / utils.album_picture).

INVARIANT: only pictures inside the date range are offered, and only ones whose
JPG exists on disk — so whatever is picked can be made into the 600x450 picture.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QImageReader, QPixmap
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListView,
                             QListWidget, QListWidgetItem, QVBoxLayout)

logger = logging.getLogger("speciesviewer")

THUMB = 150


def _rate(row) -> float:
    try:
        return float(row.get("Rate") or 0)
    except (TypeError, ValueError):
        return 0.0


def range_pictures(master_root, by_day: dict) -> list:
    """[(jpg Path, caption, day)] from pictures_in_daterange's {day: [rows]}:
    per species the best-rated picture with a JPG on disk, best first (ties:
    earliest). A picture without a species name stands for itself."""
    root = Path(master_root)
    best: dict = {}
    for day, rows in sorted(by_day.items()):
        for r in rows:
            jpg = (r.get("BestJPG") or "").strip()
            rel = (r.get("TargetPath") or r.get("BestJPGPathName") or "").strip()
            if not jpg or not rel:
                continue
            sp = ((r.get("WetSoortNaam") or "").strip().lower()
                  or r.get("UniquePictureName", ""))
            name = (r.get("EngSoortNaam") or r.get("NLSoortNaam")
                    or r.get("WetSoortNaam") or Path(jpg).stem).strip()
            cand = (_rate(r), day, root / rel / jpg, name[:1].upper() + name[1:])
            have = best.get(sp)
            if have is None or cand[0] > have[0]:
                best[sp] = cand
    ranked = sorted(best.values(), key=lambda c: (-c[0], c[1]))
    return [(p, cap, day) for _r, day, p, cap in ranked if p.is_file()]


class AlbumPicturePicker(QDialog):
    """Grid of the range's pictures; double-click or Choose returns one."""

    def __init__(self, parent, items: list, fromdate: str, todate: str):
        super().__init__(parent)
        self.setWindowTitle("Choose the album picture")
        self.resize(900, 640)
        self.chosen: Path | None = None
        self._items = items
        lay = QVBoxLayout(self)
        note = QLabel(
            f"The best picture of each species between {fromdate} and {todate}, "
            f"best rated first. Choose one; the next window shows the 600×450 crop "
            f"before anything is saved.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self._list = QListWidget()
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setIconSize(QSize(THUMB, THUMB))
        self._list.setGridSize(QSize(THUMB + 24, THUMB + 44))
        self._list.setWordWrap(False)
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setStyleSheet("QListWidget::item:selected { border: 4px solid #2f7de1; }")
        # Every tile starts with a grey square of the full size: the tile is
        # measured right (not text-high) and shows a picture is still coming.
        blank = QPixmap(THUMB, THUMB)
        blank.fill(QColor("#55595f"))
        placeholder = QIcon(blank)
        for path, caption, day in items:
            it = QListWidgetItem(placeholder, caption)
            it.setSizeHint(QSize(THUMB + 20, THUMB + 40))
            it.setToolTip(f"{caption}\n{day}\n{path.name}")
            it.setData(Qt.ItemDataRole.UserRole, str(path))
            self._list.addItem(it)
        self._list.itemDoubleClicked.connect(lambda _it: self._choose())
        lay.addWidget(self._list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Choose")
        buttons.accepted.connect(self._choose)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        # Thumbnails a few at a time, so the window is there at once.
        self._next = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._load_some)
        self._timer.start(0)

    def _load_some(self, n: int = 8):
        end = min(self._next + n, self._list.count())
        for i in range(self._next, end):
            it = self._list.item(i)
            reader = QImageReader(it.data(Qt.ItemDataRole.UserRole))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(THUMB, THUMB,
                                                 Qt.AspectRatioMode.KeepAspectRatio))
            img = reader.read()
            if not img.isNull():
                it.setIcon(QIcon(QPixmap.fromImage(img)))
        self._next = end
        if self._next >= self._list.count():
            self._timer.stop()

    def _choose(self):
        it = self._list.currentItem()
        if it is None:
            return
        self.chosen = Path(it.data(Qt.ItemDataRole.UserRole))
        self.accept()
