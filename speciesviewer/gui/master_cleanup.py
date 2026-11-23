"""Master ▸ Clean Up the Master… — the AI… suggestion for every species at once.

ONE JOB: start one of engine.master_cleanup's all-species runs (mark the bins
Purple / delete Purple / undo the marking) on a worker thread, show one line
per species as it goes, and say where the CSV report is. The window decides
nothing itself: what is marked or deleted is the engine's, with its rules.

INVARIANTS
- Nothing runs without a question first that says what will happen.
- The worker opens its OWN master database (SQLite connections do not cross
  threads); the window only shows what it reports.
- Stop finishes the picture in hand and ends; what is done stays done, and is
  in the report and (for a marking) in the undo record.
- On a computer where the master may not be changed, the three commands are off.
"""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from gui.widgets import wrapped
from utils.config import _config

logger = logging.getLogger("speciesviewer")

MARK, DELETE, UNDO = "mark", "delete", "undo"


def master_writable() -> bool:
    return _config.get("master", "readonly", fallback="yes").strip().lower() == "no"


class _Run(QThread):
    line = pyqtSignal(object)            # one species' dict (object: no copy)
    status = pyqtSignal(str)
    finished_run = pyqtSignal(object, str)   # totals, report path
    failed = pyqtSignal(str)

    def __init__(self, master_root: str, kind: str, parent=None):
        super().__init__(parent)
        self._root = master_root
        self._kind = kind
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from db.master_db import MasterDB
        from engine import master_cleanup as mc
        db = report = None
        try:
            self.status.emit("Opening the master…")
            db = MasterDB(self._root)
            db.ensure_fresh()
            names = {}
            for r in db.all_species_rows():
                sci = (r.get("WetSoortNaam") or r.get("sci") or "").strip()
                if sci:
                    names[sci.lower()] = (r.get("nl") or r.get("en") or "").strip()
            by_sci, floors = ({}, {}) if self._kind == UNDO else mc.master_species(db)
            fields = (mc.CSV_FIELDS if self._kind != UNDO else
                      ["species", "purple", "done", "failed", "changed since"])
            report = mc.CsvReport(mc.csv_path(self._kind), ["name"] + fields)
            total = len(by_sci)
            seen = [0]

            def on_line(d):
                seen[0] += 1
                d["name"] = names.get(d["species"].lower(), "")
                report(d)
                self.line.emit(d)
                if total:
                    self.status.emit(f"Species {seen[0]} of {total}…")

            stopped = lambda: self._stop                       # noqa: E731
            if self._kind == MARK:
                tot = mc.mark_all(db, self._root, by_sci, floors,
                                  report=on_line, cancelled=stopped)
            elif self._kind == DELETE:
                tot = mc.delete_all_purple(db, self._root, by_sci,
                                           report=on_line, cancelled=stopped)
            else:
                tot = mc.undo_last_run(db, self._root, report=on_line, cancelled=stopped)
            tot["stopped"] = self._stop
            self.finished_run.emit(tot, str(report.path))
        except Exception as e:
            logger.error(f"Clean up the master ({self._kind}) failed: {e}", exc_info=True)
            self.failed.emit(str(e))
        finally:
            if report is not None:
                report.close()
            if db is not None:
                db.close()


def _who(d) -> str:
    return f"{d['name']} ({d['species']})" if d.get("name") else d["species"]


def _line_text(kind: str, d: dict) -> str | None:
    """The log line for one species, or None when there is nothing to say."""
    fail = f", {d['failed']} failed" if d.get("failed") else ""
    if kind == MARK:
        if not d["purple"] and not d.get("failed"):
            return None
        return (f"{_who(d)}: {d['done']} of {d['candidates']} candidates Purple "
                f"({d['GB']:.1f} GB){fail}")
    if kind == DELETE:
        jpg = f", {d['with JPG left']} with a JPG left" if d["with JPG left"] else ""
        return f"{_who(d)}: {d['done']} to the trash ({d['GB']:.1f} GB){jpg}{fail}"
    since = f", {d['changed since']} you changed since" if d["changed since"] else ""
    return f"{_who(d)}: {d['done']} colours back{since}{fail}"


