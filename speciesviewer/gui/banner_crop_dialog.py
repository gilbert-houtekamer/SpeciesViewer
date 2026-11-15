"""Cut a web banner out of a picture: drag a 2000:270 band, see the result.

ONE JOB: let the person place and size the banner band on one picture and, on
"Use as banner", hand it to utils.album_banner.set_banner. Nothing is written
before that button.

Plain mouse drag (no Ctrl/Shift), the Size slider, and the arrow keys all move
or size the band, so it works without precise mouse control.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QMessageBox, QSlider, QVBoxLayout, QWidget)

from utils.album_banner import (BANNER_NAME, RATIO, banner_box, output_size,
                                render_banner, set_banner)

logger = logging.getLogger("speciesviewer")

VIEW_W, VIEW_H = 1100, 560      # the largest the picture is shown


def _to_pixmap(img) -> QPixmap:
    data = img.tobytes("raw", "RGB")
    q = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(q.copy())


class _BandView(QWidget):
    """The picture with the band on it; the outside is dimmed."""

    def __init__(self, img, on_change):
        super().__init__()
        self._img = img
        self._on_change = on_change
        w, h = img.size
        vw, vh = VIEW_W, VIEW_H
        from PyQt6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:          # leave room for the strip and buttons
            g = screen.availableGeometry()
            vw, vh = min(vw, int(g.width() * 0.85)), min(vh, int(g.height() * 0.5))
        scale = min(vw / w, vh / h, 1.0)
        self._pix = _to_pixmap(img.resize((max(1, int(w * scale)),
                                           max(1, int(h * scale)))))
        self.setFixedSize(self._pix.size())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.cx, self.cy, self.size_share = 0.5, 0.5, 1.0
        self._drag: QPoint | None = None

    def box(self):
        w, h = self._img.size
        return banner_box(w, h, self.cx, self.cy, self.size_share)

    def _view_rect(self) -> QRect:
        l, t, r, b = self.box()
        sx = self._pix.width() / self._img.size[0]
        sy = self._pix.height() / self._img.size[1]
        return QRect(int(l * sx), int(t * sy), int((r - l) * sx), int((b - t) * sy))

    def _settle(self):
        # Keep the centre where the band actually is (clamped at the edges), so
        # a drag past the edge doesn't leave the band "stuck" on the way back.
        l, t, r, b = self.box()
        w, h = self._img.size
        self.cx, self.cy = (l + r) / 2 / w, (t + b) / 2 / h
        self.update()
        self._on_change()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pix)
        band = self._view_rect()
        shade = QColor(0, 0, 0, 150)
        p.fillRect(QRect(0, 0, self.width(), band.top()), shade)
        p.fillRect(QRect(0, band.bottom() + 1, self.width(),
                         self.height() - band.bottom() - 1), shade)
        p.fillRect(QRect(0, band.top(), band.left(), band.height()), shade)
        p.fillRect(QRect(band.right() + 1, band.top(),
                         self.width() - band.right() - 1, band.height()), shade)
        p.setPen(QPen(QColor("#ffd54a"), 2))
        p.drawRect(band)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        pos = e.position().toPoint()
        d = pos - self._drag
        self._drag = pos
        self.cx += d.x() / self._pix.width()
        self.cy += d.y() / self._pix.height()
        self._settle()

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def keyPressEvent(self, e):
        step = 0.02
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                 Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}
        if e.key() in moves:
            dx, dy = moves[e.key()]
            self.cx += dx
            self.cy += dy
            self._settle()
        else:
            super().keyPressEvent(e)


class BannerCropDialog(QDialog):
    """img: the upright PIL picture; root: the trip/album folder."""

    def __init__(self, parent, img, source: Path, root, *, kind="album", owner="",
                 ini_name="virttrip.ini"):
        super().__init__(parent)
        self.setWindowTitle(f"Use as {kind} banner")
        self._img, self._root, self._ini = img, Path(root), ini_name
        self.written: Path | None = None
        lay = QVBoxLayout(self)
        intro = QLabel(
            f"Make the banner of {kind} <b>{owner or self._root.name}</b> from "
            f"<b>{Path(source).name}</b>. Drag the band (or use the arrow keys) and "
            f"set its size; the strip below is the banner.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self._view = _BandView(img, self._changed)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._view)
        row.addStretch()
        lay.addLayout(row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size:"))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        # In picture pixels: from the widest band the picture holds down to
        # exactly 2000 — cut 1:1, the sharpest banner (never narrower: that
        # would have to be enlarged).
        w, h = img.size
        widest = int(min(w, h * RATIO))
        self._slider.setRange(min(2000, widest), widest)
        self._slider.setValue(widest)
        self._slider.setSingleStep(20)
        self._slider.setPageStep(200)
        self._slider.setEnabled(widest > 2000)
        self._slider.setToolTip("How wide the banner band is. All the way left is "
                                "exactly 2000 pixels of the picture: no scaling.")
        self._slider.valueChanged.connect(self._on_size)
        size_row.addWidget(self._slider, 1)
        self._size_note = QLabel("")
        size_row.addWidget(self._size_note)
        lay.addLayout(size_row)

        self._preview = QLabel()
        self._preview.setFixedHeight(int(self._view.width() / 7.4) + 2)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._preview)

        note = QLabel(
            f"Saved as {BANNER_NAME} in the {kind} folder; the banner it replaces is "
            f"kept as a dated copy. The next Write All Blogs or Rebuild website "
            f"puts it on the site.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        lay.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Use as banner")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        # The full-resolution strip is re-rendered a moment after the last move.
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(120)
        self._redraw.timeout.connect(self._render_preview)
        self._changed()
        self._view.setFocus()
        # Wrapped labels as wide as the picture: their height is then known, so
        # the window's minimum height holds everything (no overlap).
        for lbl in (intro, note):
            lbl.setFixedWidth(self._view.width())
            lbl.setMinimumHeight(lbl.heightForWidth(self._view.width()))
        lay.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        self.adjustSize()

    def _on_size(self, v):
        self._view.size_share = v / self._img.size[0]
        self._view._settle()

    def _changed(self):
        w, h = output_size(self._view.box())
        bw = self._view.box()[2] - self._view.box()[0]
        if w < 2000:
            tail = "  — the picture is smaller than 2000 wide, so it is not enlarged"
        elif bw <= 2000:
            tail = "  — 1:1, not scaled"
        else:
            tail = f"  — from {bw} px of the picture"
        self._size_note.setText(f"{w}×{h}{tail}")
        self._redraw.start()

    def _render_preview(self):
        strip = render_banner(self._img, self._view.box())
        pix = _to_pixmap(strip).scaledToWidth(
            self._view.width(), Qt.TransformationMode.SmoothTransformation)
        self._preview.setPixmap(pix)

    def _save(self):
        try:
            self.written = set_banner(self._img, self._view.box(), self._root,
                                      ini_name=self._ini)
        except OSError as e:
            logger.warning(f"banner: {e}")
            QMessageBox.warning(self, "Banner", f"The banner was not saved:\n\n{e}")
            return
        self.accept()
