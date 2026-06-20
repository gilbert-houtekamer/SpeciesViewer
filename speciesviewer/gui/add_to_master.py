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
    QPlainTextEdit, QFileDialog, QLabel, QApplication, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

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
        super().__init__(parent,
                         image_dir=micro_trip.input_dir,
                         trip_root_dir=micro_trip.scratch_repo,
                         mode="all")
        folder = Path(micro_trip.input_dir).name
        self.setWindowTitle(f"Add to Master — {folder}")
        self._add_commit_bar(self._report)

    def _add_commit_bar(self, report):
        bar = QHBoxLayout()
        n = report.get("pictures", 0)
        ident = report.get("identified", 0)
        untag = report.get("no_species", 0)
        info = QLabel(f"{n} pictures · {ident} with a species · {untag} untagged "
                      f"(untagged are not added)")
        info.setStyleSheet("color: #444;")
        self.delete_originals_cb = QCheckBox("Delete originals after commit")
        self.delete_originals_cb.setEnabled(False)        # next increment
        self.delete_originals_cb.setToolTip("Coming next — use Trip ▸ Delete Local "
                                            "Files Backed Up in Master for now.")
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
            QMessageBox.information(
                self, "Add to Master",
                f"Added {ident} picture(s) to the master and updated the website.")
            self.commit_btn.setText("Committed ✓")
        except Exception as e:
            logger.warning(f"add_to_master: commit failed: {e}")
            QMessageBox.warning(self, "Add to Master", f"Commit failed:\n{e}")
            self.commit_btn.setEnabled(True)
        finally:
            prog.close()


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

        self.hint = QLabel("Next: after editing, commit to the master (coming next).")
        self.hint.setStyleSheet("color: gray;")
        lay.addWidget(self.hint)

        close = QPushButton("Close")
        close.clicked.connect(self.close)
        lay.addWidget(close)

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

    def closeEvent(self, event):
        # Closing ends the session: close the review window and remove the
        # scratch (XMP edits already live on the real files).
        self._close_review()
        self._cleanup_scratch()
        super().closeEvent(event)
