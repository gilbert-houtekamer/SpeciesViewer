# add_to_master.py: "Add Pictures to Master" — point at one folder, ingest it
# through an ephemeral micro-trip, review/edit it in a DEDICATED window, and
# (next step) commit it to the master.
#
# The micro-trip is a self-contained entity: the review window reads the clean
# scratch data (just the folder's freshly-recognised pictures), NOT the live
# trip — so there's no bleed from an existing dirty day. The active trip is
# never touched. The picture files are only read/edited in place; nothing moves
# until the commit + optional delete-originals step.

import os
import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QCheckBox,
    QPlainTextEdit, QFileDialog, QLabel, QApplication, QMessageBox, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QFont, QShortcut, QKeySequence, QTextCursor, QTextDocument,
)

from utils.config import _config, persist_config
from gui.image_viewer import ImagePreviewDialog

logger = logging.getLogger("speciesviewer")


class MicroTripReviewDialog(ImagePreviewDialog):
    """The dedicated micro-trip review window. Reuses the Species Image Preview
    card grid (species/GPS/rating editing) but is pointed at the scratch repo so
    it shows ONLY the folder's clean, freshly-recognised pictures, and carries
    the add-to-master chrome (title + a commit bar)."""

    def __init__(self, parent, micro_trip, report):
        self._micro_trip = micro_trip
        self._report = report or {}
        # A species fix here writes the real XMP, but the scratch is ephemeral and
        # belongs to no live trip — don't trigger the trip's "re-run Read &
        # Summarize" (it targets the active trip, not the scratch). Re-Scan instead.
        self._suppress_rerun = True
        super().__init__(parent,
                         image_dir=micro_trip.input_dir,
                         trip_root_dir=micro_trip.scratch_repo,
                         mode="all",
                         start_species=True)   # focus on the pictures that will be committed
        folder = Path(micro_trip.input_dir).name
        self.setWindowTitle(f"Add to Master — {folder}")
        self._add_commit_bar(self._report)

    def _color_delete_trash_root(self):
        # The trip_root here is the throwaway scratch (discarded on close), so a
        # blue/purple cull must NOT trash there. Send it beside the real pictures
        # so it's recoverable, like a normal trip cull.
        from pathlib import Path as _P
        return _P(self._micro_trip.input_dir) / ".trash"

    def _add_commit_bar(self, report):
        bar = QHBoxLayout()
        n = report.get("pictures", 0)
        ident = report.get("identified", 0)
        untag = report.get("no_species", 0)
        info = QLabel(f"{n} pictures · {ident} with a species · {untag} untagged "
                      f"(untagged are not added)")
        info.setStyleSheet("color: #444;")
        self.delete_originals_cb = QCheckBox("Delete originals after commit")
        self.delete_originals_cb.setToolTip("After committing, delete the folder's "
                                            "files that are confirmed present in the master.")
        self.commit_btn = QPushButton("Commit to Master")
        self.commit_btn.setEnabled(ident > 0)
        self.commit_btn.clicked.connect(self._commit)
        bar.addWidget(info)
        bar.addStretch(1)
        bar.addWidget(self.delete_originals_cb)
        bar.addWidget(self.commit_btn)
        self.layout().addLayout(bar)

    def _commit(self):
        """Run the proven master pipeline (sv2→sv3→sv5→sv6→sv7) scoped to this
        one folder. Re-reads the real files (with any species/GPS edits the user
        just made), copies them into the master, and updates the website."""
        import tempfile
        import configparser
        from utils.batch_config import generate_svconfig_ini
        import batch.sv2ReadDirExif as sv2
        import batch.sv3WritePictures as sv3
        import batch.sv5BuildWebsite as sv5
        import batch.sv6Freeze as sv6
        import batch.sv7WriteCommit as sv7
        from PyQt6.QtWidgets import QProgressDialog

        folder = self._micro_trip.input_dir
        recurse = getattr(self._micro_trip, "recurse", False)
        ident = self._report.get("identified", 0)
        if QMessageBox.question(
                self, "Commit to Master",
                f"Add the {ident} identified picture(s) in\n{folder}\n"
                f"to the master and update the website?\n\n"
                f"Untagged pictures are not added. This writes to the master "
                f"(undoable via the master .trash).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        # Write to a temp svconfig so the user's svconfig.ini is left alone.
        tmp_cfg = os.path.join(tempfile.gettempdir(), "sv_addmaster_svconfig.ini")
        generate_svconfig_ini(svconfig_path=tmp_cfg, master_mode=True,
                              single_dir=folder, recurse=recurse)
        cfg = configparser.ConfigParser()
        cfg.read(tmp_cfg)

        steps = [
            ("Reading & recognising species", sv2.main),
            ("Summarising pictures", sv3.main),
            ("Building master website", sv5.main),
            ("Freezing", sv6.main),
            ("Committing", sv7.main),
        ]
        prog = QProgressDialog("Committing to master…", None, 0, len(steps), self)
        prog.setWindowTitle("Add to Master")
        prog.setWindowModality(Qt.WindowModality.ApplicationModal)
        prog.setCancelButton(None)
        prog.setMinimumDuration(0)
        prog.show()
        self.commit_btn.setEnabled(False)
        try:
            for i, (label, fn) in enumerate(steps):
                prog.setLabelText(f"{label}…")
                prog.setValue(i)
                QApplication.processEvents()
                fn(None, cfg)
            prog.setValue(len(steps))
            deleted_msg = ""
            if self.delete_originals_cb.isChecked():
                deleted_msg = self._delete_originals(folder)
            QMessageBox.information(
                self, "Add to Master",
                f"Added {ident} picture(s) to the master and updated the website.{deleted_msg}")
            self.commit_btn.setText("Committed ✓")
            # If this folder is a registered Day, mark it done automatically
            # (it stays in the Days list; nothing is deleted). No-op otherwise.
            try:
                from utils import days_registry
                if days_registry.find_day(folder):
                    days_registry.set_status(folder, days_registry.STATUS_DONE)
                    panel = getattr(self.main_window, "days_panel", None)
                    if panel is not None:
                        panel.rebuild()
            except Exception as e:
                logger.info(f"add_to_master: could not mark day done: {e}")
        except Exception as e:
            logger.warning(f"add_to_master: commit failed: {e}")
            QMessageBox.warning(self, "Add to Master", f"Commit failed:\n{e}")
            self.commit_btn.setEnabled(True)
        finally:
            prog.close()

    def _delete_originals(self, folder):
        """Delete the folder's files now confirmed present in the master (reuses
        the master-authoritative trip-delete scan — only byte-confirmed files are
        removed). Returns a message fragment for the result dialog."""
        import engine.trip_delete_scan as tds
        try:
            plan = tds.scan_trip(folder, self._micro_trip.master_root, "")
            if not plan.get("ok"):
                return f"\nOriginals kept — could not verify against master: {plan.get('error')}"
            nf, npx, errs = tds.execute_scan_delete(plan)
            msg = f"\nDeleted {npx} original picture(s) ({nf} files) now safe in the master."
            held = plan["totals"].get("held_files", 0) + plan["totals"].get("keep_files", 0)
            if held:
                msg += (f" Kept {held} file(s) the master doesn't hold an exact copy of "
                        f"(see log).")
                self._log_kept_files(plan)
            if errs:
                msg += f" {len(errs)} could not be deleted (see log)."
            return msg
        except Exception as e:
            logger.warning(f"add_to_master: delete originals failed: {e}")
            return f"\nOriginals kept — delete failed: {e}"

    def _log_kept_files(self, plan):
        """List every file the scan kept (the master has no exact copy) so the
        user can see what — and why. 'held' = the picture is in the master but
        not this exact file/rendition; 'keep' = the picture isn't in the master
        at all. Both are left on disk untouched."""
        why = {"held": "picture in master, but not this exact file",
               "keep": "picture not in master"}
        for verdict in ("held", "keep"):
            for d in plan.get("days", []):
                for it in d.get(verdict, []):
                    logger.info(f"add_to_master: kept {os.path.basename(it['path'])}"
                                f" — {why[verdict]}  ({it['path']})")


class AddToMasterDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Add Pictures to Master")
        self.resize(780, 520)
        self._mt = None                 # the live micro-trip (scratch on disk)
        self._report = None
        self._review_dlg = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Choose one folder of pictures to add to the master.\n"
            "Pictures need a species (tag them in Lightroom first); untagged "
            "ones are listed but not added."))

        row = QHBoxLayout()
        self.dir_edit = QLineEdit(_config.get("addmaster", "lastdir", fallback=""))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.dir_edit)
        row.addWidget(browse)
        lay.addLayout(row)

        self.recurse_cb = QCheckBox("Include nested folders")
        self.recurse_cb.setChecked(_config.get("addmaster", "recurse", fallback="0") == "1")
        self.recurse_cb.toggled.connect(self._save_recurse)
        lay.addWidget(self.recurse_cb)

        btns = QHBoxLayout()
        self.scan_btn = QPushButton("Scan folder")
        self.scan_btn.clicked.connect(self._scan)
        self.review_btn = QPushButton("Review && edit…")
        self.review_btn.setToolTip("Open the micro-trip's pictures in a dedicated "
                                   "window to fix species, GPS and descriptions.")
        self.review_btn.clicked.connect(self._review)
        self.review_btn.setEnabled(False)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.clicked.connect(self._discard)
        self.discard_btn.setEnabled(False)
        btns.addWidget(self.scan_btn)
        btns.addWidget(self.review_btn)
        btns.addWidget(self.discard_btn)
        btns.addStretch(1)
        lay.addLayout(btns)

        self.out = QPlainTextEdit(readOnly=True)
        self.out.setFont(QFont("Consolas", 10))
        self.out.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(self.out)

        lay.addWidget(self._build_find_bar())

        self.hint = QLabel("Next: after editing, commit to the master (coming next).")
        self.hint.setStyleSheet("color: gray;")
        lay.addWidget(self.hint)

        close = QPushButton("Close")
        close.clicked.connect(self.close)
        lay.addWidget(close)

        # Ctrl+F (and F3 / Shift+F3) search the report text.
        QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self._open_find)
        QShortcut(QKeySequence.StandardKey.FindNext, self).activated.connect(self._find_next)
        QShortcut(QKeySequence.StandardKey.FindPrevious, self).activated.connect(self._find_prev)

    # ── Find-in-report bar (Ctrl+F) ───────────────────────────────────────────
    def _build_find_bar(self) -> QWidget:
        self.find_row = QWidget()
        fr = QHBoxLayout(self.find_row)
        fr.setContentsMargins(0, 0, 0, 0)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find in report…  (Enter = next, Esc = close)")
        self.find_input.returnPressed.connect(self._find_next)
        self.find_input.textChanged.connect(self._find_incremental)
        prev_btn = QPushButton("▲"); prev_btn.setFixedWidth(28)
        next_btn = QPushButton("▼"); next_btn.setFixedWidth(28)
        prev_btn.setToolTip("Previous match"); next_btn.setToolTip("Next match")
        prev_btn.clicked.connect(self._find_prev)
        next_btn.clicked.connect(self._find_next)
        self.find_status = QLabel("")
        self.find_status.setStyleSheet("color: #888;")
        for w in (self.find_input, prev_btn, next_btn, self.find_status):
            fr.addWidget(w)
        fr.addStretch(1)
        # Esc closes the find bar only (scoped to the box so it doesn't reject the dialog).
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.find_input)
        esc.setContext(Qt.ShortcutContext.WidgetShortcut)
        esc.activated.connect(self._close_find)
        self.find_row.setVisible(False)
        return self.find_row

    def _open_find(self):
        self.find_row.setVisible(True)
        self.find_input.setFocus()
        self.find_input.selectAll()

    def _close_find(self):
        self.find_row.setVisible(False)
        self.find_status.clear()
        self.out.setFocus()

    def _find_incremental(self, _text):
        # Keep the current match selected as the query grows: search from the
        # start of the current selection.
        cursor = self.out.textCursor()
        cursor.setPosition(cursor.selectionStart())
        self.out.setTextCursor(cursor)
        self._do_find(backward=False)

    def _find_next(self):
        self._do_find(backward=False)

    def _find_prev(self):
        self._do_find(backward=True)

    def _do_find(self, backward: bool):
        text = self.find_input.text()
        if not text:
            self.find_status.clear()
            return
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if self.out.find(text, flags):
            self.find_status.clear()
            return
        # Wrap around to the other end and try once more.
        cursor = self.out.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End if backward
                            else QTextCursor.MoveOperation.Start)
        self.out.setTextCursor(cursor)
        self.find_status.setText("" if self.out.find(text, flags) else "no match")

    def _save_recurse(self, checked: bool):
        if not _config.has_section("addmaster"):
            _config.add_section("addmaster")
        _config.set("addmaster", "recurse", "1" if checked else "0")
        try:
            persist_config()
        except Exception as e:
            logger.info(f"add_to_master: could not persist recurse: {e}")

    def _browse(self):
        start = (self.dir_edit.text().strip()
                 or _config.get("project", "picturedirectory", fallback=""))
        chosen = QFileDialog.getExistingDirectory(self, "Choose a picture folder", start)
        if chosen:
            self.dir_edit.setText(chosen)

    @staticmethod
    def _clean(s):
        return (s or "").replace('"', "").replace("'", "").strip()

    # ── scan (ingest into a fresh scratch) ────────────────────────────────────
    def _scan(self):
        import engine.micro_trip as micro_trip
        input_dir = self._clean(self.dir_edit.text())
        master = self._clean(_config.get("master", "masterbiotadatabase", fallback=""))
        web = self._clean(_config.get("master", "masterbiotawebroot", fallback="/nature/")) or "/nature/"
        if not input_dir or not os.path.isdir(input_dir):
            self.out.setPlainText("Pick a valid folder first.")
            return
        if not master:
            self.out.setPlainText("No master database is configured.")
            return

        if not _config.has_section("addmaster"):
            _config.add_section("addmaster")
        _config.set("addmaster", "lastdir", input_dir)
        try:
            persist_config()
        except Exception as e:
            logger.info(f"add_to_master: could not persist lastdir: {e}")

        # Drop any previous scratch (and its review window) before scanning again.
        self._close_review()
        self._cleanup_scratch()

        self.scan_btn.setEnabled(False)
        self.out.setPlainText("Scanning… (reading EXIF and recognising species)")
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            mt = micro_trip.MicroTrip(input_dir, master, web_root=web,
                                      recurse=self.recurse_cb.isChecked())
            mt.setup()
            mt.ingest()
            report = mt.summary()
            self._mt = mt
            self._report = report
            self.out.setPlainText(micro_trip.format_summary(report))
            has_pics = report.get("ok", False) and report.get("identified", 0) > 0
            self.review_btn.setEnabled(has_pics)
            self.discard_btn.setEnabled(report.get("ok", False))
        except Exception as e:
            logger.warning(f"add_to_master: scan failed: {e}")
            self.out.setPlainText(f"Scan failed: {e}")
            self._cleanup_scratch()
        finally:
            QApplication.restoreOverrideCursor()
            self.scan_btn.setEnabled(True)

    # ── review (dedicated window on the clean scratch data) ───────────────────
    def _review(self):
        if not self._mt or not self._mt.scratch_repo:
            return
        self._close_review()
        try:
            self._review_dlg = MicroTripReviewDialog(self.main_window, self._mt, self._report or {})
            self._review_dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            self._review_dlg.show()
            self._review_dlg.raise_()
            self._review_dlg.activateWindow()
        except Exception as e:
            logger.warning(f"add_to_master: could not open review window: {e}")
            QMessageBox.warning(self, "Add Pictures to Master",
                                f"Could not open the review window:\n{e}")

    # ── discard / cleanup ─────────────────────────────────────────────────────
    def _discard(self):
        self._close_review()
        self._cleanup_scratch()
        self.review_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)
        self.out.appendPlainText("\nDiscarded — scratch removed.")

    def _close_review(self):
        if self._review_dlg is not None:
            try:
                self._review_dlg.close()
            except Exception:
                pass
            self._review_dlg = None

    def _cleanup_scratch(self):
        if self._mt is not None:
            try:
                self._mt.teardown()
            except Exception as e:
                logger.info(f"add_to_master: scratch cleanup: {e}")
            self._mt = None
            self._report = None

    def _reset_scan_state(self):
        """Clean slate for a (re)open: the scratch from a previous session is
        gone, so the old scan results are stale and Review/Discard are dead.
        Keep the remembered directory; require a fresh Scan."""
        self.review_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)
        self.out.setPlainText('Click "Scan folder" to read this folder.')

    def closeEvent(self, event):
        # Closing ends the session: close the review window and remove the
        # scratch (XMP edits already live on the real files), then leave the
        # dialog clean so the next open isn't showing stale results.
        self._close_review()
        self._cleanup_scratch()
        self._reset_scan_state()
        super().closeEvent(event)

    def showEvent(self, event):
        # Reopened (the instance is reused) with no live scratch -> stale state.
        # Reset so the slow scan is simply redone, keeping the remembered folder.
        super().showEvent(event)
        if self._mt is None:
            self._reset_scan_state()
