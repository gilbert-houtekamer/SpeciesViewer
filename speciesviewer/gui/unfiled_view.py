"""Unfiled pictures — the inbox between a card and a trip.

ONE JOB: show what is sitting in <CurrentPictures>/<year>/<year> unfiled/ and let
each day be filed under a trip. The list is read from DISK every time it opens
(engine.unfiled.scan_unfiled) — this screen owns no state, so it cannot disagree
with what is actually there, and it never touches the Days registry.

Filing is a directory move. Deliberately offered BEFORE Read & Summarize: at that
point nothing in the trip or the master points at the old path, so the move costs
nothing. Once a day has been summarised or frozen, moving it is no longer this
screen's business.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QMessageBox, QComboBox, QDialogButtonBox,
)
from PyQt6.QtCore import Qt

from engine import unfiled
from gui import theme
from widgets.small_widgets import HTMLLabel


class _PickTripDialog(QDialog):
    """Choose the trip to file a day under. Trips whose date range covers the day
    are listed first and preselected — a suggestion, not a decision: overlapping
    ranges are normal (a virtual trip over the same dates), so the whole list
    stays reachable."""

    def __init__(self, parent, day: str, trips: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("File day under a trip")
        self.setMinimumWidth(520)
        self._trips = trips
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{day}</b>"))

        covering = [t for t in trips if t["covers"]]
        if covering:
            note = QLabel(f"{len(covering)} trip(s) cover this date — shown first.")
        else:
            note = QLabel("No trip's dates cover this day — pick one anyway, or "
                          "widen the trip's dates first.")
            note.setStyleSheet(f"color:{theme.WARN};")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.combo = QComboBox()
        for t in trips:
            mark = "● " if t["covers"] else "   "
            span = f'  ({t["fromdate"]} … {t["todate"]})' if t["fromdate"] else ""
            self.combo.addItem(f'{mark}{t["title"]}  [{t["trip_id"]}]{span}', t)
        lay.addWidget(self.combo)

        self.dest_lbl = QLabel("")
        self.dest_lbl.setWordWrap(True)
        self.dest_lbl.setStyleSheet(f"color:{theme.INK_SOFT}; font-size:11px;")
        lay.addWidget(self.dest_lbl)
        self.combo.currentIndexChanged.connect(self._show_dest)
        self._show_dest()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Move day")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _show_dest(self):
        t = self.combo.currentData()
        self.dest_lbl.setText(f"Moves into:  {t['picture_root']}" if t else "")

    def chosen(self) -> dict | None:
        return self.combo.currentData()


class UnfiledInboxDialog(QDialog):
    """The inbox itself: one row per unfiled day, with Move to trip."""

    def __init__(self, parent):
        super().__init__(parent)
        self.passed_parent = parent
        self.logger = parent.logger
        self.setWindowTitle("Unfiled pictures")
        self.resize(720, 420)

        lay = QVBoxLayout(self)
        lay.addWidget(HTMLLabel(
            "<p><b>Unfiled pictures</b><br>"
            "Days read off a card that do not belong to a trip yet. They sit in "
            "<i>&lt;year&gt; unfiled</i> next to your trip folders and are claimed by "
            "nothing — read more cards now and decide later.</p>"
            "<p>File a day by moving it into a trip's pictures folder. Do that "
            "<b>before</b> Read &amp; Summarize, while nothing points at it yet.</p>"
        ))

        self.root_lbl = QLabel("")
        self.root_lbl.setWordWrap(True)
        self.root_lbl.setStyleSheet(f"color:{theme.INK_SOFT}; font-size:11px;")
        lay.addWidget(self.root_lbl)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Day", "Pictures", "Folder"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda *_: self.on_move())
        lay.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        row = QHBoxLayout()
        self.read_btn = QPushButton("Read a card…")
        self.read_btn.setToolTip("Copy another card into the unfiled inbox")
        self.cull_btn = QPushButton("Review && cull…")
        self.cull_btn.setToolTip(
            "Open this day in the picture grid — species, rating, colour, GPS and "
            "delete all work here; edits go into the .xmp beside each picture and "
            "travel with the day when you file it")
        self.move_btn = QPushButton("File under a trip…")
        self.open_btn = QPushButton("Open folder")
        self.refresh_btn = QPushButton("Refresh")
        close_btn = QPushButton("Close")
        row.addWidget(self.read_btn)
        row.addStretch()
        row.addWidget(self.open_btn)
        row.addWidget(self.cull_btn)
        row.addWidget(self.move_btn)
        row.addWidget(self.refresh_btn)
        row.addWidget(close_btn)
        lay.addLayout(row)

        self.read_btn.clicked.connect(self.on_read_card)
        self.cull_btn.clicked.connect(self.on_cull)
        self.move_btn.clicked.connect(self.on_move)
        self.open_btn.clicked.connect(self.on_open)
        self.refresh_btn.clicked.connect(self.reload)
        close_btn.clicked.connect(self.accept)

        self.reload()

    # ── data ────────────────────────────────────────────────────────────────
    def reload(self):
        base = unfiled.currentpictures_root(
            _active_picture_dir())
        self._rows = unfiled.scan_unfiled(base)
        self.table.setRowCount(0)
        for r in self._rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(r["day"]))
            cnt = QTableWidgetItem(str(r["pictures"]))
            cnt.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 1, cnt)
            self.table.setItem(i, 2, QTableWidgetItem(r["folder"]))
        self.table.resizeColumnsToContents()
        if base:
            self.root_lbl.setText(f"Looking in: {base}\\<year>\\<year> unfiled")
        else:
            self.root_lbl.setText(
                "No pictures folder configured yet — read a card into the inbox "
                "once and it will be set.")
        total = sum(r["pictures"] for r in self._rows)
        self.status.setText(
            f"{len(self._rows)} unfiled day(s), {total} picture(s)." if self._rows
            else "Nothing unfiled — the inbox is empty.")
        has = bool(self._rows)
        self.move_btn.setEnabled(has)
        self.open_btn.setEnabled(has)
        self.cull_btn.setEnabled(has)
        if has:
            self.table.selectRow(0)

    def _selected(self) -> dict | None:
        i = self.table.currentRow()
        return self._rows[i] if 0 <= i < len(self._rows) else None

    # ── actions ─────────────────────────────────────────────────────────────
    def on_read_card(self):
        read_card_into_unfiled(self.passed_parent)
        self.reload()

    def on_open(self):
        row = self._selected()
        if row:
            from utils.open_path import open_path
            open_path(row["folder"])

    def on_cull(self):
        """Cull an unfiled day in place — the full culling grid, same as a day in
        a trip. Everything it writes lands in the .xmp beside each picture, so the
        work is not tied to this folder and survives the move into a trip. Culling
        BEFORE filing is the point: what you throw away never reaches the trip."""
        row = self._selected()
        if not row:
            return
        from gui.image_viewer import ImagePreviewDialog
        # No fpr and no trip → sidecar-backed culling mode, exactly like reviewing
        # an SD-card folder. trip_root_dir = the day itself, so a blue/purple
        # delete trashes into <day>/.trash and stays with the day.
        dlg = ImagePreviewDialog(self.passed_parent, image_dir=row["folder"],
                                 trip_root_dir=row["folder"], mode="all")
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.passed_parent._unfiled_cull_dlg = dlg   # keep a ref (PyQt GC)
        dlg.finished.connect(lambda *_: self.reload())
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def on_move(self):
        row = self._selected()
        if not row:
            return
        trips = unfiled.trips_covering(row["day"])
        if not trips:
            QMessageBox.information(
                self, "No trips",
                "No trip has a pictures folder to move this day into.\n\n"
                "Create the trip first (Trips ▸ Create Trip), then come back.")
            return
        dlg = _PickTripDialog(self, row["day"], trips)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        trip = dlg.chosen()
        if not trip:
            return
        ok, msg = unfiled.move_day_to(row["folder"], trip["picture_root"])
        if not ok:
            QMessageBox.warning(self, "Could not file this day", msg)
            self.logger.warning(f"Unfiled → {trip['trip_id']}: {msg}")
            self.reload()
            return
        self.logger.info(f"Filed {row['day']} ({row['pictures']} pictures) under "
                         f"{trip['trip_id']}: {trip['picture_root']}")
        self.status.setText(f"✅ {msg}")
        self.reload()


def _active_picture_dir() -> str:
    """A sample path to guess the pictures base from, when it is not configured."""
    try:
        from utils.config import _config
        return _config.get("project", "picturedirectory", fallback="")
    except Exception:
        return ""


def ensure_currentpictures_root(parent) -> str:
    """The pictures base, ASKED ONCE and then stored — never silently guessed.

    A guess is fine for pre-filling a field the user can see; it is not fine as
    the thing that decides where a card is written. A card of mushroom photos
    from last autumn must land in `<base>/2025/2025 unfiled/`, and that is only
    predictable if `base` is a settled fact. Stored in [currentpictures]
    rootdirectory — the same setting that makes a trip.ini's picture_root
    portable across machines. Returns "" when the user declines."""
    from utils.config import get_currentpictures_root, set_setting
    from PyQt6.QtWidgets import QFileDialog
    current = get_currentpictures_root()
    if current and os.path.isdir(current):
        return os.path.normpath(current)
    guess = unfiled.guess_currentpictures_root(_active_picture_dir())
    QMessageBox.information(
        parent, "Where do you keep your pictures?",
        "SpeciesViewer needs to know the folder that holds your picture years "
        "(the one with 2025, 2026, … inside it).\n\n"
        "Unfiled cards are read into <that folder>\\<year>\\<year> unfiled, and "
        "trips store their pictures folder relative to it — so a trip stays "
        "readable on another machine.\n\n"
        "You are asked this once."
        + (f"\n\nSuggested: {guess}" if guess else ""))
    chosen = QFileDialog.getExistingDirectory(
        parent, "Pictures folder (the one containing 2025, 2026, …)", guess or "")
    if not chosen:
        return ""
    chosen = os.path.normpath(chosen)
    set_setting("currentpictures", "rootdirectory", chosen)
    try:
        parent.logger.info(f"Pictures root set to {chosen} "
                           f"([currentpictures] rootdirectory).")
    except Exception:
        pass
    return chosen


def open_unfiled_inbox(main_window):
    if not ensure_currentpictures_root(main_window):
        return
    UnfiledInboxDialog(main_window).exec()


def read_card_into_unfiled(main_window):
    """Read a card into the unfiled inbox — no trip is chosen or claimed."""
    if not ensure_currentpictures_root(main_window):
        return
    from gui.trip_process import ReadSDCardDialog
    ReadSDCardDialog(main_window, None, unfiled_mode=True).exec()
