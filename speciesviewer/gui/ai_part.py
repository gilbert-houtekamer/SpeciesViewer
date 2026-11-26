"""AI: name a part — what the AI calls the square you drew on a picture.

ONE JOB: show the part you framed in the large view, let BioCLIP name it (on a
worker thread: the first time loads the model) and let you pick one of its
names; the pick is returned, the host offers it in Set species ("AI: …").

INVARIANT: writes nothing — no crop, no sidecar, no cache. The name reaches a
picture only when you press Set species yourself.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout)

from gui.widgets import wrapped

logger = logging.getLogger("speciesviewer")


class _Namer(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, im, region, master_root, parent=None):
        super().__init__(parent)
        self.im, self.region, self.master_root = im, region, master_root

    def run(self):
        try:
            from engine import ai_measure
            self.done.emit(ai_measure.part_names(self.im, self.region,
                                                 self.master_root or None))
        except Exception as e:
            logger.error(f"AI name a part failed: {e}", exc_info=True)
            self.failed.emit(str(e))


def pil_from_pixmap(pm: QPixmap):
    """A PIL RGB image of a QPixmap (the part cut from the picture on screen)."""
    from PIL import Image
    from PyQt6.QtGui import QImage
    img = pm.toImage().convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    data = bytes(img.constBits().asarray(bpl * h))
    return Image.frombuffer("RGB", (w, h), data, "raw", "RGB", bpl, 1).copy()


class PartNamesDialog(QDialog):
    """The part you drew, and the AI's names for it; a click picks one."""

    def __init__(self, part: QPixmap, region: str, master_root: str,
                 thread_parent, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI: name a part")
        self.chosen: dict | None = None
        row = QHBoxLayout(self)
        pic = QLabel()
        pic.setPixmap(part.scaled(260, 260, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))
        pic.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(pic)
        self._col = QVBoxLayout()
        row.addLayout(self._col, 1)
        self._status = wrapped("The AI is looking at this part…"
                               + ("" if region else
                                  "  (No country known for this picture: it "
                                  "chooses among everything it knows.)"))
        self._col.addWidget(self._status)
        self._names = QVBoxLayout()
        self._col.addLayout(self._names)
        self._col.addStretch()
        bottom = QHBoxLayout()
        bottom.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        self._col.addLayout(bottom)
        self.setMinimumWidth(620)
        # Parented to the viewer, not to this window: closing the window while
        # the AI is still busy must not destroy a running thread.
        self._run = _Namer(pil_from_pixmap(part), region, master_root, thread_parent)
        self._run.done.connect(self._on_done)
        self._run.failed.connect(self._on_failed)
        self._run.start()

    def _on_failed(self, why: str):
        self._status.setText(f"The AI could not name this part: {why}")

    def _on_done(self, sections):
        self._status.setText("Click a name: Set species then offers it as the AI's "
                             "name for this picture. Nothing is written until you "
                             "press Set species.")
        for among, names in sections:
            head = wrapped(f"Among {among}:")
            head.setStyleSheet("font-weight: bold; margin-top: 6px;")
            self._names.addWidget(head)
            for n in names:
                self._names.addWidget(self._button(n))
        self.adjustSize()

    def _button(self, n: dict) -> QPushButton:
        name, sci = (n.get("name") or "").strip(), n["sci"].strip()
        text = f"{name} · {sci}" if name and name.lower() != sci.lower() else sci
        text += f"   {(n.get('prob') or 0) * 100:.0f} %"
        b = QPushButton(text)
        b.setStyleSheet("text-align: left; padding: 3px 8px;")
        known = n.get("how") in ("exact", "renamed")
        b.setToolTip("In your species list." if known else
                     "Not in your species list yet: Set species looks it up "
                     "online (GBIF) and adds it.")
        b.clicked.connect(lambda _=False, n=n: self._pick(n))
        return b

    def _pick(self, n: dict):
        self.chosen = dict(n)
        self.accept()
