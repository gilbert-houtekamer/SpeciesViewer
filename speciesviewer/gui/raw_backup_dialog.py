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

Each run remembers what it did per day (engine.raw_backup.record_backup_run)
and writes a plain-text summary to metadata/reports/RAW backup report.txt —
see format_raw_backup_status for how that turns into the Compact/Full/
Incomplete status shown here and in the trip overview.
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
from utils.keep_awake import prevent_sleep
from gui.widgets import wrapped

CONFIG_SECTION = "rawbackup"
CONFIG_KEY = "location"

# Shared with gui.trip_overview's RAW backup column, so the picker and the
# overview always say the same thing about the same day. Word choice over
# numbers on purpose — see engine.raw_backup.backup_status: the old "X/Y"
# file count never landed on an exact match once Blue-skip or .trash were in
# play, even on a day that was genuinely fully handled, which is exactly what
# was confusing about it.
_STATUS_LABELS = {"not_copied": "Not copied", "compact": "Compact",
                  "full": "Full", "incomplete": "Incomplete", "legacy": "Copied",
                  "missing": "Missing"}


def format_raw_backup_status(status: str, rec: dict | None, current_total: int) -> tuple[str, str, str]:
    """(label, tooltip, colour_key) for one day's remembered RAW-backup
    status. colour_key is 'done' | 'warn' | 'faint' — the caller maps it to
    its own theme constant (QColor isn't available outside a GUI context)."""
    label = _STATUS_LABELS[status]
    if status == "not_copied":
        return (label, "This day has never been copied to the RAW backup "
                       "location. Click to copy it.", "warn")
    if status == "legacy":
        return (label, "Files are at the RAW backup location from before this "
                       "app tracked backup details (or a manual copy). Click "
                       "to copy again and start tracking its status.", "done")
    if status == "missing":
        was = rec.get("copied", 0) if rec else 0
        return (label, f"This day WAS backed up ({was} file(s) recorded), but "
                       f"nothing is at the destination now — deleted from the "
                       f"drive? Click to copy it again.", "warn")
    if status == "incomplete":
        was = rec.get("folder_total", "?") if rec else "?"
        return (label, f"This day has changed since the last backup ({was} → "
                       f"{current_total} file(s)). Click to back up what's "
                       f"missing.", "warn")
    if status == "compact":
        return (label, f"Backed up (compact): {rec.get('copied', 0)} file(s) "
                       f"copied last run, {rec.get('excluded_blue', 0)} "
                       f"Blue-marked skipped. Kept pictures only — click to "
                       f"add Blue/.trash too, or to check for new pictures.",
               "done")
    # "full"
    return (label, f"Backed up (full): {rec.get('copied', 0)} file(s) copied "
                   f"last run, including Blue and/or already-deleted "
                   f"pictures. Click to check again.", "done")

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

    def __init__(self, day_dirs, dest, trip_name, trip_root, include_trash, exclude_blue):
        super().__init__()
        self._days = day_dirs
        self._dest = dest
        self._trip_name = trip_name
        self._trip_root = trip_root
        self._include_trash = include_trash
        self._exclude_blue = exclude_blue
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        out = {"days": len(self._days), "copied": 0, "skipped": 0, "failed": 0,
               "bytes": 0, "results": [], "errors": [], "cancelled": False,
               "report_path": ""}
        # A RAW backup to a slow SD card can run for minutes with no keyboard/
        # mouse activity — exactly what Windows' own idle-sleep timer watches
        # for, and it has no way to know a background thread is still copying.
        # Without this, sleep can cut the copy off mid-file.
        with prevent_sleep():
            try:
                for di, day in enumerate(self._days, 1):
                    if self._cancel:
                        out["cancelled"] = True
                        break
                    res = rb.copy_day_raw(
                        day, self._dest, self._trip_name,
                        include_trash=self._include_trash,
                        exclude_blue=self._exclude_blue,
                        trip_root=self._trip_root,
                        progress=lambda i, t, name, di=di: self.progress.emit(
                            i, t, name, di, len(self._days)),
                        should_cancel=lambda: self._cancel)
                    out["copied"] += res["copied"]
                    out["skipped"] += res["skipped"]
                    out["failed"] += res["failed"]
                    out["bytes"] += res["bytes"]
                    out["errors"].extend(res["errors"])
                    out["results"].append(res)
                    # Remembered per day, immediately — a cancelled run still
                    # keeps whatever days it did finish (see engine.raw_backup).
                    rb.record_backup_run(self._trip_root, res)
                if out["results"]:
                    report_path = rb.write_backup_report(
                        self._trip_root, self._trip_name, self._dest, out["results"])
                    out["report_path"] = str(report_path)
                self.finished.emit(out, "")
            except Exception as e:
                self.finished.emit(out, str(e))


