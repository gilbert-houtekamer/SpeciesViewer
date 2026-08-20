"""Back up the days' work to a spare card.

ONE job: choose a folder and a set of days, and write one ZIP per day into it.
All the thinking lives in `engine.work_backup`; this is the picker and the
progress report.

Invariant: the day list is rebuilt from disk every time it is shown, and each
day's state comes from comparing the folder against the manifest inside its
existing ZIP. Nothing is remembered between openings, so the screen cannot
disagree with the card.

Runs on a worker thread: the metadata alone is fast (a real 3-day trip took
0.1 s), but once a day's JPGs are in the ZIP it is tens of megabytes onto a
card, and the window must not freeze while that happens.
"""

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from engine import work_backup as wb
from utils.config import _config, persist_config
from utils.disk_space import check_free_space, human_bytes

CONFIG_SECTION = "backup"
CONFIG_KEY = "location"

_STATE_TEXT = {
    "never":   "not backed up yet",
    "stale":   "changed since the backup",
    "current": "backed up",
    "empty":   "nothing to back up",
}


def backup_location() -> str:
    return _config.get(CONFIG_SECTION, CONFIG_KEY, fallback="").strip()


def set_backup_location(path: str):
    if not _config.has_section(CONFIG_SECTION):
        _config.add_section(CONFIG_SECTION)
    _config.set(CONFIG_SECTION, CONFIG_KEY, path.strip())
    persist_config()


class _BackupWorker(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict, str)

    def __init__(self, day_dirs, dest, trip_root=""):
        super().__init__()
        self._days = day_dirs
        self._dest = dest
        self._trip_root = trip_root
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            out = wb.backup_days(
                self._days, self._dest, skip_unchanged=False,
                progress=lambda d, t, n: self.progress.emit(d, t, n),
                should_cancel=lambda: self._cancel)
            trip_name = self._days[0].parent.name if self._days else ""
            trip_folder = Path(self._dest) / trip_name if trip_name else Path(self._dest)

            # The day ZIPs are not a restore on their own: they hold every
            # culling decision and no lifer verdicts. This is the other half —
            # trip.ini, the species collection and the lifer baseline — and it
            # is kilobytes, so it is rewritten every run and can never be out of
            # step with the days beside it.
            if self._trip_root:
                # Refuse rather than write the WRONG trip's lifer baseline into
                # this folder — a mistake that would only surface at restore.
                ok, why = wb.trip_matches_days(self._trip_root, self._days)
                if not ok:
                    out["identity"] = {"written": False, "reason": why, "path": None}
                else:
                    # Fill in the filename prefix for trips read before it was
                    # recorded — otherwise the finished trips, the ones most
                    # likely to need restoring, would never carry one.
                    wb.ensure_trip_prefix(self._trip_root, self._days)
                    wb.ensure_trip_tz_offset(self._trip_root)
                    out["identity"] = wb.backup_trip_identity(
                        self._trip_root, trip_folder,
                        master_root=_config.get("master", "masterbiotadatabase", fallback=""),
                        master_web_root=_config.get("master", "masterbiotawebroot", fallback=""))
            out["tracklogs"] = wb.backup_tracklogs(
                _config.get("gpslogs", "rootdirectory", fallback=""), trip_folder)

            # The GUI cache travels too: not needed by a restore (the manifest
            # already carries the prefix) but it turns a reconstruction into a
            # comparison if one is ever needed. svconfig.ini is deliberately
            # left behind — it holds the SFTP password.
            out["settings"] = wb.copy_settings_snapshot(trip_folder)
            self.finished.emit(out, "")
        except Exception as e:
            self.finished.emit({}, str(e))


