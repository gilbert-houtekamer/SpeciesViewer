"""Copy the days' PICTURES to a RAW-backup target — a spare SD card or an
external HDD.

ONE job: choose a folder and a set of days, and copy each day's folder into
it. All the thinking lives in `engine.raw_backup`; this is the picker and the
progress report. Sibling to work_backup_dialog.BackupWorkDialog (the
Metadata backup), which this deliberately does not duplicate: that one ZIPs
the culling WORK and never touches a RAW; this one copies the pictures and
knows nothing about ratings or species.

Runs on a worker thread — a day can be gigabytes, and the window must not
freeze while that copies.
"""

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from engine import raw_backup as rb
from gui import theme
from utils.config import _config, persist_config
from utils.disk_space import human_bytes
from gui.widgets import wrapped

CONFIG_SECTION = "rawbackup"
CONFIG_KEY = "location"


def raw_backup_location() -> str:
    return _config.get(CONFIG_SECTION, CONFIG_KEY, fallback="").strip()


def set_raw_backup_location(path: str):
    if not _config.has_section(CONFIG_SECTION):
        _config.add_section(CONFIG_SECTION)
    _config.set(CONFIG_SECTION, CONFIG_KEY, path.strip())
    persist_config()


class _RawBackupWorker(QObject):
    progress = pyqtSignal(int, int, str, int, int)   # file i/total, name, day i/total
    finished = pyqtSignal(dict, str)

    def __init__(self, day_dirs, dest, trip_name, include_trash, exclude_blue):
        super().__init__()
        self._days = day_dirs
        self._dest = dest
        self._trip_name = trip_name
        self._include_trash = include_trash
        self._exclude_blue = exclude_blue
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        out = {"days": len(self._days), "copied": 0, "skipped": 0, "failed": 0,
               "bytes": 0, "results": [], "errors": [], "cancelled": False}
        try:
            for di, day in enumerate(self._days, 1):
                if self._cancel:
                    out["cancelled"] = True
                    break
                res = rb.copy_day_raw(
                    day, self._dest, self._trip_name,
                    include_trash=self._include_trash,
                    exclude_blue=self._exclude_blue,
                    progress=lambda i, t, name, di=di: self.progress.emit(
                        i, t, name, di, len(self._days)),
                    should_cancel=lambda: self._cancel)
                out["copied"] += res["copied"]
                out["skipped"] += res["skipped"]
                out["failed"] += res["failed"]
                out["bytes"] += res["bytes"]
                out["errors"].extend(res["errors"])
                out["results"].append(res)
            self.finished.emit(out, "")
        except Exception as e:
            self.finished.emit(out, str(e))


