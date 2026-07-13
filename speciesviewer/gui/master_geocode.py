"""Master ▸ "Add missing place names…" — reverse-geocode the pictures that have a
coordinate but no place text at all.

Why it exists: the published species map showed dots labelled only
`41.3455, -0.3424`. Those pictures carry GPS and nothing else — Location, City,
Municipality, State and Country are all empty, because they were never
reverse-geocoded. The fix belongs in the DATA, not in the page (user's call).

The flow follows the house rule for data fixes — look, then decide, then write:

    Scan  →  Look up (Nominatim, ~1/sec, cancellable)  →  REVIEW TABLE  →  Write

Nothing is written until the review table has been seen and confirmed, and any
row can be unticked. Pictures that already have ANY place text are never touched,
so this cannot overwrite a name typed by hand.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

logger = logging.getLogger("speciesviewer")

GPS_COL, PLACE_COL, N_COL = 0, 1, 2


class _LookupThread(QThread):
    """Nominatim runs off the UI thread: ~534 coordinates at one per second is a
    nine-minute job, and the window must stay alive (and cancellable) throughout."""
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(list)

    def __init__(self, db, gaps, parent=None):
        super().__init__(parent)
        self._db, self._gaps, self._stop = db, gaps, False

    def stop(self):
        self._stop = True

    def run(self):
        from engine.geo_backfill import plan
        rows = plan(self._db, self._gaps,
                    progress_cb=lambda d, t, p: self.progress.emit(d, t, p),
                    should_stop=lambda: self._stop)
        self.done.emit(rows)


class _WriteThread(QThread):
    """The WRITE runs off the UI thread too.

    It is not the 2,415 pictures that cost time — write_location_batch collapses a
    coordinate's whole file list into ONE exiftool call, so this is 534 spawns, not
    2,415. But 534 spawns is still minutes, and the first version ran them on the UI
    thread: the window went grey and said "Not Responding" while it worked perfectly.
    A progress bar you cannot repaint is worse than no progress bar."""
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, db, master_root, rows, parent=None):
        super().__init__(parent)
        self._db, self._root, self._rows = db, master_root, rows

    def run(self):
        from engine.geo_backfill import apply
        try:
            res = apply(self._db, self._root, self._rows,
                        progress_cb=lambda d, t, p: self.progress.emit(d, t, p))
        except Exception as e:
            logger.exception("geo backfill failed")
            self.failed.emit(str(e))
            return
        self.done.emit(res)


class MasterGeocodeDialog(QDialog):
    def __init__(self, parent, db, master_root):
        super().__init__(parent)
        self.setWindowTitle("Add missing place names")
        self.resize(880, 560)
        self._db, self._master_root = db, master_root
        self._rows: list[dict] = []
        self._thread = None

        lay = QVBoxLayout(self)
        self._head = QLabel("Looking for pictures with a coordinate but no place…")
        self._head.setWordWrap(True)
        lay.addWidget(self._head)

        self._bar = QProgressBar()
        self._bar.setVisible(False)
        lay.addWidget(self._bar)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(
            ["Coordinate", "Place found (OpenStreetMap)", "Pictures"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            PLACE_COL, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            GPS_COL, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self._table)

        row = QHBoxLayout()
        self._look_btn = QPushButton("Look up places")
        self._look_btn.clicked.connect(self._on_lookup)
        row.addWidget(self._look_btn)
        self._write_btn = QPushButton("Write the ticked places")
        self._write_btn.setEnabled(False)
        self._write_btn.clicked.connect(self._on_write)
        row.addWidget(self._write_btn)
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)

        self._scan()

    # ── scan ──────────────────────────────────────────────────────────────
    def _scan(self):
        from engine.geo_backfill import find_gaps
        self._gaps = find_gaps(self._db)
        pics = sum(len(v) for v in self._gaps.values())
        if not pics:
            self._head.setText(
                "Every master picture with a coordinate already has a place name. "
                "Nothing to do.")
            self._look_btn.setEnabled(False)
            return
        mins = max(1, round(len(self._gaps) * 1.05 / 60))
        self._head.setText(
            f"<b>{pics:,} pictures</b> have a coordinate but no place name at all "
            f"(no Location, City, State or Country). They sit on "
            f"<b>{len(self._gaps):,} distinct coordinates</b> — pictures taken in "
            f"one burst share one fix — so this needs {len(self._gaps):,} lookups, "
            f"not {pics:,}.<br><br>"
            f"OpenStreetMap allows about one lookup a second, so this takes roughly "
            f"<b>{mins} minutes</b>. Nothing is written until you have seen the "
            f"result and pressed Write. Pictures that already have a place name are "
            f"never touched.")

    # ── look up ───────────────────────────────────────────────────────────
    def _on_lookup(self):
        if self._thread:                      # running → this is the Cancel press
            self._thread.stop()
            self._look_btn.setEnabled(False)
            self._look_btn.setText("Stopping…")
            return
        self._bar.setVisible(True)
        self._bar.setRange(0, len(self._gaps))
        self._bar.setValue(0)
        self._look_btn.setText("Stop")
        self._write_btn.setEnabled(False)
        self._thread = _LookupThread(self._db, self._gaps, self)
        self._thread.progress.connect(self._on_progress)
        self._thread.done.connect(self._on_done)
        self._thread.start()

    def _on_progress(self, done, total, place):
        self._bar.setValue(done)
        self._bar.setFormat(f"%v of %m — {place[:60]}")

    def _on_done(self, rows):
        self._thread = None
        self._rows = rows
        self._look_btn.setText("Look up places")
        self._look_btn.setEnabled(True)
        self._bar.setVisible(False)
        self._fill_table()

    def _fill_table(self):
        usable = [r for r in self._rows if r["fields"] and not r["error"]]
        failed = [r for r in self._rows if not r["fields"] or r["error"]]
        self._table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            gps = QTableWidgetItem(r["gps"])
            gps.setFlags(gps.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ok = bool(r["fields"]) and not r["error"]
            gps.setCheckState(Qt.CheckState.Checked if ok
                              else Qt.CheckState.Unchecked)
            if not ok:
                gps.setFlags(gps.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(i, GPS_COL, gps)
            self._table.setItem(i, PLACE_COL, QTableWidgetItem(
                r["place"] or f"— {r['error'] or 'nothing found'}"))
            n = QTableWidgetItem(str(r["n"]))
            n.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, N_COL, n)
        pics = sum(r["n"] for r in usable)
        self._head.setText(
            f"Found a place for <b>{len(usable):,} of {len(self._rows):,}</b> "
            f"coordinates, covering <b>{pics:,} pictures</b>. "
            + (f"{len(failed)} could not be resolved and are unticked. "
               if failed else "")
            + "Untick anything that looks wrong, then press Write.")
        self._write_btn.setEnabled(bool(usable))

    # ── write ─────────────────────────────────────────────────────────────
    def _checked_rows(self) -> list[dict]:
        out = []
        for i, r in enumerate(self._rows):
            item = self._table.item(i, GPS_COL)
            if item and item.checkState() == Qt.CheckState.Checked:
                out.append(r)
        return out

    def _on_write(self):
        rows = self._checked_rows()
        pics = sum(r["n"] for r in rows)
        if not rows:
            return
        if QMessageBox.question(
                self, "Write place names",
                f"Write the place name into <b>{pics:,} pictures</b> "
                f"({len(rows):,} coordinates)?<br><br>"
                f"The name goes into each picture's XMP sidecar and into the master "
                f"index, so the website and the maps show it straight away. "
                f"data_pictures.csv is backed up first.<br><br>"
                f"Only pictures that have NO place name are touched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        self._bar.setVisible(True)
        self._bar.setRange(0, len(rows))
        self._bar.setValue(0)
        self._write_btn.setEnabled(False)
        self._look_btn.setEnabled(False)
        self._thread = _WriteThread(self._db, self._master_root, rows, self)
        self._thread.progress.connect(self._on_progress)
        self._thread.done.connect(self._on_written)
        self._thread.failed.connect(self._on_write_failed)
        self._thread.start()

    def _on_write_failed(self, msg: str):
        self._thread = None
        self._bar.setVisible(False)
        self._write_btn.setEnabled(True)
        self._look_btn.setEnabled(True)
        QMessageBox.critical(self, "Add missing place names",
                             f"Writing failed:\n{msg}")

    def _on_written(self, res: dict):
        self._thread = None
        self._bar.setVisible(False)
        self._look_btn.setEnabled(True)
        QMessageBox.information(
            self, "Add missing place names",
            f"Wrote place names into {res['pictures']:,} pictures "
            f"({res['coordinates']:,} coordinates, {res['files']:,} sidecars).\n\n"
            f"Backup: {res['backup'] or '(none needed)'}\n\n"
            f"Regenerate a species page to see the names on its map.")
        self._table.setRowCount(0)
        self._rows = []
        self._scan()


def open_master_geocode(main_window):
    """Master ▸ Add missing place names…"""
    from utils.config import _config
    from db.master_db import MasterDB
    root = _config.get("master", "MasterBiotaDatabase", fallback="").strip().strip('"')
    if not root:
        QMessageBox.warning(main_window, "Add missing place names",
                            "No master repository is configured.")
        return
    MasterGeocodeDialog(main_window, MasterDB(root), root).exec()
