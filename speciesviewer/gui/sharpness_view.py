"""Sharpness — one picture the way the picture score sees it (the Editor's 4th tab).

ONE JOB: show where the score looked and what it found, so you can judge it and
learn from it:

  - the whole frame, with the sharpest tiles (cyan) the score averaged, the
    camera's AF point (yellow, OM System only) and the SPOT (orange) — the
    subject area the 100 % block comes from;
  - that spot at 100 % of the picture's own size (the camera's embedded JPEG:
    full size on the Nikons, 3200 px on the OM-1) — the pixel level where
    "equally sharp" frames differ;
  - the numbers: subject sharpness at 1600 px, the pixel-level sharpness on
    the spot, and the AI… verdict when that dialog is open.

"Keep this spot" holds the spot while you step through a burst, so every frame
is compared on the same part of the scene — the way the suggestion itself
compares the frames of one pose.

INVARIANT: read-only. Nothing is written except the score cache in app-data.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from gui import theme as _th

logger = logging.getLogger("speciesviewer")

CROP_PX = 600          # the 100 % block, in picture pixels


class _Load(QThread):
    done = pyqtSignal(int, object)          # generation, result dict | None

    def __init__(self, gen: int, src: Path, parent=None):
        super().__init__(parent)
        self._gen, self._src = gen, src

    def run(self):
        try:
            from engine import af_point
            from engine.picture_score import Cache, image_bytes, score_image
            data = image_bytes(self._src)
            if not data:
                self.done.emit(self._gen, None)
                return
            # Upright, like the score's own decode (OpenCV applies the picture's
            # orientation) — otherwise the boxes land wrong on a portrait frame.
            from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
            from PyQt6.QtGui import QImageReader
            buf = QBuffer()
            buf.setData(QByteArray(data))
            buf.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buf)
            reader.setAutoTransform(True)
            img = reader.read()
            cache = Cache()
            try:
                sc = cache.get(self._src)
                if sc is None:
                    sc = score_image(data)
                    if sc:
                        cache.put(self._src, sc)
            finally:
                cache.close()
            af = af_point.lookup(af_point.read([self._src]), self._src)
            self.done.emit(self._gen, {"image": img, "score": sc, "af": af})
        except Exception as e:                       # shown, never fatal
            logger.warning(f"Sharpness tab: {self._src}: {e}", exc_info=True)
            self.done.emit(self._gen, {"error": str(e)})


class _Frame(QWidget):
    """The whole picture, fitted, with the boxes drawn over it. A click puts
    the spot where you clicked (fractions of the picture)."""
    clicked = pyqtSignal(float, float)

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip("Click the bird to see that spot at 100 %")
        self._shown = None           # (x0, y0, w, h) of the picture on screen
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 150)
        self.image: QImage | None = None
        self.tiles: list = []        # [(x, y, w, h)] fractions
        self.af = None               # (x0, y0, x1, y1)
        self.spot = None             # (x0, y0, x1, y1)
        self.show_tiles = True
        self.show_af = True
        self.verdict = ""            # the AI… suggestion: "purple" | "keep" | ""
        self.yours = False           # you decided it (k / b): drawn dashed

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(_th.SURFACE))
        if self.image is None or self.image.isNull():
            return
        iw, ih = self.image.width(), self.image.height()
        s = min(self.width() / iw, self.height() / ih)
        w, h = iw * s, ih * s
        x0, y0 = (self.width() - w) / 2, (self.height() - h) / 2
        self._shown = (x0, y0, w, h)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(x0, y0, w, h), self.image)

        def box(b, colour, width, dash=False):
            pen = QPen(QColor(colour), width)
            if dash:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(x0 + b[0] * w, y0 + b[1] * h,
                              (b[2] - b[0]) * w, (b[3] - b[1]) * h))

        if self.show_tiles:
            for t in self.tiles:
                box((t[0], t[1], t[0] + t[2], t[1] + t[3]), "#00e5ff", 1.5)
        if self.spot:
            box(self.spot, "#ff6d00", 2.5)
        if self.show_af and self.af:
            box(self.af, "#ffeb3b", 2.5)
        # The AI… verdict as on the grid's cards: a purple frame with a bin on a
        # picture that could go, a green frame on one that is kept; dashed = yours.
        if self.verdict in ("purple", "keep"):
            colour = "#9370DB" if self.verdict == "purple" else "#4caf50"
            pen = QPen(QColor(colour), 6)
            if self.yours:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRect(QRectF(x0 + 3, y0 + 3, w - 6, h - 6))
            if self.verdict == "purple":
                font = p.font()
                font.setPixelSize(30)
                p.setFont(font)
                p.setPen(QColor("#e8dcff"))
                p.drawText(QRectF(x0, y0 + h - 48, w, 42),
                           int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom), "🗑")
        p.end()


    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self._shown:
            return super().mousePressEvent(e)
        x0, y0, w, h = self._shown
        fx, fy = (e.position().x() - x0) / w, (e.position().y() - y0) / h
        if 0 <= fx <= 1 and 0 <= fy <= 1:
            self.clicked.emit(fx, fy)


class SharpnessView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._gen = 0
        self._threads: list[_Load] = []
        self._result = None
        self._suggestion = ""
        self._yours = False
        self._held_spot = None       # (cx, cy) fractions, while "Keep this spot" is on

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self._frame = _Frame()
        self._frame.clicked.connect(self._on_frame_clicked)
        self._clicked_spot = None    # (cx, cy): where you clicked, for this picture
        lay.addWidget(self._frame, 3)

        side = QVBoxLayout()
        self._crop = QLabel()
        self._crop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._crop.setMinimumSize(220, 220)
        self._crop.setStyleSheet(f"background:{_th.SURFACE}; border:2px solid #ff6d00;")
        side.addWidget(self._crop, 1)
        self._keep = QCheckBox("Keep this spot while stepping")
        self._keep.setToolTip(
            "Hold the 100 % block on the same part of the picture while you go to the "
            "next frame — so the frames of a burst are compared on the same spot, the "
            "way the suggestion compares them")
        self._keep.toggled.connect(self._on_keep_toggled)
        side.addWidget(self._keep)
        row = QHBoxLayout()
        self._cb_tiles = QCheckBox("Sharpest tiles")
        self._cb_tiles.setChecked(True)
        self._cb_tiles.toggled.connect(self._toggle_tiles)
        self._cb_af = QCheckBox("AF point")
        self._cb_af.setChecked(True)
        self._cb_af.toggled.connect(self._toggle_af)
        row.addWidget(self._cb_tiles)
        row.addWidget(self._cb_af)
        row.addStretch(1)
        side.addLayout(row)
        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setTextFormat(Qt.TextFormat.RichText)
        side.addWidget(self._info)
        legend = QLabel(
            "<span style='color:#00e5ff'>■</span> sharpest tiles (the score) &nbsp; "
            "<span style='color:#ff6d00'>■</span> the spot, at 100 % above &nbsp; "
            "<span style='color:#ffeb3b'>■</span> camera AF point")
        legend.setWordWrap(True)
        legend.setStyleSheet(f"color:{_th.INK_FAINT}")
        side.addWidget(legend)
        holder = QWidget()
        holder.setLayout(side)
        holder.setMaximumWidth(460)
        lay.addWidget(holder, 2)

    # ── loading ─────────────────────────────────────────────────────────────

    def set_verdict(self, suggestion: str = "", yours: bool = False) -> None:
        """The AI… verdict for the picture on screen — redrawn, not re-measured."""
        self._suggestion, self._yours = suggestion or "", bool(yours)
        self._frame.verdict, self._frame.yours = self._suggestion, self._yours
        self._frame.update()
        if self._result:
            self._render()

    def set_picture(self, src: Path | None, suggestion: str = "", yours: bool = False) -> None:
        """Show `src` (a RAW, HEIC or JPG). `suggestion`: the AI… verdict, if any."""
        self._gen += 1
        self._clicked_spot = None    # a new picture: the spot follows it again
        self._suggestion, self._yours = suggestion or "", bool(yours)
        self._frame.verdict, self._frame.yours = self._suggestion, self._yours
        self._result = None
        self._frame.image = None
        self._frame.update()
        self._crop.setPixmap(QPixmap())
        if src is None:
            self._crop.setText("")
            self._info.setText("No picture file to measure.")
            return
        self._crop.setText("Measuring…")
        self._info.setText("")
        t = _Load(self._gen, Path(src), self)
        t.done.connect(self._on_loaded)
        t.finished.connect(lambda t=t: self._threads.remove(t) if t in self._threads else None)
        self._threads.append(t)
        t.start()

    def _on_loaded(self, gen: int, res):
        if gen != self._gen:
            return                                   # you moved on
        if not res or "error" in res:
            self._crop.setText("")
            self._info.setText("This picture could not be read"
                               + (f": {res['error']}" if res and res.get("error") else "."))
            return
        self._result = res
        self._render()

    # ── drawing ─────────────────────────────────────────────────────────────

    def _render(self):
        import numpy as np
        from engine.picture_score import TOP_SHARE, fine_in, subject_box
        res = self._result
        img, sc = res["image"], res["score"] or {}
        tiles = sc.get("tiles")
        f = self._frame
        f.image = img
        f.tiles = []
        if tiles is not None:
            rows, cols = tiles.shape
            k = max(3, int(tiles.size * TOP_SHARE))
            for i in np.argsort(tiles.ravel())[-k:]:
                f.tiles.append((int(i % cols) / cols, int(i // cols) / rows, 1 / cols, 1 / rows))
        f.af = res["af"][0] if res.get("af") else None

        # The spot: where you held it, else the subject area of this picture.
        box = subject_box(tiles)
        if self._clicked_spot is not None:
            cx, cy = self._clicked_spot
        elif self._keep.isChecked() and self._held_spot is not None:
            cx, cy = self._held_spot
        elif box is not None:
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        else:
            cx, cy = 0.5, 0.5
        iw, ih = img.width(), img.height()
        cw, ch = min(CROP_PX, iw), min(CROP_PX, ih)
        x = int(min(max(0, cx * iw - cw / 2), iw - cw))
        y = int(min(max(0, cy * ih - ch / 2), ih - ch))
        spot = (x / iw, y / ih, (x + cw) / iw, (y + ch) / ih)
        f.spot = spot
        f.update()

        crop = QPixmap.fromImage(img.copy(x, y, cw, ch))
        crop.setDevicePixelRatio(self.devicePixelRatioF())   # true 100 %: 1 picture px = 1 screen px
        self._crop.setText("")
        self._crop.setPixmap(crop)

        fine = fine_in(sc.get("fine"), spot)
        af_box, found = res.get("af") or (None, None)
        af_txt = ("no AF point recorded" if af_box is None else
                  "AF: the camera found a subject" if found else
                  "AF: no subject found" if found is False else "AF point")
        mine = " (your choice)" if self._yours else ""
        verdict = {"purple": f"<b style='color:#b39ddb'>AI…: could go{mine}</b> · ",
                   "keep": f"<b style='color:#81c784'>AI…: kept{mine}</b> · "
                   }.get(self._suggestion, "")
        text = f"{verdict}Subject sharpness <b>{sc.get('sharp', 0):.0f}</b> (at 1600 px)"
        if fine is not None:
            where = ("your spot" if self._clicked_spot is not None
                     else "the held spot" if self._keep.isChecked() and self._held_spot
                     else "the spot")
            text += (f" · on {where} <b>{fine:.0f}</b> at the picture's own size "
                     f"({iw}×{ih})")
        text += (f"<br>{af_txt}. Compare the numbers between frames of one burst with "
                 "“Keep this spot” on; on their own they mean little.")
        self._info.setText(text)

    def _on_frame_clicked(self, fx: float, fy: float):
        """You point at the bird: the 100 % block and its number go there. With
        "Keep this spot" on, the next frames are shown on this spot too."""
        self._clicked_spot = (fx, fy)
        if self._keep.isChecked():
            self._held_spot = (fx, fy)
        if self._result:
            self._render()

    def _on_keep_toggled(self, on: bool):
        if on and self._frame.spot:
            s = self._frame.spot
            self._held_spot = ((s[0] + s[2]) / 2, (s[1] + s[3]) / 2)
        elif not on:
            self._held_spot = None
            if self._result:
                self._render()

    def _toggle_tiles(self, on):
        self._frame.show_tiles = on
        self._frame.update()

    def _toggle_af(self, on):
        self._frame.show_af = on
        self._frame.update()

    def stop(self):
        """Before the Editor closes: let a running measurement finish quietly."""
        self._gen += 1
        for t in list(self._threads):
            t.wait(10000)
