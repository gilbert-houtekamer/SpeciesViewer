"""Suggest — which pictures of the species in view could go (read-only).

ONE JOB: for one master species, show what it is (pictures, days, places, your
keepers, the candidates) and what the suggestion would do with the settings you
choose — overall and per day — so you can judge the cut-offs. Opened from the
master Species Viewer's "AI…" button. Plan: docs/plans/picture_score_plan.md.

With a `grid` (the Species Viewer that opened it) the suggestion is also drawn
over its cards — a bin on what could go, a green frame on what is kept — and a
day picked in the table moves the grid to that day's pictures. Closing the
dialog takes the bins away again.

INVARIANT: this dialog changes nothing — not a colour, not a file, not a list;
the bins on the grid's cards are a view.
The measuring (engine.picture_score) runs on a background thread and is cached
under app-data; the choosing (engine.redundancy) is recomputed instantly when a
setting changes.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from engine import redundancy as rd
from gui import theme as _th
from gui.widgets import wrapped

logger = logging.getLogger("speciesviewer")


class _ScoreThread(QThread):
    # `object`, not `list`: a list signal hands the slot a CONVERTED COPY (dicts
    # become new dicts), so the measurements added later never reached the dialog.
    loaded = pyqtSignal(object)          # the pictures, before measuring
    progress = pyqtSignal(int, int)
    scored = pyqtSignal(int)             # how many had to be decoded
    failed = pyqtSignal(str)

    def __init__(self, rows, master_root, sci, parent=None):
        """`rows`: the species' master picture rows, read on the GUI thread (a
        MasterDB connection belongs to the thread that opened it)."""
        super().__init__(parent)
        self._rows, self._root, self._sci = rows, master_root, sci
        self._stop = False
        self.pics: list[dict] = []

    def stop(self):
        self._stop = True

    def run(self):
        try:
            from engine.picture_score import Cache, score_pictures
            self.pics = rd.from_master_rows(self._rows, self._root)
            self.loaded.emit(self.pics)
            if self._stop:
                return
            cache = Cache()
            try:
                n = score_pictures(self.pics, cache, progress=self.progress.emit,
                                   cancelled=lambda: self._stop)
            finally:
                cache.close()
            if not self._stop:
                self.scored.emit(n)
        except Exception as e:
            logger.warning(f"Suggest: scoring {self._sci} failed: {e}", exc_info=True)
            self.failed.emit(str(e))


def _date(t) -> str:
    return f"{t:%d %b %Y}" if t else "?"


class SuggestDialog(QDialog):
    def __init__(self, parent, master_db, master_root, sci: str, name: str, *, grid=None):
        super().__init__(parent)
        self._grid, self._sci = grid, sci
        self._filling = False
        self.setWindowTitle(f"Suggest — {name}")
        self.resize(900, 720)
        self._pics: list[dict] = []
        self._scored = False
        self._days = 0

        lay = QVBoxLayout(self)
        lay.addWidget(wrapped(
            f"<b>Which pictures of {name} could go?</b><br>"
            "A suggestion only — nothing is changed here. Pictures you kept (stars, Red, "
            "Yellow or a JPG) are never suggested, and neither are the ones you already "
            "coloured Blue or Purple. Only pictures without stars that are Green or have "
            "no colour can be suggested."))

        self._stats = wrapped("Reading the pictures …")
        lay.addWidget(self._stats)

        # ── settings ────────────────────────────────────────────────────────
        form = QFormLayout()
        self._per_day = QSpinBox()
        self._per_day.setRange(0, 200)
        self._per_day_note = QLabel("")
        self._per_day_note.setStyleSheet(f"color:{_th.INK_FAINT}")
        row = QHBoxLayout()
        row.addWidget(self._per_day)
        row.addWidget(self._per_day_note, 1)
        form.addRow("Keep per day, besides yours:", row)
        self._per_pose = QSpinBox()
        self._per_pose.setRange(1, 20)
        form.addRow("Keep per pose:", self._per_pose)
        self._gap = QSpinBox()
        self._gap.setRange(1, 3600)
        self._gap.setSuffix(" s")
        form.addRow("A new pose after a pause of:", self._gap)
        self._change = QSpinBox()
        self._change.setRange(1, 100)
        self._change.setSuffix(" %")
        form.addRow("… or when the picture changes by:", self._change)
        self._soft = QSpinBox()
        self._soft.setRange(0, 100)
        self._soft.setSuffix(" %")
        form.addRow("Never keep a frame below this share\nof its pose's sharpest:", self._soft)
        self._cap = QSpinBox()
        self._cap.setRange(0, 10000)
        self._cap.setSpecialValueText("no limit")
        form.addRow("Limit for the whole species:", self._cap)
        lay.addLayout(form)
        reset = QPushButton("Defaults")
        reset.setToolTip("Back to the settings that follow from how often you saw the species")
        reset.clicked.connect(self._defaults)
        r2 = QHBoxLayout()
        r2.addWidget(reset)
        r2.addStretch(1)
        lay.addLayout(r2)

        # ── progress + result ──────────────────────────────────────────────
        self._bar = QProgressBar()
        self._bar.setFormat("Measuring sharpness %v / %m")
        lay.addWidget(self._bar)
        self._result = wrapped("")
        lay.addWidget(self._result)
        self._note = wrapped("")
        self._note.setStyleSheet(f"color:{_th.INK_FAINT}")
        lay.addWidget(self._note)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["Day", "Place", "Pictures", "Yours", "Keep", "Suggest Purple", "Frees"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        if grid is not None:              # mouse or arrow keys: the grid follows
            self._table.setToolTip("Pick a day to see its pictures in the viewer")
            self._table.itemSelectionChanged.connect(self._on_day_picked)
        lay.addWidget(self._table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        lay.addWidget(buttons)

        for w in (self._per_day, self._per_pose, self._gap, self._change, self._soft,
                  self._cap):
            w.valueChanged.connect(self._recompute)
        self._set_settings(rd.Settings())

        rows = master_db.pictures_for_species_and_subspecies(sci)
        self._thread = _ScoreThread(rows, master_root, sci, self)
        self._thread.loaded.connect(self._on_loaded)
        self._thread.progress.connect(self._on_progress)
        self._thread.scored.connect(self._on_scored)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    # ── settings ────────────────────────────────────────────────────────────

    def _set_settings(self, s: rd.Settings):
        self._quiet = True
        self._per_day.setValue(s.per_day)
        self._per_pose.setValue(s.per_pose)
        self._gap.setValue(int(s.gap))
        self._change.setValue(round(s.change * 100))
        self._soft.setValue(round(s.soft * 100))
        self._cap.setValue(s.cap)
        self._quiet = False
        self._recompute()

    def _settings(self) -> rd.Settings:
        return rd.Settings(per_day=self._per_day.value(), per_pose=self._per_pose.value(),
                           gap=float(self._gap.value()), change=self._change.value() / 100,
                           soft=self._soft.value() / 100, cap=self._cap.value())

    def _defaults(self):
        self._set_settings(rd.default_settings(self._pics))

    # ── the thread ──────────────────────────────────────────────────────────

    def _on_loaded(self, pics):
        self._pics = pics
        st = rd.statistics(pics)
        self._days = st["days"]
        if not pics:
            self._stats.setText("No pictures of this species were found on disk.")
            self._bar.hide()
            return
        places = (f" at {st['places']} place{'s' if st['places'] != 1 else ''}"
                  if st["places"] else "")
        span = (f" ({_date(st['first'])} – {_date(st['last'])})"
                if st["first"] and st["first"].date() != st["last"].date()
                else f" ({_date(st['first'])})" if st["first"] else "")
        self._stats.setText(
            f"<b>{st['pictures']}</b> pictures on <b>{st['days']}</b> "
            f"day{'s' if st['days'] != 1 else ''}{places}{span}, {rd.gb(st['bytes'])}.<br>"
            f"Kept by you: <b>{st['keepers']}</b> — stars {st['stars']}, Red {st['red']}, "
            f"Yellow {st['yellow']}, with a JPG {st['jpg']} (a picture can be several).<br>"
            f"Already Blue or Purple: {st['decided']}. "
            f"Could be suggested: <b>{st['candidates']}</b> "
            f"({rd.gb(st['candidate_bytes'])}).")
        d = rd.per_day_for(self._days)
        self._per_day_note.setText(
            f"default for {self._days} day{'s' if self._days != 1 else ''}: {d}")
        self._per_day.setToolTip(
            "Pictures kept per day on top of the ones you kept yourself. The default "
            "is higher for a species you saw on few days: 20 for one day, 12 for 4, "
            "8 for 9, 5 for 25, 3 for 65 or more.")
        self._bar.setRange(0, max(1, len(pics)))
        self._defaults()

    def _on_progress(self, i, n):
        self._bar.setRange(0, n)
        self._bar.setValue(i)

    def _on_scored(self, decoded):
        self._pics = self._thread.pics       # the very dicts that were measured
        self._scored = True
        self._bar.hide()
        self._recompute()

    def _on_failed(self, err):
        self._bar.hide()
        self._result.setText(f"Measuring failed: {err}")

    # ── the suggestion ──────────────────────────────────────────────────────

    def _recompute(self, *_):
        if getattr(self, "_quiet", False) or not self._scored or not self._pics:
            if self._pics and not self._scored:
                self._result.setText("Measuring how sharp each picture is — the first "
                                     "time for a species this takes a while; after that it "
                                     "is instant.")
            return
        groups = rd.select(self._pics, self._settings())
        out = rd.outcome(self._pics, groups)
        self._result.setText(
            f"{out['poses']} poses. Keep <b>{out['keep']}</b> besides yours; "
            f"suggest Purple <b>{out['purple']}</b>, which frees "
            f"<b>{rd.gb(out['purple_bytes'])}</b>.")
        self._show_on_grid()
        rows = rd.per_day_rows(self._pics)
        self._day_rows = rows
        self._filling = True
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [f"{r['day']:%Y-%m-%d}" if r["day"] else "no date", r["places"],
                    r["pictures"], r["keepers"], r["keep"], r["purple"],
                    rd.gb(r["purple_bytes"]) if r["purple"] else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c >= 2:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(i, c, it)
        self._filling = False

    # ── the grid ────────────────────────────────────────────────────────────

    def _show_on_grid(self):
        if self._grid is None:
            return
        by_upn = {p["upn"]: p["verdict"] for p in self._pics
                  if p.get("verdict") in (rd.PURPLE, rd.KEEP)}
        try:
            hidden = self._grid.show_suggestions(self._sci, by_upn)
        except RuntimeError:              # the viewer was closed
            self._grid = None
            return
        self._note.setText(
            f"{hidden} of the pictures that could go are hidden in the viewer by a filter "
            f"— “JPG only” hides every one of them; turn it off to see the bins."
            if hidden else "In the viewer: a bin on each picture that could go, a green "
                           "frame on the ones kept. Pick a day below to go there.")

    def _on_day_picked(self):
        if self._filling or self._grid is None:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(getattr(self, "_day_rows", [])):
            return
        day = self._day_rows[rows[0].row()]["day"]
        pics = [p for p in self._pics if rd.day_of(p) == day]
        # To the first picture that could go — what you want to look at — or,
        # on a day with none, to the day's first picture.
        pics = [p for p in pics if p.get("verdict") == rd.PURPLE] or pics
        try:
            shown, hidden = self._grid.jump_to_upns([p["upn"] for p in pics])
        except RuntimeError:
            self._grid = None
            return
        if not shown:
            self._note.setText("The pictures of that day are hidden in the viewer by a "
                               "filter (JPG only, a colour or a camera).")

    def _clear_grid(self):
        if self._grid is not None:
            try:
                self._grid.clear_suggestions()
            except RuntimeError:
                pass
            self._grid = None

    def done(self, r):                    # any way the dialog closes: the bins go
        self._clear_grid()
        super().done(r)

    def _stop_thread(self):
        if self._thread.isRunning():
            self._thread.stop()          # the pictures being read finish, then it ends
            self._thread.wait(30000)

    def closeEvent(self, e):
        self._stop_thread()
        self._clear_grid()
        super().closeEvent(e)

    def reject(self):                    # Esc / Close do not pass through closeEvent
        self._stop_thread()
        super().reject()
