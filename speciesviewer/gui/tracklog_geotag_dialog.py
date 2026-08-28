"""Master ▸ "Geotag from Tracklog…" — fill missing GPS and/or altitude for
master pictures from a GPX track or an OM-1 NMEA `.log`, matched by time.

Two gaps this closes, in one pass over one tracklog:
  * a day the camera GPS wasn't enabled -> no lat/lon at all on the photo;
  * GPSAltitude, which the camera never records even when GPS was on.

Follows the same house rule as "Add missing place names…":

    Browse for a tracklog  ->  Plan (no network, in-memory join)  ->
    REVIEW TABLE  ->  Write

Nothing is written until the review table has been seen and confirmed, and
any row can be unticked. A photo with no track point within the cutoff is
simply never proposed — never a stale/garbage match.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout)
from gui.widgets import wrapped

logger = logging.getLogger("speciesviewer")

PICTURE_COL, WHEN_COL, STATUS_COL, PROPOSED_COL, GAP_COL = 0, 1, 2, 3, 4


class _PlanThread(QThread):
    done = pyqtSignal(list, dict)
    failed = pyqtSignal(str)

    def __init__(self, source, points, camera_utc_offset, max_gap_s, parent=None):
        super().__init__(parent)
        self._source, self._points = source, points
        self._offset, self._max_gap_s = camera_utc_offset, max_gap_s

    def run(self):
        try:
            rows, stats = self._source.plan(self._points, self._offset, self._max_gap_s)
        except Exception as e:
            logger.exception("tracklog geotag plan failed")
            self.failed.emit(str(e))
            return
        self.done.emit(rows, stats)


class _WriteThread(QThread):
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, source, rows, parent=None):
        super().__init__(parent)
        self._source, self._rows = source, rows

    def run(self):
        try:
            res = self._source.apply(self._rows)
        except Exception as e:
            logger.exception("tracklog geotag apply failed")
            self.failed.emit(str(e))
            return
        self.done.emit(res)


class TracklogGeotagDialog(QDialog):
    def __init__(self, parent, source, *, initial_dir: str = "",
                default_tracklog_paths: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Geotag from Tracklog")
        self.resize(880, 560)
        self._source = source
        self._initial_dir = initial_dir
        self._points: list[dict] = []
        self._rows: list[dict] = []
        self._stats: dict = {}
        self._thread = None

        lay = QVBoxLayout(self)
        self._head = QLabel(
            "Fills missing GPS coordinates (for a day the camera's GPS wasn't "
            "enabled) and/or altitude (which the camera never records) by "
            "matching pictures to a tracklog by time.\n\n"
            "Choose an OM-1 tracking-app log (.log) and/or a phone app's GPX "
            "export (.gpx) covering the same day(s).")
        self._head.setWordWrap(True)
        lay.addWidget(self._head)

        browse_row = QHBoxLayout()
        self._file_label = QLabel("No tracklog file chosen.")
        browse_row.addWidget(self._file_label, 1)
        browse_btn = QPushButton("Browse for tracklog file(s)…")
        browse_btn.clicked.connect(self._on_browse)
        browse_row.addWidget(browse_btn)
        lay.addLayout(browse_row)

        offset_row = QHBoxLayout()
        offset_row.addWidget(wrapped(
            "Camera time zone offset (e.g. +02:00) — the camera's clock has no "
            "stored offset, so this is needed to line photo times up with the "
            "tracklog's UTC times:"))
        self._offset_edit = QLineEdit()
        self._offset_edit.setPlaceholderText("+02:00")
        self._offset_edit.setFixedWidth(90)
        offset_row.addWidget(self._offset_edit)
        offset_row.addStretch()
        lay.addLayout(offset_row)

        self._bar = QProgressBar()
        self._bar.setVisible(False)
        lay.addWidget(self._bar)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Picture", "Taken", "Status", "Proposed", "Match gap"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            PROPOSED_COL, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._table)

        row = QHBoxLayout()
        self._plan_btn = QPushButton("Plan")
        self._plan_btn.setEnabled(False)
        self._plan_btn.clicked.connect(self._on_plan)
        row.addWidget(self._plan_btn)
        self._write_btn = QPushButton("Write the ticked pictures")
        self._write_btn.setEnabled(False)
        self._write_btn.clicked.connect(self._on_write)
        row.addWidget(self._write_btn)
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)

        if default_tracklog_paths:
            # Programmatic pre-load (the Unfiled/Days row action's
            # auto-detected log) — same UI update _on_browse does after an
            # interactive pick, minus the folder-remembering (that only makes
            # sense for a Browse click). User still presses Plan then Write.
            self._load_paths(default_tracklog_paths)

    # ── browse ────────────────────────────────────────────────────────────
    def _on_browse(self):
        from utils.gui_config import get_cache_gui_section, update_cache_gui_section
        from utils.config import get_gpslogs_root
        # The tracklog usually lives somewhere other than the SD card (a phone
        # export folder, the OM-1 app's export location) — once one has been
        # chosen, remember THAT folder and prefer it over everything else.
        # Next best (especially on a machine where nothing's been browsed
        # yet): the configured GPS-logs root, a real hint rather than a
        # coincidence. initial_dir (the card/day folder) is the last resort.
        start_dir = (get_cache_gui_section("tracklog_geotag", "dir")
                    or get_gpslogs_root() or self._initial_dir)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose tracklog file(s)", start_dir,
            "Tracklog files (*.gpx *.log);;GPX files (*.gpx);;OM-1 log files (*.log)")
        if not paths:
            return
        update_cache_gui_section("tracklog_geotag", "dir", os.path.dirname(paths[0]))
        self._load_paths(paths)

    def _load_paths(self, paths):
        """Load tracklog file(s) and refresh the dialog's UI state — shared
        by an interactive Browse and a programmatic pre-load
        (default_tracklog_paths)."""
        try:
            self._points = self._load(paths)
        except Exception as e:
            QMessageBox.warning(self, "Geotag from Tracklog", f"Couldn't read that file:\n{e}")
            return
        from engine.tracklog_geotag import track_date_range
        lo, hi = track_date_range(self._points)
        names = ", ".join(p.split("/")[-1].split("\\")[-1] for p in paths)
        self._file_label.setText(f"{names}  ({lo} to {hi}, {len(self._points):,} points)")
        self._plan_btn.setEnabled(True)
        self._write_btn.setEnabled(False)
        self._table.setRowCount(0)
        self._rows = []

    def _load(self, paths):
        from engine.tracklog_geotag import load_track
        points = load_track(paths)
        if not self._offset_edit.text().strip():
            # The camera's own recorded UTC offset (EXIF OffsetTimeOriginal,
            # carried into the sidecar), not a correlation guess. A stale time
            # ZONE setting doesn't spoil this: changing a camera's zone moves the
            # clock and the offset together, so the offset still converts its
            # clock to correct UTC. Left blank when the pictures don't state one,
            # so the field reads as "you must tell me" rather than as an answer.
            from engine.geo import camera_offset_from_dates
            dates = self._source.sample_dates(points)
            tz = camera_offset_from_dates(dates)
            if tz is not None:
                total_min = round(tz.utcoffset(None).total_seconds() / 60)
                sign = "+" if total_min >= 0 else "-"
                total_min = abs(total_min)
                self._offset_edit.setText(
                    f"{sign}{total_min // 60:02d}:{total_min % 60:02d}")
        return points

    # ── plan ──────────────────────────────────────────────────────────────
    def _on_plan(self):
        if self._thread:
            return
        offset = self._parse_offset()
        if offset is None:
            QMessageBox.warning(
                self, "Geotag from Tracklog",
                "Enter the camera's time zone offset first (e.g. +02:00) — "
                "without it, photo times can't be lined up with the tracklog.")
            return
        self._bar.setVisible(True)
        self._bar.setRange(0, 0)   # busy indicator — plan() is a fast in-memory join
        self._plan_btn.setEnabled(False)
        self._write_btn.setEnabled(False)
        self._thread = _PlanThread(self._source, self._points, offset, 15 * 60, self)
        self._thread.done.connect(self._on_planned)
        self._thread.failed.connect(self._on_plan_failed)
        self._thread.start()

    def _parse_offset(self):
        from engine.geo import parse_utc_offset
        return parse_utc_offset(self._offset_edit.text())

    def _on_plan_failed(self, msg: str):
        self._thread = None
        self._bar.setVisible(False)
        self._plan_btn.setEnabled(True)
        QMessageBox.critical(self, "Geotag from Tracklog", f"Planning failed:\n{msg}")

    def _on_planned(self, rows, stats):
        self._thread = None
        self._rows = rows
        self._stats = stats
        self._bar.setVisible(False)
        self._plan_btn.setEnabled(True)
        self._fill_table()

    def _fill_table(self):
        from engine.tracklog_geotag import GPS_ACTION
        self._table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            pic = QTableWidgetItem(r["upn"] + (f"  ({r['species']})" if r["species"] else ""))
            pic.setFlags(pic.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            pic.setCheckState(Qt.CheckState.Checked)
            self._table.setItem(i, PICTURE_COL, pic)
            self._table.setItem(i, WHEN_COL, QTableWidgetItem(r["when"]))
            status = "No GPS" if r["action"] == GPS_ACTION else "Has GPS"
            self._table.setItem(i, STATUS_COL, QTableWidgetItem(status))
            if r["action"] == GPS_ACTION:
                proposed = f"{r['proposed_lat']:.6f}, {r['proposed_lon']:.6f}"
                if r["proposed_alt"] is not None:
                    proposed += f"  ·  altitude {r['proposed_alt']:.1f} m"
            else:
                proposed = f"altitude {r['proposed_alt']:.1f} m"
            self._table.setItem(i, PROPOSED_COL, QTableWidgetItem(proposed))
            gap = QTableWidgetItem(f"{r['gap_s']}s")
            gap.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, GAP_COL, gap)
        n_gps = sum(1 for r in self._rows if r["action"] == GPS_ACTION)
        n_alt = sum(1 for r in self._rows if r["proposed_alt"] is not None)
        if not self._rows:
            self._head_note(self._empty_result_note())
        else:
            self._head_note(
                f"<b>{len(self._rows):,} picture(s)</b> matched: "
                f"<b>{n_gps:,}</b> had no GPS at all, "
                f"<b>{n_alt:,}</b> get an altitude. "
                f"Untick anything that looks wrong, then press Write.")
        self._write_btn.setEnabled(bool(self._rows))

    def _empty_result_note(self) -> str:
        """Explain a zero-row plan honestly — each of the three reasons
        _correlate() can skip a photo maps to a different message, so a blank
        table never looks like the join silently failed."""
        considered = self._stats.get("considered", 0)
        if considered == 0:
            return "No pictures to check — nothing to propose."
        outside = self._stats.get("outside_range", 0)
        gap = self._stats.get("gap", 0)
        no_change = self._stats.get("no_change", 0)
        clauses = []
        if outside:
            clauses.append(f"{outside:,} picture(s) fall outside this tracklog's date coverage")
        if gap:
            clauses.append(f"{gap:,} picture(s) didn't match within the 15-minute cutoff")
        if no_change:
            clauses.append(f"{no_change:,} picture(s) already have this GPS/altitude information")
        if not clauses:
            return "Nothing to propose."
        if len(clauses) == 1:
            return f"Nothing to propose: {clauses[0]}."
        return "Nothing to propose: " + "; ".join(clauses) + "."

    def _head_note(self, text):
        self._head.setText(text)

    # ── write ─────────────────────────────────────────────────────────────
    def _checked_rows(self) -> list[dict]:
        out = []
        for i, r in enumerate(self._rows):
            item = self._table.item(i, PICTURE_COL)
            if item and item.checkState() == Qt.CheckState.Checked:
                out.append(r)
        return out

    def _on_write(self):
        rows = self._checked_rows()
        if not rows:
            return
        note = getattr(self._source, "confirm_note", "")
        if QMessageBox.question(
                self, "Geotag from Tracklog",
                f"Write GPS/altitude into <b>{len(rows):,} picture(s)</b>?<br><br>"
                f"Each picture's XMP sidecar is updated."
                + (f" {note}" if note else ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._write_btn.setEnabled(False)
        self._plan_btn.setEnabled(False)
        self._bar.setVisible(True)
        self._bar.setRange(0, 0)
        self._thread = _WriteThread(self._source, rows, self)
        self._thread.done.connect(self._on_written)
        self._thread.failed.connect(self._on_write_failed)
        self._thread.start()

    def _on_write_failed(self, msg: str):
        self._thread = None
        self._bar.setVisible(False)
        self._write_btn.setEnabled(True)
        self._plan_btn.setEnabled(True)
        QMessageBox.critical(self, "Geotag from Tracklog", f"Writing failed:\n{msg}")

    def _on_written(self, res: dict):
        self._thread = None
        self._bar.setVisible(False)
        self._plan_btn.setEnabled(True)
        msg = (f"Updated {res['pictures']:,} picture(s) "
              f"({res['gps_files']:,} sidecars got GPS, "
              f"{res['altitude_files']:,} sidecars got altitude).")
        if "backup" in res:
            msg += f"\n\nBackup: {res['backup'] or '(none needed)'}"
        QMessageBox.information(self, "Geotag from Tracklog", msg)
        self._table.setRowCount(0)
        self._rows = []
        self._write_btn.setEnabled(False)


def open_tracklog_geotag(main_window):
    """Master ▸ Geotag from Tracklog…"""
    from utils.config import _config
    from db.master_db import MasterDB
    from engine.tracklog_geotag import MasterSource
    root = _config.get("master", "MasterBiotaDatabase", fallback="").strip().strip('"')
    if not root:
        QMessageBox.warning(main_window, "Geotag from Tracklog",
                            "No master repository is configured.")
        return
    source = MasterSource(MasterDB(root), root)
    TracklogGeotagDialog(main_window, source).exec()


def open_tracklog_geotag_for_folder(parent, folder: str):
    """Geotag from tracklog… for one already-unfiled/registered day folder —
    the Unfiled/Days row action, independent of a card read. `folder` is a
    YYYY-MM-DD folder (both lanes use that as the folder name). Scans it for
    RAW+XMP pairs the same way ReadSDCardDialog._build_tracklog_entries does
    for a just-read card, then opens the same review dialog, pre-loaded with
    that day's auto-detected log file when one exists."""
    from pathlib import Path
    from gui.trip_process import _PL_RAW_EXTS
    from gui.geolocate import batch_datetime_gps
    from engine.tracklog_geotag import FilesSource, find_day_tracklogs
    from utils.config import get_gpslogs_root

    folder_path = Path(folder)
    xmps = []
    for p in folder_path.iterdir():
        if p.is_file() and p.suffix.lower() in _PL_RAW_EXTS and p.with_suffix(".xmp").exists():
            xmps.append((p.stem, str(p.with_suffix(".xmp"))))
    if not xmps:
        QMessageBox.information(parent, "Geotag from Tracklog",
                                "No RAW+XMP pictures in this day to geotag.")
        return
    meta = batch_datetime_gps([x[1] for x in xmps])
    entries, files_by_key = [], {}
    for stem, xmp in xmps:
        m = meta.get(os.path.normcase(os.path.normpath(xmp)))
        if not m:
            continue
        entries.append({"key": stem, "date": m["date"], "has_gps": m["has_gps"],
                        "cur_alt": m.get("cur_alt")})
        files_by_key[stem] = [xmp]

    day = folder_path.name
    log_paths = find_day_tracklogs(day, get_gpslogs_root())
    source = FilesSource(entries, files_by_key, day)
    TracklogGeotagDialog(
        parent, source, initial_dir=folder,
        default_tracklog_paths=log_paths or None).exec()
