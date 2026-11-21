"""Database (SQLite) settings + live stats dialog (Phase 2).

Surfaces how the per-machine master SQLite cache is running: row counts, file
sizes, when it was last built, and — most importantly — the pending master
write queue (master.wal.jsonl). If pending writes sit unflushed for longer than
the configurable threshold (e.g. Dropbox offline, or the checkpoint worker
wedged) a red banner warns the user. "Flush now" forces a synchronous
checkpoint. Rebuilding the read-copy lives in File ▸ Settings ▸ Manage Caches.
"""

import logging
import time
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QFrame
)

from utils.config import _config, persist_config

logger = logging.getLogger("speciesviewer")


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class DatabaseSettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Database (SQLite)")
        self.setMinimumWidth(560)

        self._master_root = _config.get(
            "master", "masterbiotadatabase", fallback="").strip()
        self._last_check = None      # the live check is ~1s — far too slow for the 1s timer

        layout = QVBoxLayout(self)

        layout.addWidget(self._section_label("Master SQLite cache"))

        self._warn = QLabel("")
        self._warn.setWordWrap(True)
        self._warn.setStyleSheet(
            "color: white; background: #b00020; padding: 6px; border-radius: 4px;")
        self._warn.hide()
        layout.addWidget(self._warn)

        form = QFormLayout()
        self._values: dict[str, QLabel] = {}
        for key, label in (
            ("db_path", "DB file"),
            ("counts", "Rows (pictures / files)"),
            ("db_size", "DB + WAL size"),
            ("built", "Last built"),
            ("pics_mtime", "pictures CSV mtime"),
            ("files_mtime", "files CSV mtime"),
            ("pending", "Changes not yet copied back"),
        ):
            val = QLabel("—")
            val.setWordWrap(True)
            self._values[key] = val
            form.addRow(QLabel(label + ":"), val)
        layout.addLayout(form)

        layout.addWidget(self._divider())

        # ── Health & backup ───────────────────────────────────────────────────
        # The master has TWO KINGS: the CSVs are canonical, the DB is a derived mirror
        # that writes back to them. On 2026-07-14 the mirror won an argument it should
        # have lost and destroyed 2718 species rows. A guardrail now refuses that write;
        # this panel is how you SEE that the pair still agree, and the way back if not.
        layout.addWidget(self._section_label("Health and backup"))
        hform = QFormLayout()
        for key, label in (
            ("health", "Consistency (CSV vs database)"),
            ("backups", "Backups"),
        ):
            val = QLabel("—")
            val.setWordWrap(True)
            self._values[key] = val
            hform.addRow(QLabel(label + ":"), val)
        layout.addLayout(hform)

        hrow = QHBoxLayout()
        # The check and the restore live in Master ▸ Check the Master…
        # (backlog 1.9); this opens the same window.
        check_btn = QPushButton("Check the Master…")
        check_btn.setToolTip(
            "Every master check as a report, with a fix for what it finds, and\n"
            "restoring a backup. Also in the Master menu.")
        check_btn.clicked.connect(self._open_check_master)
        hrow.addWidget(check_btn)
        backup_btn = QPushButton("Back up now")
        backup_btn.setToolTip(
            "Gzip the three master CSVs into metadata/active/backup/<date>/.\n"
            "About 7 MB and a second. Refused if the consistency check fails —\n"
            "a snapshot of a broken master would eventually push out the last good one.")
        backup_btn.clicked.connect(self._backup_now)
        hrow.addWidget(backup_btn)
        hrow.addStretch(1)
        layout.addLayout(hrow)

        layout.addWidget(self._divider())

        # ── Write-back ────────────────────────────────────────────────────────
        # The first header here said "Pending writes", which read as though
        # SQLite were having trouble writing. It is the opposite: the edit is
        # already safely in the database, and what is outstanding is copying it
        # back into the canonical master CSVs. Normal for a few seconds after an
        # edit; only a count that stays up is a symptom.
        layout.addWidget(self._section_label("Writing changes back to the master"))
        explain = QLabel(
            "An edit goes into the database first, then a worker copies it back "
            "into the master CSV files. A count that stays above zero means that "
            "copy-back is stuck — usually the master path being unreachable.")
        explain.setWordWrap(True)
        layout.addWidget(explain)
        warn_row = QHBoxLayout()
        warn_row.addWidget(QLabel("Warn if changes stay uncopied for longer than (seconds):"))
        self._warn_secs = QLineEdit(
            _config.get("masterdb", "pending_warn_seconds", fallback="30"))
        self._warn_secs.setFixedWidth(80)
        # Saved when you leave the field, not by a Save button. This is the only
        # editable setting on a panel where every other control acts at once, and
        # a lone "Save" down in the corner read as if it committed the whole
        # dialog — it never did.
        self._warn_secs.editingFinished.connect(self._save_warn_secs)   # no Save button
        warn_row.addWidget(self._warn_secs)
        warn_row.addStretch(1)
        layout.addLayout(warn_row)

        btn_row = QHBoxLayout()
        flush_btn = QPushButton("Flush now")
        flush_btn.clicked.connect(self._flush_now)
        btn_row.addWidget(flush_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        layout.addWidget(self._divider())

        # ── Trashed images ────────────────────────────────────────────────────
        # Its own section because it is its own subject: files that are deleted
        # from the master but still on disk. The size line and the two buttons
        # that act on it belong in one eyeful — particularly Empty trash, which
        # is what makes Restore deleted impossible.
        layout.addWidget(self._section_label("Trashed images"))
        tform = QFormLayout()
        val = QLabel("—")
        val.setWordWrap(True)
        self._values["trash"] = val
        tform.addRow(QLabel("Deleted, still recoverable:"), val)
        layout.addLayout(tform)

        trash_row = QHBoxLayout()
        # Restore and Empty live in one window, Master ▸ Master Trash…
        # (backlog 1.3); this opens the same window.
        undo_btn = QPushButton("Master Trash…")
        undo_btn.setToolTip(
            "Put deleted master pictures back, or empty the trash for good.\n"
            "Also in the Master menu.")
        undo_btn.clicked.connect(self._undo_last_delete)
        trash_row.addWidget(undo_btn)
        trash_row.addStretch(1)
        layout.addLayout(trash_row)

        layout.addWidget(self._divider())

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        # Live refresh while the dialog is open.
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    # ── helpers ───────────────────────────────────────────────────────────
    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; margin-top: 4px;")
        return lbl

    def _divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _open_db(self):
        """Open the existing DB read-only-ish (no ensure_fresh — we don't want a
        6s rebuild from a stats panel). Returns a MasterDB or None."""
        if not self._master_root or not Path(self._master_root).exists():
            return None
        from db.master_db import MasterDB
        return MasterDB(self._master_root)

    # ── live stats ────────────────────────────────────────────────────────
    def _refresh(self):
        from db.master_db import db_path, wal_jsonl_path
        v = self._values
        dbp = db_path()
        v["db_path"].setText(str(dbp))

        # Sizes (db + WAL sidecar) without opening a connection.
        size = 0
        for p in (dbp, dbp.with_name(dbp.name + "-wal"),
                  dbp.with_name(dbp.name + "-shm")):
            if p.exists():
                size += p.stat().st_size
        v["db_size"].setText(_fmt_size(size) if dbp.exists() else "— (not built)")

        db = self._open_db()
        if db is None:
            for k in ("counts", "built", "pics_mtime", "files_mtime"):
                v[k].setText("— (master root not set)")
        else:
            try:
                v["counts"].setText(
                    f"{db.picture_count():,} / {db.file_count():,}")
                built = db.meta_value("built_at")
                if built:
                    try:
                        built = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(float(built)))
                    except ValueError:
                        pass
                v["built"].setText(built or "—")
                v["pics_mtime"].setText(self._mtime_str(db.meta_value("pictures_csv_mtime")))
                v["files_mtime"].setText(self._mtime_str(db.meta_value("files_csv_mtime")))
            except Exception as e:
                v["counts"].setText(f"(error: {e})")
            finally:
                db.close()

        # Pending writes + stuck warning (jsonl is cheap to read).
        pending = 0
        oldest = None
        jp = wal_jsonl_path()
        if jp.exists():
            stats_db = self._open_db()
            if stats_db is not None:
                try:
                    pending = stats_db.pending_count()
                    oldest = stats_db.pending_oldest_ts()
                finally:
                    stats_db.close()
        # Trash (deleted-but-recoverable master files).
        if self._master_root:
            try:
                from db.master_mutate import trash_size
                tbytes, tfiles = trash_size(self._master_root)
                v["trash"].setText(
                    f"{_fmt_size(tbytes)} in {tfiles} file(s)" if tfiles
                    else "empty")
            except Exception as e:
                v["trash"].setText(f"(error: {e})")
        else:
            v["trash"].setText("—")

        # Backups + the last consistency verdict. Both cheap — a directory listing and
        # a cached result. The check itself takes ~1s, far too slow for a 1s timer, so
        # it runs on the daily backup (its verdict is in the manifest) or on demand.
        if self._master_root:
            try:
                from db import master_health
                backups = master_health.list_backups(self._master_root)
                if backups:
                    total = sum(b["bytes"] for b in backups)
                    newest = backups[0]
                    v["backups"].setText(
                        f"{len(backups)} kept, {_fmt_size(total)} — newest {newest['day']}")
                    if self._last_check is None:
                        verdict = (newest["manifest"] or {}).get("check")
                        v["health"].setText(
                            f"{verdict} (as of the backup on {newest['day']})"
                            if verdict else "not checked yet — press Check now")
                else:
                    v["backups"].setText("none yet — one is taken daily on startup")
                    if self._last_check is None:
                        v["health"].setText("not checked yet — press Check now")
                if self._last_check is not None:
                    rep = self._last_check
                    v["health"].setText(
                        f"{rep.summary()} (checked just now, {rep.seconds:.1f}s)")
            except Exception as e:
                v["backups"].setText(f"(error: {e})")
        else:
            v["backups"].setText("—")
            v["health"].setText("—")

        if pending == 0:
            v["pending"].setText("0 (queue empty)")
            self._warn.hide()
        else:
            age = (time.time() - oldest) if oldest else 0
            v["pending"].setText(f"{pending} pending — oldest {age:.0f}s old")
            try:
                threshold = int(self._warn_secs.text())
            except ValueError:
                threshold = 30
            if age > threshold:
                self._warn.setText(
                    f"⚠ {pending} master write(s) have been pending for "
                    f"{age:.0f}s (> {threshold}s). The canonical master CSVs are "
                    f"behind — check that the checkpoint worker is running and "
                    f"that the master path is reachable (Dropbox online).")
                self._warn.show()
            else:
                self._warn.hide()

    def _mtime_str(self, raw):
        if not raw:
            return "—"
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(raw)))
        except ValueError:
            return str(raw)

    # ── health & backup ───────────────────────────────────────────────────
    def _open_check_master(self):
        from gui.check_master import open_check_master
        open_check_master(self)
        self._refresh()

    def _backup_now(self):
        if not self._master_root:
            return
        from db.master_db import MasterDB
        from db import master_health
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        db = MasterDB(self._master_root)
        try:
            db.ensure_fresh()
            res = master_health.make_backup(db, self._master_root)
        except Exception as e:
            QMessageBox.warning(self, "Back up", f"The backup failed:\n{e}")
            return
        finally:
            db.close()
            QApplication.restoreOverrideCursor()

        if not res.get("ok"):
            self._last_check = res.get("report")
            QMessageBox.critical(
                self, "Back up — refused",
                "No backup was taken: the master failed its consistency check.\n\n"
                "A snapshot of a broken master is not a backup — keeping it would "
                "eventually push the last good one out of the rotation.\n\n"
                "Press Check now to see what is wrong.")
        elif res.get("skipped"):
            self._last_check = res.get("report")
            QMessageBox.information(
                self, "Back up",
                "Nothing to back up yet — this collection is still empty.")
        else:
            self._last_check = res.get("report")
            QMessageBox.information(
                self, "Back up",
                f"Backed up to {res['path'].name} — {_fmt_size(res['bytes'])} "
                f"in {res['seconds']:.1f}s.\n\nThe master is {res['report'].summary()}.")
        self._refresh()

    # ── actions ───────────────────────────────────────────────────────────
    def _flush_now(self):
        if not self._master_root:
            return
        from db.master_db import MasterCheckpointWorker
        try:
            n = MasterCheckpointWorker.get(self._master_root).flush()
        except Exception as e:
            QMessageBox.warning(self, "Flush", f"Flush failed:\n{e}")
            return
        QMessageBox.information(self, "Flush", f"Flushed {n} pending write(s).")
        self._refresh()

    def _undo_last_delete(self):
        """Open the trash picker.

        The engine could always undo a delete — master_mutate.undo_delete has
        worked for a while — but nothing in the program could reach it, so in
        practice an accidental delete was permanent whatever the trash still
        held. Deletes stack, so the screen is a list rather than a one-shot
        "undo last": popping the stack a dialog at a time says nothing about
        what else is down there.

        It belongs beside Restore and Empty trash: those are the master's other
        recovery actions, and Empty trash is the one button that takes this
        possibility away, so the two want to be in the same eyeful.
        """
        if not self._master_root:
            return
        from settings.trash_restore import RestoreTrashDialog
        RestoreTrashDialog(self, self._master_root).exec()
        self._refresh()

    def _save_warn_secs(self):
        """Persist the warn threshold as soon as the field is left.

        There used to be a Save button here, and it covered this one field while
        every other control on the panel acted immediately — so it looked like
        the dialog had unsaved state that it never had. A rejected value is put
        back to what is stored rather than left sitting there looking accepted.
        """
        text = self._warn_secs.text().strip()
        stored = _config.get("masterdb", "pending_warn_seconds", fallback="30")
        if text == stored:
            return
        try:
            int(text)
        except ValueError:
            QMessageBox.warning(self, "Write-back",
                                "The warning threshold must be a whole number of seconds.")
            self._warn_secs.setText(stored)
            return
        if not _config.has_section("masterdb"):
            _config.add_section("masterdb")
        _config.set("masterdb", "pending_warn_seconds", text)
        persist_config()
        logger.info(f"Pending-write warning threshold set to {text}s.")
