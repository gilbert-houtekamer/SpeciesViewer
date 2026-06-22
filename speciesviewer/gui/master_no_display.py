# master_no_display.py: GUI for "Species without a website picture" — runs the
# master scan (engine.master_no_display), shows the classified report, and opens
# the SHOWABLE species (those that still have a file on disk) in the card viewer
# so they can be seen and decided on. RAW-only species render straight from the
# RAW preview; orphans (no files) are listed but can't be shown.

import os
import logging
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMessageBox, QDialog, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QPushButton,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from gui.preview_provider import FileListPreviewCollection, FilePreviewItem
import engine.master_no_display as mnd

logger = logging.getLogger("speciesviewer")

_STATUS_SHORT = {"stale_counter": "stale counter", "raw_only": "RAW only", "orphan": "orphan"}


class _LabeledItem(FilePreviewItem):
    """A file-list card captioned with the species name instead of the filename."""

    @property
    def display_name(self) -> str:
        return self._c.label_for(self._key)

    @property
    def species(self) -> str:
        return self._c.label_for(self._key)


class LabeledFileCollection(FileListPreviewCollection):
    """File-list collection whose cards are captioned with the species name, and
    that supports the viewer's Find boxes (find by species or by filename)."""

    def __init__(self, pairs, **kw):
        super().__init__([p for p, _ in pairs], **kw)
        self._labels = {str(Path(p)): lbl for p, lbl in pairs}

    def label_for(self, key: str) -> str:
        return self._labels.get(str(Path(key)), Path(key).name)

    def item(self, index: int) -> _LabeledItem:
        return _LabeledItem(self, self._order[index])

    def find_keys(self, query: str) -> list[str]:
        q = (query or "").strip().lower()
        if not q:
            return self.all_keys()
        return [k for k in self.all_keys() if q in self.label_for(k).lower()]

    def file_keys(self, query: str) -> list[str]:
        q = (query or "").strip().lower()
        if not q:
            return self.all_keys()
        return [k for k in self.all_keys() if q in k.lower()]


def show(main_window, config):
    master = (config.get("master", "masterbiotadatabase", fallback="")
              or "").replace('"', "").replace("'", "").strip()
    if not master:
        QMessageBox.warning(main_window, "Species without a website picture",
                            "No master is configured.")
        return
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        rep = mnd.scan(master)
    finally:
        QApplication.restoreOverrideCursor()
    if not rep.get("ok"):
        QMessageBox.warning(main_window, "Species without a website picture",
                            rep.get("error", "Failed."))
        return

    text = mnd.format_report(rep)
    out = os.path.join(master, "admin", "status", "species_no_display.txt")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        text += f"\n\nReport written to:\n{out}"
    except Exception:
        pass
    showable = [e for e in rep["entries"] if e["rep_path"]]

    dlg = QDialog(main_window)
    dlg.setWindowTitle("Species without a website picture (dry-run, no changes)")
    dlg.resize(820, 560)
    lay = QVBoxLayout(dlg)
    view = QPlainTextEdit(text, readOnly=True)
    view.setFont(QFont("Consolas", 9))
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    lay.addWidget(view)

    row = QHBoxLayout()
    view_btn = QPushButton(f"View {len(showable)} showable species…")
    view_btn.setEnabled(bool(showable))
    view_btn.setToolTip("Open the species that still have a file on disk in a card "
                        "viewer (RAW preview). Orphans have no file to show.")
    view_btn.clicked.connect(lambda: _open_showable_viewer(main_window, showable))
    close_btn = QPushButton("Close")
    close_btn.clicked.connect(dlg.accept)
    row.addWidget(view_btn)
    row.addStretch(1)
    row.addWidget(close_btn)
    lay.addLayout(row)

    main_window._no_display_report = dlg   # keep a reference (non-modal)
    dlg.show()


def _open_showable_viewer(main_window, showable):
    from gui.image_viewer import ImagePreviewDialog
    pairs = []
    for e in showable:
        tag = _STATUS_SHORT.get(e["status"], e["status"])
        pairs.append((e["rep_path"], f"{e['species']}  ·  {tag}"))
    cache_root = Path(tempfile.gettempdir()) / "sv_no_display_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    coll = LabeledFileCollection(pairs, cache_root=cache_root)
    dlg = ImagePreviewDialog(main_window, image_dir="", mode="all",
                             cache_root=cache_root, collection=coll,
                             source_label="Master — species without a website picture")
    main_window._no_display_viewer = dlg   # keep a reference
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
