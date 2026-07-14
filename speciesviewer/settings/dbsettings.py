"""Database (SQLite) settings + live stats dialog (Phase 2).

Surfaces how the per-machine master SQLite cache is running: row counts, file
sizes, when it was last built, and — most importantly — the pending master
write queue (master.wal.jsonl). If pending writes sit unflushed for longer than
the configurable threshold (e.g. Dropbox offline, or the checkpoint worker
wedged) a red banner warns the user. "Flush now" forces a synchronous
checkpoint; "Rebuild master DB" re-mirrors the CSVs.
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
            ("pending", "Pending master writes"),
            ("trash", "Trash (deleted, recoverable)"),
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
        check_btn = QPushButton("Check now")
        check_btn.setToolTip(
            "Compare the master CSVs with the database, and the master with itself:\n"
            "row counts, pictures naming a JPG that has no file row, orphan file rows,\n"
            "and JPGmap entries that would publish as a broken image.")
        check_btn.clicked.connect(self._check_now)
        hrow.addWidget(check_btn)
        backup_btn = QPushButton("Back up now")
        backup_btn.setToolTip(
            "Gzip the three master CSVs into metadata/active/backup/<date>/.\n"
            "About 7 MB and a second. Refused if the consistency check fails —\n"
            "a snapshot of a broken master would eventually push out the last good one.")
        backup_btn.clicked.connect(self._backup_now)
        hrow.addWidget(backup_btn)
        restore_btn = QPushButton("Restore…")
        restore_btn.clicked.connect(self._restore)
        hrow.addWidget(restore_btn)
        hrow.addStretch(1)
        layout.addLayout(hrow)

        layout.addWidget(self._divider())

        # Configurable stuck-pending-writes threshold.
        warn_row = QHBoxLayout()
        warn_row.addWidget(QLabel("Warn if pending writes older than (seconds):"))
        self._warn_secs = QLineEdit(
            _config.get("masterdb", "pending_warn_seconds", fallback="30"))
        self._warn_secs.setFixedWidth(80)
        warn_row.addWidget(self._warn_secs)
        warn_row.addStretch(1)
        layout.addLayout(warn_row)

        btn_row = QHBoxLayout()
        flush_btn = QPushButton("Flush now")
        flush_btn.clicked.connect(self._flush_now)
        btn_row.addWidget(flush_btn)
        rebuild_btn = QPushButton("Rebuild master DB")
        rebuild_btn.clicked.connect(self._rebuild)
        btn_row.addWidget(rebuild_btn)
        purge_btn = QPushButton("Empty trash…")
        purge_btn.clicked.connect(self._purge_trash)
        btn_row.addWidget(purge_btn)
        btn_row.addStretch(1)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

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
    def _run_check(self):
        from db.master_db import MasterDB
        from db import master_health
        db = MasterDB(self._master_root)
        try:
            db.ensure_fresh()
            rep = master_health.check(db, self._master_root)
        finally:
            db.close()
        self._last_check = rep
        return rep

    def _check_now(self):
        if not self._master_root:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rep = self._run_check()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Consistency check", f"The check failed:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        lines = [f"Rows — {name}: {c['csv']} in the CSV, {c['db']} in the database"
                 for name, c in rep.counts.items()]
        if rep.findings:
            lines.append("")
            for f in rep.findings:
                mark = "PROBLEM" if f.level == "error" else "warning"
                lines.append(f"[{mark}] {f.what}" + (f"\n          {f.detail}"
                                                     if f.detail else ""))
        else:
            lines.append("\nNo problems found.")
        box = QMessageBox(self)
        box.setWindowTitle("Consistency check")
        box.setIcon(QMessageBox.Icon.Critical if rep.errors
                    else QMessageBox.Icon.Information)
        box.setText(f"The master is {rep.summary()}.")
        box.setDetailedText("\n".join(lines))
        box.exec()
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
        else:
            self._last_check = res.get("report")
            QMessageBox.information(
                self, "Back up",
                f"Backed up to {res['path'].name} — {_fmt_size(res['bytes'])} "
                f"in {res['seconds']:.1f}s.\n\nThe master is {res['report'].summary()}.")
        self._refresh()

    def _restore(self):
        if not self._master_root:
            return
        from PyQt6.QtWidgets import QInputDialog
        from db import master_health
        backups = master_health.list_backups(self._master_root)
        backups = [b for b in backups if not b["day"].startswith("pre-restore")]
        if not backups:
            QMessageBox.information(self, "Restore", "There are no backups yet.")
            return
        items = [f"{b['day']}  —  {_fmt_size(b['bytes'])}  "
                 f"({(b['manifest'] or {}).get('check', 'not checked')})"
                 for b in backups]
        pick, ok = QInputDialog.getItem(
            self, "Restore the master CSVs",
            "Restoring REPLACES the three master CSVs and rebuilds the database.\n"
            "The current CSVs are snapshotted first, so this is reversible.\n\n"
            "Restore from:", items, 0, False)
        if not ok:
            return
        day = backups[items.index(pick)]["day"]
        if QMessageBox.question(
                self, "Restore the master CSVs",
                f"Replace data_pictures.csv, data_files.csv and species.csv with the "
                f"backup from {day}?\n\nAnything added to the master since then will be "
                f"lost. The current CSVs are kept (gzipped) under backup/pre-restore-*, "
                f"so you can come back.") != QMessageBox.StandardButton.Yes:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = master_health.restore(self._master_root, day)
            from db.master_db import MasterDB
            db = MasterDB(self._master_root)
            try:
                db.rebuild()          # the DB is stale by construction after a restore
            finally:
                db.close()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Restore", f"The restore failed:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._last_check = None
        QMessageBox.information(
            self, "Restore",
            f"Restored {len(res['restored'])} CSV(s) from {day} and rebuilt the "
            f"database.\n\nThe CSVs you replaced are in {res['kept'].name}.\n\n"
            f"Restart SpeciesViewer — it caches the master in memory.")
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

    def _purge_trash(self):
        if not self._master_root:
            return
        from db.master_mutate import trash_size, purge_trash, trash_root
        root = trash_root(self._master_root)
        if not root.exists() or not any(root.iterdir()):
            QMessageBox.information(self, "Empty trash", "The master trash is already empty.")
            return
        tbytes, tfiles = trash_size(self._master_root)
        if QMessageBox.question(
                self, "Empty trash",
                f"Permanently delete {tfiles} recoverable file(s) "
                f"({_fmt_size(tbytes)}) and clear the trash manifest/snapshots? "
                f"This cannot be undone.") != QMessageBox.StandardButton.Yes:
            return
        try:
            n = purge_trash(self._master_root, older_than_days=0)
        except Exception as e:
            QMessageBox.warning(self, "Empty trash", f"Purge failed:\n{e}")
            return
        QMessageBox.information(self, "Empty trash", f"Removed {n} day-folder(s).")
        self._refresh()

    def _rebuild(self):
        if not self._master_root or not Path(self._master_root).exists():
            QMessageBox.warning(self, "Rebuild master DB",
                                "Master root not configured or not found.")
            return
        from db.master_db import MasterDB
        try:
            db = MasterDB(self._master_root)
            db.rebuild()
            n = db.picture_count()
            db.close()
        except Exception as e:
            QMessageBox.warning(self, "Rebuild master DB", f"Rebuild failed:\n{e}")
            return
        QMessageBox.information(self, "Rebuild master DB",
                                f"Master DB rebuilt — {n} pictures indexed.")
        self._refresh()

    def _save(self):
        if not _config.has_section("masterdb"):
            _config.add_section("masterdb")
        try:
            int(self._warn_secs.text())
        except ValueError:
            QMessageBox.warning(self, "Save", "Warn threshold must be a whole number.")
            return
        _config.set("masterdb", "pending_warn_seconds", self._warn_secs.text().strip())
        persist_config()
        QMessageBox.information(self, "Success", "Database settings saved")
        logger.info("Database (SQLite) settings updated.")
        self.close()
