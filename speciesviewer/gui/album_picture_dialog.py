"""Confirm setting an album's cover picture — showing the crop before writing it.

A 3:2 photograph loses about 12% off each side becoming 4:3, and a bird is
rarely centred, so the interesting question is not "which picture" but "does it
survive the crop". Showing the actual 600x450 that will be written answers that
in a glance, which is cheaper than writing it and looking afterwards.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QDialogButtonBox, QMessageBox,
)

from utils.album_picture import (
    ALBUM_PICTURE_SIZE, is_seeded_default, render_cover, set_album_picture,
    target_path,
)

logger = logging.getLogger("speciesviewer")


def _pil_to_pixmap(img) -> QPixmap:
    data = img.convert("RGB").tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())      # copy: the buffer is temporary


class AlbumPictureDialog(QDialog):
    def __init__(self, parent, source, album_root, album_name=""):
        super().__init__(parent)
        self.setWindowTitle("Use as album picture")
        self._source = Path(source)
        self._album_root = Path(album_root)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        name = album_name or self._album_root.name
        root.addWidget(QLabel(
            f"Use <b>{self._source.name}</b> as the picture for album <b>{name}</b>."))

        try:
            cover = render_cover(self._source)
        except Exception as e:
            logger.warning(f"album picture: could not render {self._source}: {e}")
            QMessageBox.warning(parent, "Use as album picture",
                                f"Could not read that picture:\n\n{e}")
            self._failed = True
            return
        self._failed = False

        shot = QLabel()
        shot.setPixmap(_pil_to_pixmap(cover))
        shot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shot.setStyleSheet("border: 1px solid #bbb;")
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(shot)
        row.addStretch()
        root.addLayout(row)

        w, h = ALBUM_PICTURE_SIZE
        note = QLabel(
            f"This is exactly what will be written: centre-cropped to 4:3 and "
            f"resized to {w}×{h}. If the bird sits near an edge, crop the picture "
            f"first and try again.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(note)

        # Replacing the shipped placeholder is nothing; replacing a picture the
        # user chose (or made by hand) deserves to be said out loud.
        if not is_seeded_default(self._album_root):
            warn = QLabel(
                f"⚠ <b>{target_path(self._album_root).name}</b> is not the default "
                f"placeholder — it will be replaced. The current one is kept as "
                f"<i>tripimage.jpg.previous</i>.")
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b8860b; font-size: 11px;")
            root.addWidget(warn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Use this picture")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self):
        try:
            written = set_album_picture(self._source, self._album_root)
        except Exception as e:
            logger.warning(f"album picture: write failed: {e}")
            QMessageBox.warning(self, "Use as album picture",
                                f"Could not write the album picture:\n\n{e}")
            return
        self.written = written
        self.accept()
