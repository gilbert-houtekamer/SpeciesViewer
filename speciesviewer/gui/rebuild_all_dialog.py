"""Rebuild all Albums and the Master website — the GUI face of
batch/svrRebuildAllWebsites.

One job: sweep a template/code fix across every album's published site, without
selecting each album by hand.

Why its own dialog rather than BatchModuleRunner: that runner is built around a
StateMachine keyed on one repository's PictureDirectory (it fingerprints step
output CSVs and updates trip cells). A sweep has no single repository and no
per-day state — it walks 17 of them. It also must never touch which album is
active, and BatchModuleRunner starts by writing svconfig.ini for the active one.

The dry run is the default action and the one wired to the Enter key; rebuilding
asks for confirmation first, because it re-renders and re-uploads every album.
"""

import io
import logging
import time

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QPlainTextEdit, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, QObject, QTimer, pyqtSignal

from batch.svrRebuildAllWebsites import _hms
from widgets.small_widgets import HTMLLabel

logger = logging.getLogger("speciesviewer")


class _LineStream(io.TextIOBase):
    """A stdout stand-in that emits each finished line as a Qt signal, so the
    sweep's progress appears while it runs instead of all at the end."""

    def __init__(self, emit):
        super().__init__()
        self._emit = emit
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(line)
        return len(s)

    def flush(self):
        if self._buf:
            self._emit(self._buf)
            self._buf = ""


class _Worker(QObject):
    line = pyqtSignal(str)
    finished = pyqtSignal(str)          # "" = success, else the error

    def __init__(self, apply_it, blogs, master, albums, website):
        super().__init__()
        self._apply, self._blogs = apply_it, blogs
        self._master, self._albums, self._website = master, albums, website

    def run(self):
        import contextlib
        import batch.svrRebuildAllWebsites as sweep
        from utils.config import _config

        class Args:
            apply = self._apply
            blogs = self._blogs
            master = self._master
            albums = self._albums
            website = self._website
            only = ""

        err = ""
        stream = _LineStream(self.line.emit)
        try:
            with contextlib.redirect_stdout(stream):
                sweep.main(Args(), _config)
            stream.flush()
        except Exception as e:                      # noqa: BLE001 - reported to the user
            stream.flush()
            logger.warning(f"Rebuild sweep failed: {e}", exc_info=True)
            err = str(e)
        self.finished.emit(err)


class RebuildAllWebsitesDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Rebuild all Albums and the Master website")
        self.resize(1000, 560)
        self._thread = None
        self._worker = None
        self._started = None
        self._tick = None

        # The sandgrouse on the left, as in Create Excel (his Firefly set,
        # 2026-10-21); everything else in a column beside it.
        from gui.wait_picture import picture_column
        lay = picture_column(self, "sandgrouse")
        lay.addWidget(HTMLLabel(
            "<p><b>Rebuild all Albums and the Master website</b></p>"
            "<p>The site is generated, so a fix to a page template or to the "
            "rendering code only reaches an album once that album is rebuilt. "
            "This re-renders and re-uploads them all, one after another.</p>"
            "<p>Which album is active is not changed. An album that fails is "
            "reported and the sweep carries on.</p>"))

        # Scope is two independent choices, not "albums, and optionally also the
        # master": rebuilding ONLY the master is a normal thing to want (it owns
        # tripindex-nl/en.html), and an "Also…" checkbox offers no way to say it.
        self.albums_cb = QCheckBox("Rebuild the albums")
        self.albums_cb.setChecked(True)
        self.master_cb = QCheckBox("Rebuild the master site")
        # svv6RedoBlog rewrites blog HTML only — setSkipJPGs(True),
        # uploadBlog(sftpPictures=False) — so it is the cheap half of a refresh,
        # not the expensive one. On by default because it is exactly the step
        # that re-links the blogs after a template change.
        self.pages_cb = QCheckBox(
            "…their species pages (uploads pictures — slow, cached after the first run)")
        self.pages_cb.setChecked(True)
        self.blogs_cb = QCheckBox(
            "…their blogs (HTML only — no pictures are moved)")
        self.blogs_cb.setChecked(True)
        lay.addWidget(self.albums_cb)
        lay.addWidget(self.pages_cb)
        lay.addWidget(self.blogs_cb)
        lay.addWidget(self.master_cb)
        for cb in (self.albums_cb, self.pages_cb, self.blogs_cb, self.master_cb):
            cb.toggled.connect(self._sync_enabled)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText(
            "Press “Show what would be rebuilt” to list the albums first.")
        self.log.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        lay.addWidget(self.log, 1)

        self.status = QLabel("")
        lay.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch(1)
        self.dry_btn = QPushButton("Show what would be rebuilt")
        self.run_btn = QPushButton("Rebuild all…")
        self.close_btn = QPushButton("Close")
        self.dry_btn.setDefault(True)
        row.addWidget(self.dry_btn)
        row.addWidget(self.run_btn)
        row.addWidget(self.close_btn)
        lay.addLayout(row)

        self.dry_btn.clicked.connect(lambda: self._start(False))
        self.run_btn.clicked.connect(self._confirm_and_run)
        self.close_btn.clicked.connect(self.close)

    def _confirm_and_run(self):
        parts = []
        if self.albums_cb.isChecked():
            bits = (["species pages"] if self.pages_cb.isChecked() else [])                  + (["blog HTML"] if self.blogs_cb.isChecked() else [])
            if bits:
                parts.append("every album's " + " and ".join(bits))
        if self.master_cb.isChecked():
            parts.append("the master site")
        what = " and ".join(parts)
        if QMessageBox.question(
                self, "Rebuild all Albums and the Master website",
                f"This re-renders {what} and uploads the result.\n\n"
                "It can take a long while. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._start(True)

    def _start(self, apply_it):
        if self._thread is not None:
            return                                  # already running
        self.log.clear()
        self._started = time.time()
        self._set_busy(True, "Rebuilding…" if apply_it else "Listing…")
        # A sweep runs for minutes with nothing but log lines; a ticking elapsed
        # time is how you tell "still working" from "wedged".
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._show_elapsed)
        self._tick.start(1000)

        self._thread = QThread()
        self._worker = _Worker(apply_it, self.blogs_cb.isChecked(),
                               self.master_cb.isChecked(),
                               self.albums_cb.isChecked(),
                               self.pages_cb.isChecked())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.line.connect(self._append)
        self._worker.finished.connect(lambda err, a=apply_it: self._done(err, a))
        self._thread.start()

    def _append(self, line):
        self.log.appendPlainText(line)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _show_elapsed(self):
        if self._started:
            self.status.setText(f"Rebuilding… {_hms(time.time() - self._started)} so far")

    def _done(self, err, applied):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._worker = None
        if self._tick is not None:
            self._tick.stop()
            self._tick = None
        took = _hms(time.time() - self._started) if self._started else "?"
        self._started = None
        self._set_busy(False, "")
        if err:
            self.status.setText(f"❌ {err}  (after {took})")
        elif applied:
            self.status.setText(
                f"✅ Rebuild finished in {took} — check the log above for any failures.")
        else:
            self.status.setText(f"Nothing was rebuilt — this was only a listing ({took}).")

    def _sync_enabled(self):
        """With neither scope ticked there is nothing to rebuild, so neither
        button should look as if it would do something."""
        albums_useful = self.albums_cb.isChecked() and (
            self.pages_cb.isChecked() or self.blogs_cb.isChecked())
        any_scope = albums_useful or self.master_cb.isChecked()
        for cb in (self.pages_cb, self.blogs_cb):
            cb.setEnabled(self.albums_cb.isChecked())
        self.dry_btn.setEnabled(any_scope)
        self.run_btn.setEnabled(any_scope)
        if not any_scope:
            self.status.setText(
                "Nothing selected — tick the master site, or the albums with at "
                "least one of their two parts.")
        elif not self.status.text().startswith(("✅", "❌")):
            self.status.setText("")

    def _set_busy(self, busy, msg):
        any_scope = (self.albums_cb.isChecked()
                     and (self.pages_cb.isChecked() or self.blogs_cb.isChecked())
                     ) or self.master_cb.isChecked()
        self.dry_btn.setEnabled(not busy and any_scope)
        self.run_btn.setEnabled(not busy and any_scope)
        self.status.setText(msg)
        self.setCursor(Qt.CursorShape.WaitCursor if busy else Qt.CursorShape.ArrowCursor)

    def closeEvent(self, e):
        if self._thread is not None:
            QMessageBox.information(self, "Rebuild in progress",
                                    "The rebuild is still running — let it finish first.")
            e.ignore()
            return
        super().closeEvent(e)
