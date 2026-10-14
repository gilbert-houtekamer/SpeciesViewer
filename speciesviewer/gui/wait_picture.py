"""A picture beside the text of a long "Please wait…" window.

Only for the waits that really take a while (the master merge, Retire Trip):
a drawing to look at instead of a bare sentence. Pictures live in assets/ as
wait_<name>.jpg; a missing picture just means the plain window.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
SIDE = 180          # the picture's edge on screen, in pixels


def wait_layout(dialog: QDialog, picture: str | None,
                width: int, height: int) -> QVBoxLayout:
    """Lay out `dialog` and return the column its text and spinner go into.
    With a picture, the picture sits on the left and the window grows to fit;
    without one (or if the file is missing), the column is the whole window."""
    pix = QPixmap(str(_ASSETS / f"wait_{picture}.jpg")) if picture else QPixmap()
    if pix.isNull():
        dialog.setFixedSize(width, height)
        return QVBoxLayout(dialog)
    dpr = dialog.devicePixelRatioF()
    pix = pix.scaled(int(SIDE * dpr), int(SIDE * dpr),
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    pix.setDevicePixelRatio(dpr)
    image = QLabel()
    image.setPixmap(pix)
    image.setFixedSize(SIDE, SIDE)
    row = QHBoxLayout(dialog)
    row.setContentsMargins(16, 16, 16, 16)
    row.setSpacing(16)
    row.addWidget(image, alignment=Qt.AlignmentFlag.AlignVCenter)
    column = QVBoxLayout()
    row.addLayout(column, 1)
    dialog.setFixedSize(width + SIDE + 16, max(height, SIDE + 32))
    return column