class BackupWorkDialog(QDialog):
    def __init__(self, parent, day_dirs, preselect=None, trip_root=""):
        super().__init__(parent)
        self.setWindowTitle("Back up the day's work")
        self._trip_root = str(trip_root or "")
        self._day_dirs = [Path(d) for d in day_dirs]
        self._preselect = str(preselect) if preselect else ""
        self._rows: list[dict] = []
        self._thread = None
        self.resize(760, 480)

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "The SD-cards keep the pictures. This backs up the <b>work</b> — culling "
            "colours, species, locations, crops, blog text and the rendered JPGs — "
            "onto the <b>work card</b>, as one ZIP per day."))

        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Work card / folder:"))
        self._dest_label = QLabel(backup_location() or "— not chosen —")
        self._dest_label.setWordWrap(True)
        dest_row.addWidget(self._dest_label, 1)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_dest)
        dest_row.addWidget(browse)
        outer.addLayout(dest_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Day", "Files", "State"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
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
        self._run_btn = QPushButton("Back up selected")
        self._run_btn.clicked.connect(self._run)
        btns.addWidget(self._run_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btns.addWidget(self._close_btn)
        outer.addLayout(btns)

        self._reload()

    # ── list ──────────────────────────────────────────────────────────────

    def _reload(self):
        """Rebuild from disk. Days that need a backup are pre-ticked, so the
        common case — 'back up whatever changed today' — is one click."""
        dest = backup_location()
        self._rows = []
        for d in self._day_dirs:
            if dest:
                st = wb.day_status(d, dest)
            else:
                scan = wb.scan_day(d)
                st = {"state": "never" if scan["payload"] else "empty",
                      "reason": "no backup folder chosen yet", "scan": scan,
                      "zip": None, "changed": len(scan["payload"])}
            st["dir"] = d
            self._rows.append(st)

        self._table.blockSignals(True)
        self._table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            n_files = len(r["scan"]["payload"])
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            want = r["state"] in ("never", "stale")
            if self._preselect:
                want = str(r["dir"]) == self._preselect and n_files > 0
            tick.setCheckState(Qt.CheckState.Checked if want else Qt.CheckState.Unchecked)
            self._table.setItem(i, 0, tick)
            self._table.setItem(i, 1, QTableWidgetItem(r["dir"].name))
            self._table.setItem(i, 2, QTableWidgetItem(str(n_files)))
            state = _STATE_TEXT.get(r["state"], r["state"])
            item = QTableWidgetItem(f"{state} — {r['reason']}" if r["reason"] else state)
            self._table.setItem(i, 3, item)
        self._table.blockSignals(False)
        self._preselect = ""      # only steers the first build
        self._update_summary()

    def _checked(self) -> list[int]:
        return [i for i in range(self._table.rowCount())
                if self._table.item(i, 0).checkState() == Qt.CheckState.Checked]

    def _set_all(self, on: bool):
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self._table.blockSignals(True)
        for i in range(self._table.rowCount()):
            if self._rows[i]["state"] != "empty" or not on:
                self._table.item(i, 0).setCheckState(state)
        self._table.blockSignals(False)
        self._update_summary()

    def _on_item_changed(self, item):
        if item.column() == 0:
            self._update_summary()

    def _selected_bytes(self, picked) -> int:
        total = 0
        for i in picked:
            for p in self._rows[i]["scan"]["payload"]:
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def _update_summary(self):
        picked = self._checked()
        files = sum(len(self._rows[i]["scan"]["payload"]) for i in picked)
        size = self._selected_bytes(picked)
        self._summary.setText(
            f"{len(picked)} of {len(self._rows)} day(s) selected — "
            f"{files} file(s), {human_bytes(size)} before compression.")
        self._run_btn.setEnabled(bool(picked) and bool(backup_location()))

    # ── actions ───────────────────────────────────────────────────────────

    def _choose_dest(self):
        start = backup_location() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Work card or folder", start)
        if not chosen:
            return
        set_backup_location(chosen)
        self._dest_label.setText(chosen)
        self._reload()

    def _run(self):
        picked = self._checked()
        dest = backup_location()
        if not picked or not dest:
            return
        days = [self._rows[i]["dir"] for i in picked]
        need = self._selected_bytes(picked)
        # Same pre-flight the card read gets: a card that cannot hold the backup
        # should say so before writing, not fill up halfway through.
        problem = check_free_space(dest, need, what=f"Backing up {len(days)} day(s)")
        if problem:
            QMessageBox.warning(self, "Back up the day's work", problem)
            return

        self._run_btn.setEnabled(False)
        self._close_btn.setEnabled(False)
        self._summary.setText("Backing up…")
        QApplication.processEvents()

        self._thread = QThread()
        self._worker = _BackupWorker(days, dest, trip_root=self._trip_root)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(
            lambda d, t, n: self._summary.setText(f"Backing up {d}/{t} — {n}…"))
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_finished(self, out, error):
        self._thread.quit()
        self._thread.wait()
        self._run_btn.setEnabled(True)
        self._close_btn.setEnabled(True)
        if error:
            self._summary.setText(f"❌ {error}")
            QMessageBox.warning(self, "Back up the day's work",
                                f"The backup failed:\n{error}")
            self._reload()
            return
        msg = (f"Backed up {out['written']} day(s) — {out['files']} file(s), "
               f"{human_bytes(out['bytes'])}.")
        if out.get("skipped"):
            msg += f" {out['skipped']} unchanged."
        if out.get("empty"):
            msg += f" {out['empty']} had nothing to back up."
        ident = out.get("identity") or {}
        if ident.get("written"):
            msg += " Trip identity (species list and lifer baseline) saved."
        elif self._trip_root:
            # Worth saying out loud: without it the backup restores the culling
            # but not the lifer verdicts.
            msg += f" ⚠️ Trip identity NOT saved — {ident.get('reason', 'no trip')}."
        tl = out.get("tracklogs") or {}
        if tl.get("copied"):
            msg += f" {tl['copied']} tracklog(s) copied."
        self._reload()
        if out.get("failed") or out.get("errors"):
            detail = "\n".join(out.get("errors", [])[:5])
            self._summary.setText(f"⚠️ {msg}")
            QMessageBox.warning(
                self, "Back up the day's work",
                f"{msg}\n\nSome files could not be read and were left out:\n\n"
                f"{detail}\n\nThose days still show as not backed up, so the next "
                f"run retries them.")
        else:
            self._summary.setText(f"✅ {msg}")
