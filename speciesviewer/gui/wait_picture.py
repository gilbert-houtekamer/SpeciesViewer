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
    # full width, text aligned inside — with an addWidget alignment a
    # word-wrapped label kept its own narrow width (2026-10-18)
    text.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    column.addWidget(text)
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


def picture_text(picture: str, text: str, side: int = 120) -> str:
    """Rich text for a QProgressDialog label: the picture beside `text` (the
    progress dialog has no picture slot of its own). Plain `text` when the
    picture is missing."""
    path = _ASSETS / f"wait_{picture}.jpg"
    if not path.is_file():
        return text
    import html
    return (f'<table><tr><td><img src="{path.as_posix()}" width="{side}" height="{side}"></td>'
            f'<td style="padding-left:12px; vertical-align:middle">{html.escape(text)}</td></tr></table>')


from contextlib import contextmanager


@contextmanager
def waiting_box(parent, message: str, picture: str | None):
    """A wait box (picture + message) for a SHORT job that must stay on the GUI
    thread — one touching the master's SQLite connection, which refuses other
    threads. Drawn before the job starts, closed when it ends; it does not
    repaint in between, so keep it for waits of a few seconds (Add new files)."""
    from PyQt6.QtWidgets import QApplication
    box = QDialog(parent)
    box.setWindowTitle("Please wait…")
    box.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    column = wait_layout(box, picture, 300, 110)
    text = QLabel(message)
    text.setWordWrap(True)
    # full width, text aligned inside — with an addWidget alignment a
    # word-wrapped label kept its own narrow width (2026-10-18)
    text.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    column.addWidget(text)
    box.show()
    for _ in range(3):                  # let the window paint before the work
        QApplication.processEvents()
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield box
    finally:
        QApplication.restoreOverrideCursor()
        box.close()
        box.deleteLater()