class RawBackupDialog(QDialog):
    def __init__(self, parent, day_dirs, trip_name: str = "", trip_root: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Back up pictures (RAW backup)")
        self._trip_name = trip_name
        self._trip_root = trip_root
        self._day_dirs = [Path(d) for d in day_dirs]
        self._rows: list[dict] = []
        self._thread = None
        self.resize(1000, 560)

        # The jay buries acorns and finds them back months later — a backup.
        # On the left of the whole dialog, as in Create Excel (2026-10-21).
        from gui.wait_picture import picture_column
        outer = picture_column(self, "jay")
        intro_row = QHBoxLayout()
        intro_row.setSpacing(14)
        outer.addLayout(intro_row)
        intro_row.addWidget(wrapped(
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
        # "All" sits above the tick column, where the eye looks for it; the two
        # copy options go to the right (his layout, 2026-10-20). It replaces the
        # Select all / Select none buttons that did the same job at the bottom.
        opts_row.setContentsMargins(4, 0, 0, 0)
        self._all_box = QCheckBox("All")
        self._all_box.setToolTip("Tick or untick every day")
        self._all_box.clicked.connect(self._set_all)
        opts_row.addWidget(self._all_box)
        opts_row.addStretch(1)
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
        outer.addLayout(opts_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Day", "Files", "Backup status"])
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
        # Rich text only kicks in when a message actually embeds an <a href>
        # (the finished-report link below) — Qt's default AutoText leaves
        # plain progress messages alone.
        self._summary.setOpenExternalLinks(False)
        self._summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._summary.linkActivated.connect(self._open_report_link)
        outer.addWidget(self._summary)

        btns = QHBoxLayout()
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

        # Counting "already copied" files at the destination for every day
        # can take a moment. Show the window with a status message and force
        # it to actually paint before running the (blocking) scan — a
        # deferred QTimer.singleShot(0, ...) call still raced the first paint
        # and lost, leaving a blank window the whole time.
        self._summary.setText("Collecting picture counts from all days…")
        self.show()
        QApplication.processEvents()
        self._reload()

    # ── list ──────────────────────────────────────────────────────────────

    def _reload(self):
        """Rebuild from disk. Nothing is pre-ticked (unlike the Metadata
        backup) — status here comes from the remembered state (see
        engine.raw_backup.backup_status), which only says a day is CURRENT
        when its file count still matches what the last run saw; a day that
        grew since then comes back 'Incomplete' rather than looking done.
        One JSON read for the whole table, not a per-day destination scan —
        status shows even when the backup drive isn't attached, except for a
        day with no recorded run, where a plain destination check is the only
        way to notice a pre-existing (legacy/manual) copy."""
        dest = raw_backup_location()
        state = rb.load_backup_state(self._trip_root) if self._trip_root else {}
        self._rows = []
        for d in self._day_dirs:
            try:
                total = sum(1 for p in d.iterdir() if p.is_file())
            except OSError:
                total = 0
            status = rb.backup_status_verified(d.name, total, state, dest, self._trip_name)
            rec = state.get(d.name)
            if status == "not_copied" and dest and rb.count_copied(d, dest, self._trip_name) > 0:
                status = "legacy"
            self._rows.append({"dir": d, "total": total, "status": status, "rec": rec})

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

            if dest:
                label, tooltip, color_key = format_raw_backup_status(r["status"], r["rec"], r["total"])
            else:
                label, tooltip, color_key = "—", "", None
            status_item = QTableWidgetItem(label)
            status_item.setToolTip(tooltip)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if color_key == "done":
                status_item.setForeground(QColor(theme.DONE_FG))
            elif color_key == "warn":
                status_item.setForeground(QColor(theme.WARN))
            self._table.setItem(i, 3, status_item)
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
        # "All" follows the ticks: on only when every day is ticked.
        self._all_box.blockSignals(True)
        self._all_box.setChecked(bool(self._rows) and len(picked) == len(self._rows))
        self._all_box.blockSignals(False)
        pics = sum(self._rows[i]["total"] for i in picked)
        self._summary.setText(
            f"{len(picked)} of {len(self._rows)} day(s) selected — "
            f"about {pics} file(s) before .trash / Blue options are applied.")
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
            days, dest, self._trip_name, self._trip_root,
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

    def _open_report_link(self, href: str):
        # A browser tab, not whatever the OS has associated with .txt — same
        # reasoning as opening a blog's HTML in a real browser rather than an
        # editor. See utils.open_path.open_in_browser.
        from utils.open_path import open_in_browser
        open_in_browser(href)

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
        if out.get("report_path"):
            # Qt's default link blue is unreadable on this app's dark theme —
            # same accent create_jpg_result.py uses for its own links.
            msg += (f' <a href="{out["report_path"]}" '
                    f'style="color:{theme.READY_FG}">View the report</a>')
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