class RawBackupDialog(QDialog):
    def __init__(self, parent, day_dirs, trip_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Back up pictures (RAW backup)")
        self._trip_name = trip_name
        self._day_dirs = [Path(d) for d in day_dirs]
        self._rows: list[dict] = []
        self._thread = None
        self.resize(760, 520)

        outer = QVBoxLayout(self)
        outer.addWidget(wrapped(
            "Copies each selected day's <b>picture folder</b> — RAW/HEIC, "
            "sidecars and JPGs — to a RAW-backup location such as a spare SD "
            "card or an external drive. This is separate from the Metadata "
            "backup: that one is a small safety net for your culling work; "
            "this one is the pictures themselves. A file already copied and "
            "unchanged is skipped on a repeat run."))

        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("RAW backup location:"))
        self._dest_label = QLabel(raw_backup_location() or "— not chosen —")
        self._dest_label.setWordWrap(True)
        dest_row.addWidget(self._dest_label, 1)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_dest)
        dest_row.addWidget(browse)
        outer.addLayout(dest_row)

        opts_row = QHBoxLayout()
        self._trash_box = QCheckBox("Include already-deleted pictures (.trash)")
        self._trash_box.setToolTip(
            "Also copy this day's .trash — pictures already moved out by a "
            "colour-delete. Off by default: a RAW backup normally only needs "
            "what you are keeping.")
        opts_row.addWidget(self._trash_box)
        self._blue_box = QCheckBox("Skip Blue-marked pictures (not yet deleted)")
        self._blue_box.setToolTip(
            "Blue is this app's discard-candidate colour. Off by default — "
            "they are copied like everything else, since they have not "
            "actually been deleted yet.")
        opts_row.addWidget(self._blue_box)
        opts_row.addStretch(1)
        outer.addLayout(opts_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Day", "Pictures", "Already copied"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemChanged.connect(self._on_item_changed)
        outer.addWidget(self._table, 1)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        outer.addWidget(self._summary)

        btns = QHBoxLayout()
        all_btn = QPushButton("Select all")
        all_btn.clicked.connect(lambda: self._set_all(True))
        btns.addWidget(all_btn)
        none_btn = QPushButton("Select none")
        none_btn.clicked.connect(lambda: self._set_all(False))
        btns.addWidget(none_btn)
        btns.addStretch(1)
        self._run_btn = QPushButton("Copy selected")
        self._run_btn.clicked.connect(self._run)
        btns.addWidget(self._run_btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btns.addWidget(self._cancel_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btns.addWidget(self._close_btn)
        outer.addLayout(btns)

        self._reload()

    # ── list ──────────────────────────────────────────────────────────────

    def _reload(self):
        """Rebuild from disk. Nothing is pre-ticked (unlike the Metadata
        backup) — the "Already copied" count is a plain file count, not a
        true current/stale check, so a full match does not by itself mean
        this day needs no more attention (e.g. it may have grown since). The
        count is cheap either way — one directory listing per day, the same
        one the overview column pays — so it's shown here too rather than
        making the user switch back to the overview to see it."""
        dest = raw_backup_location()
        self._rows = []
        for d in self._day_dirs:
            try:
                total = sum(1 for p in d.iterdir() if p.is_file())
            except OSError:
                total = 0
            copied = rb.count_copied(d, dest, self._trip_name) if dest else 0
            self._rows.append({"dir": d, "total": total, "copied": copied})

        self._table.blockSignals(True)
        self._table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Unchecked)
            self._table.setItem(i, 0, tick)
            self._table.setItem(i, 1, QTableWidgetItem(r["dir"].name))
            n_item = QTableWidgetItem(str(r["total"]))
            n_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 2, n_item)

            copied_item = QTableWidgetItem(
                f"{r['copied']}/{r['total']}" if dest else "—")
            copied_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if dest and r["total"] > 0:
                if r["copied"] >= r["total"]:
                    copied_item.setForeground(QColor(theme.DONE_FG))
                elif r["copied"] > 0:
                    copied_item.setForeground(QColor(theme.WARN))
            self._table.setItem(i, 3, copied_item)
        self._table.blockSignals(False)
        self._update_summary()

    def _checked(self) -> list[int]:
        return [i for i in range(self._table.rowCount())
                if self._table.item(i, 0).checkState() == Qt.CheckState.Checked]

    def _set_all(self, on: bool):
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self._table.blockSignals(True)
        for i in range(self._table.rowCount()):
            self._table.item(i, 0).setCheckState(state)
        self._table.blockSignals(False)
        self._update_summary()

    def _on_item_changed(self, item):
        if item.column() == 0:
            self._update_summary()

    def _update_summary(self):
        picked = self._checked()
        pics = sum(self._rows[i]["total"] for i in picked)
        self._summary.setText(
            f"{len(picked)} of {len(self._rows)} day(s) selected — "
            f"about {pics} picture file(s) before .trash / Blue options are applied.")
        self._run_btn.setEnabled(bool(picked) and bool(raw_backup_location()))

    # ── actions ───────────────────────────────────────────────────────────

    def _choose_dest(self):
        start = raw_backup_location() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "RAW backup location", start)
        if not chosen:
            return
        set_raw_backup_location(chosen)
        self._dest_label.setText(chosen)
        self._reload()

    def _run(self):
        picked = self._checked()
        dest = raw_backup_location()
        if not picked or not dest:
            return
        days = [self._rows[i]["dir"] for i in picked]

        self._run_btn.setEnabled(False)
        self._close_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._summary.setText("Copying…")
        QApplication.processEvents()

        self._thread = QThread()
        self._worker = _RawBackupWorker(
            days, dest, self._trip_name,
            self._trash_box.isChecked(), self._blue_box.isChecked())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_cancel_clicked(self):
        # Copying can be gigabytes — a click here should feel immediate even
        # though the worker only notices between files. Whatever already
        # copied stays; the source is never touched, so there is nothing to
        # undo.
        self._cancel_btn.setEnabled(False)
        self._summary.setText("Cancelling — finishing the current file…")
        if getattr(self, "_worker", None):
            self._worker.cancel()

    def _on_progress(self, i, total, name, day_i, day_total):
        self._summary.setText(f"Day {day_i}/{day_total} — file {i}/{total}: {name}…")

    def _on_finished(self, out, error):
        self._thread.quit()
        self._thread.wait()
        self._run_btn.setEnabled(True)
        self._close_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        if error:
            self._summary.setText(f"❌ {error}")
            QMessageBox.warning(self, "Back up pictures",
                                f"The RAW backup failed:\n{error}")
            self._reload()
            return
        msg = (f"Copied {out['copied']} file(s), {human_bytes(out['bytes'])} "
               f"({out['skipped']} already there, unchanged).")
        if out.get("cancelled"):
            msg += " Cancelled — the rest were left alone."
        self._reload()
        if out.get("failed"):
            detail = "\n".join(out["errors"][:5])
            self._summary.setText(f"⚠️ {msg}")
            QMessageBox.warning(
                self, "Back up pictures",
                f"{msg}\n\n{out['failed']} file(s) could not be copied:\n\n{detail}"
                f"\n\nThey stay uncopied, so the next run retries them.")
        else:
            self._summary.setText(f"✅ {msg}")
