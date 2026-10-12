"""Create Trip / Create Album ▸ "Check Range…" — a read-only report.

The date range you type into Create is a guess, and nothing on that page tells
you what the master already holds for it. This does, in two parts:

  * every day in the range, with the pictures, species and — the column that
    earns its place — the CAMERAS that shot them. "There are iPhone pictures in
    the master for that week but no Nikon or Olympus" is the real question, and
    a picture count on its own cannot answer it. Empty days are listed too:
    they are the actionable ones.

  * the trips and albums that already cover this range, so a second one is a
    decision rather than an accident.

REPORT ONLY. Nothing here is created or changed — narrowing the range, leaving
it, or dropping the idea is entirely the user's call.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

logger = logging.getLogger("speciesviewer")

DATE_COL, PICS_COL, JPG_COL, SPECIES_COL, CAMERA_COL, PLACE_COL = range(6)
DAY_HEADERS = ["Date", "Pictures", "With JPG", "Species", "Taken with", "Where"]

OV_KIND_COL, OV_NAME_COL, OV_RANGE_COL, OV_SHARED_COL, OV_WHERE_COL = range(5)
OV_HEADERS = ["What", "Name", "Its own date range", "Days shared", "Known from"]

_GREY = "#888888"
_FAINT = "#b4b4b4"


def _camera_text(day: dict) -> str:
    """What shot this day, in the user's words rather than the field's.

    A master picture with no camera model is (almost always) a waarneming.nl
    observation record — a name kept against a sighting, not a photograph. Left
    blank it reads as "nothing here"; said plainly it reads as "nothing of MINE
    here", which is the opposite conclusion and the true one.
    """
    if not day["pictures"]:
        return ""
    parts = list(day["cameras"])
    if day["no_camera"]:
        parts.append(f"{day['no_camera']} × no camera")
    return ", ".join(parts)


class RangeCheckDialog(QDialog):
    """Read-only. Two tables: what is already in the range, and who else claims it."""

    def __init__(self, parent, report: dict, *, subject: str = "trip"):
        super().__init__(parent)
        self.setWindowTitle(f"Check Range — {report['from']}  →  {report['to']}")
        self.resize(920, 640)
        self._report = report
        self._subject = subject          # "trip" or "album" — whose Create opened this

        layout = QVBoxLayout(self)
        layout.addWidget(self._intro())
        layout.addWidget(self._overlap_block())

        self._table = QTableWidget(0, len(DAY_HEADERS))
        self._table.setHorizontalHeaderLabels(DAY_HEADERS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)         # fill first, then enable
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table, 1)
        self._fill_days(report["days"])
        self._table.setSortingEnabled(True)

        layout.addWidget(self._note())

        buttons = QHBoxLayout()
        buttons.addStretch()
        copy_btn = QPushButton("Copy report")
        copy_btn.setToolTip("Copy the whole report as text")
        copy_btn.clicked.connect(lambda: self._copy(self._as_text(), "report"))
        buttons.addWidget(copy_btn)
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    # ── header blocks ───────────────────────────────────────────────────────
    def _intro(self) -> QLabel:
        t = self._report["totals"]
        if t["pictures"]:
            own = t["pictures"] - t["no_camera"]
            body = (f"The master already holds <b>{t['pictures']} pictures</b> in this range, "
                    f"on {t['days_with_pictures']} of its {t['span_days']} days "
                    f"({t['species']} species). {t['days_empty']} days hold nothing.")
            if t["no_camera"] and not own:
                body += ("<br><b>None of them was taken with a camera</b> — they are "
                         "observation records, not photographs. Whatever you shot in this "
                         "period is still outside the master.")
            elif t["no_camera"]:
                body += (f"<br>{own} came from a camera; the other {t['no_camera']} are "
                         "observation records rather than photographs.")
        else:
            body = ("The master holds <b>nothing at all</b> in this range — "
                    f"all {t['span_days']} days are empty.")
        lbl = QLabel(f"<p>{body}</p><p>This is a <b>report</b>: nothing is created or changed. "
                     f"Use it to decide whether to narrow the dates, keep them, or drop "
                     f"the {self._subject}.</p>")
        lbl.setWordWrap(True)
        return lbl

    def _overlap_block(self) -> QLabel | QTableWidget:
        rows = self._report["overlaps"]
        if not rows:
            lbl = QLabel("<p style='color:#2e7d32'>✔ <b>No other trip or album covers "
                         "this range.</b></p>")
            lbl.setWordWrap(True)
            return lbl

        table = QTableWidget(len(rows), len(OV_HEADERS))
        table.setHorizontalHeaderLabels(OV_HEADERS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        for i, r in enumerate(rows):
            known = ", ".join(x for x in (("on this machine" if r["on_disk"] else ""),
                                          ("on the website" if r["listed"] else "")) if x)
            cells = [{"trip": "Trip", "album": "Album"}.get(r["kind"], "Published"),
                     r["name"], f"{r['start']}  →  {r['end']}", r["overlap_days"], known]
            for col, value in enumerate(cells):
                item = QTableWidgetItem()
                if col == OV_SHARED_COL:
                    item.setData(Qt.ItemDataRole.DisplayRole, int(value))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setText(str(value))
                if col in (OV_KIND_COL, OV_NAME_COL):
                    f = QFont()
                    f.setBold(True)
                    item.setFont(f)
                if r["title"]:
                    item.setToolTip(r["title"])
                table.setItem(i, col, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        # Just tall enough for its rows — this is a warning strip above the real
        # table, not a second scrolling area competing with it for height.
        table.setMaximumHeight(min(len(rows), 5) * table.verticalHeader().defaultSectionSize()
                               + table.horizontalHeader().height() + 4)
        return table

    def _note(self) -> QLabel:
        note = ("<p style='color:#666'><b>Taken with</b> is what matters most here. "
                "<i>no camera</i> means a picture the master holds without any camera "
                "model — an observation record imported from waarneming.nl, kept for its "
                "name, not a photograph you took. A day showing only those, or showing "
                "nothing, is a day your own pictures have never reached.</p>")
        if not self._report["empty_days_listed"]:
            note += ("<p style='color:#666'>The range is long, so only days that hold "
                     "something are listed.</p>")
        lbl = QLabel(note)
        lbl.setWordWrap(True)
        return lbl

    # ── day table ───────────────────────────────────────────────────────────
    def _fill_days(self, days) -> None:
        self._table.setRowCount(len(days))
        grey, faint = QBrush(QColor(_GREY)), QBrush(QColor(_FAINT))
        for i, d in enumerate(days):
            has_camera = bool(d["cameras"])
            cells = [d["date"], d["pictures"], d["jpgs"], d["species"],
                     _camera_text(d), d["place"]]
            for col, value in enumerate(cells):
                item = QTableWidgetItem()
                # Numbers must sort as numbers, not as text ("9" > "148").
                if col in (PICS_COL, JPG_COL, SPECIES_COL):
                    item.setData(Qt.ItemDataRole.DisplayRole, int(value))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setText(str(value))
                # Three readings at a glance: a day with real photographs stands
                # out, a day of records only is greyed, an empty day is fainter
                # still. The pale rows are where new pictures would land.
                if not d["pictures"]:
                    item.setForeground(faint)
                elif not has_camera:
                    item.setForeground(grey)
                elif col == DATE_COL:
                    f = QFont()
                    f.setBold(True)
                    item.setFont(f)
                self._table.setItem(i, col, item)
        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setSectionResizeMode(
            CAMERA_COL, QHeaderView.ResizeMode.Stretch)

    # ── context menu / clipboard ────────────────────────────────────────────
    def _on_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        date = self._table.item(row, DATE_COL).text()
        menu = QMenu(self)
        act_date = QAction(f"Copy this date  ({date})", self)
        act_report = QAction("Copy the whole report", self)
        menu.addAction(act_date)
        menu.addAction(act_report)
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is act_date:
            self._copy(date, "date")
        elif chosen is act_report:
            self._copy(self._as_text(), "report")

    def _as_text(self) -> str:
        r, t = self._report, self._report["totals"]
        lines = [f"Check Range  {r['from']} - {r['to']}",
                 f"{t['pictures']} pictures ({t['no_camera']} without a camera), "
                 f"{t['species']} species, on {t['days_with_pictures']} of "
                 f"{t['span_days']} days.", ""]
        if r["overlaps"]:
            lines.append("Already covered by:")
            lines += [f"  {o['kind']:6s} {o['name']}  {o['start']} - {o['end']}  "
                      f"({o['overlap_days']} days shared)" for o in r["overlaps"]]
        else:
            lines.append("No other trip or album covers this range.")
        lines.append("")
        for d in r["days"]:
            lines.append(f"  {d['date']}  {d['pictures']:5d} pictures  "
                         f"{d['species']:4d} species  {_camera_text(d):30s}  {d['place']}")
        return "\n".join(lines)

    def _copy(self, text: str, what: str) -> None:
        QApplication.clipboard().setText(text)
        logger.info(f"Check Range: {what} copied to the clipboard.")


def show_for_range(parent, config, from_date: str, to_date: str,
                   *, subject: str = "trip", exclude: str = "") -> dict | None:
    """Run the check and show it. Returns the report (so the caller can put a
    one-line summary beside its button), or None when it could not run."""
    from engine.range_check import check_range
    from utils.config import get_trips_root, get_virttrips_root

    def _clean(v):
        return (v or "").replace('"', "").replace("'", "")

    master = _clean(config.get("master", "masterbiotadatabase", fallback=""))
    if not master:
        QMessageBox.warning(parent, "Check Range",
                            "No master database is configured, so there is nothing "
                            "to check the range against.")
        return None

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        report = check_range(master, from_date, to_date,
                             trips_root=_clean(get_trips_root()),
                             virttrip_root=_clean(get_virttrips_root()),
                             exclude=exclude)
    except Exception as e:
        logger.warning(f"Check Range {from_date} - {to_date} failed: {e}")
        QMessageBox.warning(parent, "Check Range",
                            f"Could not read the master:\n\n{e}")
        return None
    finally:
        QApplication.restoreOverrideCursor()

    RangeCheckDialog(parent, report, subject=subject).exec()
    return report


def check_range_row(dialog, date_field, subject: str, own_name=None):
    """The "Check Range…" button + its hint, as a row to drop under a date field.

    A free function rather than a method on either Create dialog: both of them
    want exactly this and neither owns it, and a shared row means the wording
    cannot drift apart between Create Trip and Create Album.
    """
    from PyQt6.QtWidgets import QHBoxLayout

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    btn = QPushButton("Check Range…")
    btn.setToolTip("Show what the master already holds for these dates, and "
                   "which trips or albums already cover them.\n"
                   "A report only — it changes nothing.")
    hint = QLabel("")
    hint.setStyleSheet(f"color: {_GREY};")
    hint.setWordWrap(True)

    def _run():
        from utils.config import _config
        start, end = date_field.get_dates()
        # own_name(): the name of the trip/album being edited, left out of
        # the overlaps — it always "overlaps" its own range.
        report = show_for_range(dialog, _config, start, end, subject=subject,
                                exclude=own_name() if own_name else "")
        hint.setText(summary_line(report) if report else "")

    btn.clicked.connect(_run)
    row.addWidget(btn)
    row.addWidget(hint, 1)
    return row


def summary_line(report: dict) -> str:
    """The one-line version, for the hint label next to the button."""
    t = report["totals"]
    if not t["pictures"]:
        bits = ["nothing in the master yet"]
    else:
        own = t["pictures"] - t["no_camera"]
        bits = [f"{t['pictures']} pictures on {t['days_with_pictures']} of "
                f"{t['span_days']} days"]
        if not own:
            bits.append("none from a camera")
        elif t["no_camera"]:
            bits.append(f"{own} from a camera")
    n = len(report["overlaps"])
    bits.append("no overlapping trip or album" if not n
                else f"⚠ overlaps {n} trip/album" + ("s" if n > 1 else ""))
    return " — ".join(bits)
