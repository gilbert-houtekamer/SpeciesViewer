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


def picture_label(picture: str, side: int, dpr: float = 1.0) -> QLabel | None:
    """A QLabel showing assets/wait_<picture>.jpg at `side` pixels, or None
    when the file is missing — for a window that is not a wait box but is
    open while a long job runs (Read SD-card or Folder)."""
    pix = QPixmap(str(_ASSETS / f"wait_{picture}.jpg"))
    if pix.isNull():
        return None
    pix = pix.scaled(int(side * dpr), int(side * dpr),
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    pix.setDevicePixelRatio(dpr)
    label = QLabel()
    label.setPixmap(pix)
    label.setFixedSize(side, side)
    return label


def run_waiting(parent, message: str, work, picture: str | None = "macaws"):
    """Run work() OFF the GUI thread while a small wait box (picture + message)
    stays on screen and responsive; return work()'s result, or re-raise its error.

    For the short-but-noticeable waits that used to run on the GUI thread — a
    folder delete, a website listing — where Windows showed "(Not Responding)"
    because nothing repainted. work() must not touch Qt: do the engine part in
    it and any GUI follow-up after it returns."""
    import threading
    from PyQt6.QtCore import QEventLoop, QTimer
    box = QDialog(parent)
    box.setWindowTitle("Please wait…")
    box.setModal(True)
    box.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    column = wait_layout(box, picture, 360, 120)
    text = QLabel(message)
    text.setWordWrap(True)
    column.addWidget(text, alignment=Qt.AlignmentFlag.AlignVCenter)
    result = {}

    def run():
        try:
            result["value"] = work()
        except BaseException as e:          # handed back to the GUI thread below
            result["error"] = e

    worker = threading.Thread(target=run, daemon=True)
    box.show()
    worker.start()
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: None if worker.is_alive() else loop.quit())
    timer.start(100)
    loop.exec()
    timer.stop()
    box.close()
    box.deleteLater()
    if "error" in result:
        raise result["error"]
    return result.get("value")


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