class MasterCleanupWindow(QDialog):
    def __init__(self, parent, master_root: str):
        super().__init__(parent)
        self.setWindowTitle("Clean Up the Master")
        self._root = master_root
        self._run: _Run | None = None
        self._kind = ""
        self._t0 = 0.0
        lay = QVBoxLayout(self)
        lay.addWidget(wrapped(
            "The AI… suggestion of the master Species Viewer, for every species at "
            "once. Marking only sets the colour Purple; you can look at any species "
            "afterwards, in its viewer, when it suits you. Deleting moves pictures to "
            "the master trash; Master Trash… makes that final. Close the species "
            "viewers first: they show the colours from when they were opened."))

        grid = QGridLayout()
        self._buttons = {}
        rows = [
            (MARK, "Mark the bins Purple…",
             "Each species: the pictures the suggestion would bin (no stars above the "
             "day's usual, Green or no colour, no JPG) become Purple."),
            (DELETE, "Delete Purple…",
             "Every Purple picture without a JPG goes to the master trash. Pictures "
             "with a JPG stay."),
            (UNDO, "Undo the marking…",
             "Puts back the colours of the last marking — only on pictures that are "
             "still Purple."),
        ]
        for i, (kind, text, tip) in enumerate(rows):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, k=kind: self._ask(k))
            grid.addWidget(b, i, 0)
            grid.addWidget(wrapped(tip), i, 1)
            self._buttons[kind] = b
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        self._status = QLabel("")
        lay.addWidget(self._status)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(260)
        lay.addWidget(self._log, 1)

        box = QDialogButtonBox()
        self._stop_btn = box.addButton("Stop", QDialogButtonBox.ButtonRole.ActionRole)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        close = box.addButton(QDialogButtonBox.StandardButton.Close)
        close.clicked.connect(self.reject)
        lay.addWidget(box)
        self.resize(760, 560)
        self._refresh()

    # ── buttons ─────────────────────────────────────────────────────────────

    def _refresh(self):
        from engine import purple_marks
        busy = self._run is not None and self._run.isRunning()
        ok = master_writable() and not busy
        for b in self._buttons.values():
            b.setEnabled(ok)
        _run, batches = purple_marks.last_run(self._root)
        n = sum(len(b["before"]) for b in batches)
        self._buttons[UNDO].setEnabled(ok and n > 0)
        self._buttons[UNDO].setText(f"Undo the marking ({n})…" if n else "Undo the marking…")
        self._stop_btn.setEnabled(busy)
        if not master_writable():
            self._status.setText("The master may not be changed on this computer.")

    def _ask(self, kind: str):
        if kind == MARK:
            text = ("Mark the bins Purple in every species of the master?\n\n"
                    "Nothing is deleted. The first run can take an hour or more; you "
                    "can stop at any time — what is marked stays marked, and \"Undo "
                    "the marking\" puts back each picture's own colour.")
        elif kind == DELETE:
            free, jpg = self._purple_now()
            if free is None:
                return
            if not free:
                QMessageBox.information(self, "Delete Purple",
                                        "There is no Purple picture without a JPG.")
                return
            text = (f"Move {free} Purple pictures to the master trash?"
                    + (f"\n\n{jpg} Purple pictures with a JPG stay where they are."
                       if jpg else "")
                    + "\n\nThey can be put back from Master Trash… until you empty it.")
        else:
            text = ("Put back the colours of the last marking?\n\nA picture you "
                    "changed since then (or deleted) is left as it is.")
        if QMessageBox.question(
                self, "Clean Up the Master", text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._start(kind)

    def _purple_now(self):
        """(Purple without a JPG, Purple with one), counted on a short-lived
        connection of this thread; (None, None) when the master cannot be read."""
        from db.master_db import MasterDB
        from engine import master_cleanup as mc
        db = None
        try:
            db = MasterDB(self._root)
            db.ensure_fresh()
            by_sci, _floors = mc.master_species(db)
            return mc.purple_count(by_sci)
        except Exception as e:
            QMessageBox.warning(self, "Clean Up the Master",
                                f"The master could not be read:\n{e}")
            return None, None
        finally:
            if db is not None:
                db.close()

    # ── the run ─────────────────────────────────────────────────────────────

    def _start(self, kind: str):
        self._kind = kind
        self._t0 = time.time()
        self._log.appendPlainText(
            {MARK: "── Marking the bins Purple ──", DELETE: "── Deleting Purple ──",
             UNDO: "── Undoing the marking ──"}[kind])
        self._run = _Run(self._root, kind, self)
        self._run.line.connect(self._on_line)
        self._run.status.connect(self._status.setText)
        self._run.finished_run.connect(self._on_done)
        self._run.failed.connect(self._on_failed)
        self._run.finished.connect(self._refresh)
        self._run.start()
        self._refresh()

    def _on_line(self, d):
        text = _line_text(self._kind, d)
        if text:
            self._log.appendPlainText(text)

    def _on_done(self, tot, path):
        mins = (time.time() - self._t0) / 60
        stopped = " (stopped)" if tot.get("stopped") else ""
        if self._kind == MARK:
            s = (f"{tot.get('species', 0)} species: {tot.get('done', 0)} pictures Purple "
                 f"({tot.get('GB', 0):.0f} GB) of {tot.get('candidates', 0)} candidates")
        elif self._kind == DELETE:
            s = (f"{tot.get('done', 0)} pictures to the master trash "
                 f"({tot.get('GB', 0):.0f} GB); {tot.get('with JPG left', 0)} with a JPG stayed")
        else:
            s = f"{tot.get('done', 0)} pictures have their colour back"
        if tot.get("failed"):
            s += f"; {tot['failed']} failed (see the log)"
        self._log.appendPlainText(f"{s}{stopped} — {mins:.0f} min.\nReport: {path}\n")
        self._status.setText(f"Done{stopped}.")

    def _on_failed(self, msg):
        self._log.appendPlainText(f"Stopped by an error: {msg}\n")
        self._status.setText("Stopped by an error.")

    def _on_stop(self):
        if self._run is not None:
            self._run.stop()
            self._status.setText("Stopping after the picture in hand…")

    def _may_close(self) -> bool:
        """True when nothing runs, or the run was stopped on your say."""
        if self._run is None or not self._run.isRunning():
            return True
        if QMessageBox.question(
                self, "Clean Up the Master",
                "A run is still going. Stop it and close?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return False
        self._run.stop()
        self._run.wait(60000)
        return True

    def closeEvent(self, e):
        if not self._may_close():
            e.ignore()
            return
        super().closeEvent(e)

    def reject(self):                    # Esc / Close do not pass through closeEvent
        if self._may_close():
            super().reject()
