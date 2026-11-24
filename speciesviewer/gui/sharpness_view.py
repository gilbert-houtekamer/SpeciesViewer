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
    the spot, and the AI… verdict when that dialog is open;
  - with the optional AI models installed (engine.ai_models): the animal box
    (green) and three bars — sharp ON the animal (against your own master
    pictures from the same camera), the animal's size, and the species
    confidence — with the top 3 species in words (engine.ai_measure).

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
NOT_SURE_PCT = 45      # engine.ai_measure.NOT_SURE, as a percentage


class _Load(QThread):
    done = pyqtSignal(int, object)          # generation, result dict | None
    ai = pyqtSignal(int, object)            # generation, engine.ai_measure result | {"error"}

    def __init__(self, gen: int, src: Path, parent=None, alive=None, region: str = ""):
        super().__init__(parent)
        self._gen, self._src, self._region = gen, src, region
        self._alive = alive or (lambda: True)     # False once you moved on

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
            af = af_point.lookup_info(af_point.read([self._src]), self._src)
            self.done.emit(self._gen, {"image": img, "score": sc, "af": af})
        except Exception as e:                       # shown, never fatal
            logger.warning(f"Sharpness tab: {self._src}: {e}", exc_info=True)
            self.done.emit(self._gen, {"error": str(e)})
            return
        if self._alive():                    # scrolled past: no AI work for it
            self._run_ai(data, sc, af)

    def _run_ai(self, data: bytes, sc, af):
        """The AI's view, after the picture is on screen (models load once)."""
        try:
            from engine import ai_models
            if not ai_models.available():
                self.ai.emit(self._gen, {"missing": True})
                return
            import io
            from PIL import Image, ImageOps
            from engine import ai_measure, ai_yardstick
            from utils.config import _config
            im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
            master = _config.get("master", "masterbiotadatabase",
                                 fallback="").strip().strip('"')
            res = ai_measure.measure(self._src, im, sc, (af or {}).get("model", ""),
                                     native=(af or {}).get("native") or 0,
                                     region=self._region, master_root=master or None,
                                     yardstick=ai_yardstick.get(master))
            self.ai.emit(self._gen, res)
        except Exception as e:                       # shown, never fatal
            logger.warning(f"Sharpness tab, AI: {self._src}: {e}", exc_info=True)
            self.ai.emit(self._gen, {"error": str(e)})


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
        self.af_focus: list = []     # Nikon D500: the other points that were in focus
        self.spot = None             # (x0, y0, x1, y1)
        self.show_tiles = True
        self.show_af = True
        self.verdict = ""            # the AI… suggestion: "purple" | "keep" | ""
        self.animal = None           # (x0, y0, x1, y1): the AI's animal box
        self.show_animal = True
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
        if self.show_animal and self.animal:
            box(self.animal, "#66bb6a", 2.5)
        if self.show_af:
            for b in self.af_focus:
                box(b, "#ffeb3b", 1.5, dash=True)
            if self.af:
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


class _Bar(QWidget):
    """One 0–100 % bar."""

    def __init__(self, colour: str):
        super().__init__()
        self._colour = colour
        self.value = None
        self.setFixedHeight(12)
        self.setMinimumWidth(80)

    def set(self, value):
        self.value = value
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect().adjusted(0, 2, -1, -2)
        p.setPen(QColor(_th.LINE))
        p.setBrush(QColor(_th.SURFACE_2))
        p.drawRect(r)
        if self.value is not None:
            w = int(r.width() * max(0.0, min(100.0, self.value)) / 100)
            p.fillRect(r.x() + 1, r.y() + 1, max(0, w - 1), r.height() - 1, QColor(self._colour))
        p.end()


class _AiPanel(QWidget):
    """The AI's view of the picture: three bars and the top 3 species."""
    ROWS = (("sharp", "Sharp on the animal", "#3fa34d",
             "Sharpness inside the green box, against your own master pictures from "
             "this camera: sharper on the animal than this % of the pictures you kept"),
            ("size", "Size", "#3d7fc4",
             "The animal's long side at the picture's own size: 100 px = 0 %, "
             "2500 px = 100 %"),
            ("species", "Species", "#c9a227",
             "How sure the AI is of its first name below"))

    def __init__(self):
        super().__init__()
        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 6, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        self._bars, self._vals = {}, {}
        for i, (key, text, colour, tip) in enumerate(self.ROWS):
            lab = QLabel(text)
            lab.setToolTip(tip)
            bar = _Bar(colour)
            bar.setToolTip(tip)
            val = QLabel("")
            val.setMinimumWidth(70)
            grid.addWidget(lab, i, 0)
            grid.addWidget(bar, i, 1)
            grid.addWidget(val, i, 2)
            self._bars[key], self._vals[key] = bar, val
        grid.setColumnStretch(1, 1)
        self._names = QLabel("")
        self._names.setWordWrap(True)
        self._names.setTextFormat(Qt.TextFormat.RichText)
        self._names.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self._names, len(self.ROWS), 0, 1, 3)
        self.clear("")

    def clear(self, note: str):
        for k in self._bars:
            self._bars[k].set(None)
            self._vals[k].setText("")
        self._names.setText(f"<span style='color:{_th.INK_FAINT}'>{note}</span>" if note else "")

    def show_result(self, r: dict):
        if r.get("missing"):
            self.clear("Species suggestions: the AI models are not installed on this "
                       "computer.")
            return
        if r.get("error"):
            self.clear(f"The AI could not look at this picture: {r['error']}")
            return
        if not r.get("box"):
            for k in self._bars:
                self._bars[k].set(0)
            self._vals["sharp"].setText("no animal")
            self._vals["size"].setText("")
            self._vals["species"].setText("")
            self._names.setText(f"<span style='color:{_th.INK_FAINT}'>The AI found no animal "
                                "in this picture — often a sign it is out of focus.</span>")
            return
        sp = r.get("sharp_pct")
        self._bars["sharp"].set(sp)
        self._vals["sharp"].setText(f"{sp:.0f} %" if sp is not None else "no yardstick")
        self._bars["size"].set(r.get("size_pct"))
        self._vals["size"].setText(f"{r.get('px', 0)} px")
        names = r.get("names") or []
        first = names[0]["prob"] * 100 if names else None
        self._bars["species"].set(first)
        self._vals["species"].setText(f"{first:.0f} %" if first is not None else "")
        rows = []
        for n in names:
            note = ("" if n.get("how") == "exact" else
                    " <span style='color:%s'>(another name in your list)</span>" % _th.INK_FAINT
                    if n.get("how") == "renamed" else
                    " <span style='color:%s'>(not in your list)</span>" % _th.INK_FAINT)
            rows.append(f"<b>{n['prob'] * 100:.0f} %</b> &nbsp;{n.get('name') or ''} "
                        f"<i>{n['sci']}</i>{note}")
        if names and first < NOT_SURE_PCT:
            rows.insert(0, f"<span style='color:{_th.INK_FAINT}'>Not sure — the first "
                           f"name is below {NOT_SURE_PCT} %</span>")
        if r.get("among"):
            rows.append(f"<span style='color:{_th.INK_FAINT}'>Chosen from "
                        f"{r['among']}</span>")
        self._names.setText("<br>".join(rows))


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
        self._ai_res: dict = {}      # the AI's view of the picture on screen
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self._frame, 1)
        # Under the picture: per colour, what it marks and what was found there.
        self._legend = QLabel("")
        self._legend.setWordWrap(True)
        self._legend.setTextFormat(Qt.TextFormat.RichText)
        self._legend.setToolTip(
            "The sharpness numbers mean little on their own: compare them between the "
            "frames of one burst, with “Keep this spot while stepping” on")
        left.addWidget(self._legend)
        left_w = QWidget()
        left_w.setLayout(left)
        lay.addWidget(left_w, 3)

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
        self._cb_animal = QCheckBox("Animal")
        self._cb_animal.setChecked(True)
        self._cb_animal.setToolTip("The box where the AI found the animal")
        self._cb_animal.toggled.connect(self._toggle_animal)
        row.addWidget(self._cb_tiles)
        row.addWidget(self._cb_af)
        row.addWidget(self._cb_animal)
        row.addStretch(1)
        side.addLayout(row)
        self._ai = _AiPanel()
        side.addWidget(self._ai)
        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setTextFormat(Qt.TextFormat.RichText)
        side.addWidget(self._info)
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

    def set_picture(self, src: Path | None, suggestion: str = "", yours: bool = False,
                    region: str = "") -> None:
        """Show `src` (a RAW, HEIC or JPG). `suggestion`: the AI… verdict, if any;
        `region`: the picture's country (ISO code) — the AI names from eBird's list there."""
        self._gen += 1
        self._clicked_spot = None    # a new picture: the spot follows it again
        self._suggestion, self._yours = suggestion or "", bool(yours)
        self._frame.verdict, self._frame.yours = self._suggestion, self._yours
        self._result = None
        self._ai_res = {}
        self._legend.setText("")
        self._frame.image = None
        self._frame.animal = None
        self._frame.update()
        self._crop.setPixmap(QPixmap())
        if src is None:
            self._crop.setText("")
            self._info.setText("No picture file to measure.")
            return
        self._crop.setText("Measuring…")
        self._info.setText("")
        self._ai.clear("The AI is looking…")
        gen = self._gen
        t = _Load(gen, Path(src), self, alive=lambda: self._gen == gen, region=region)
        t.done.connect(self._on_loaded)
        t.ai.connect(self._on_ai)
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

    def _on_ai(self, gen: int, res):
        if gen != self._gen:
            return                                   # you moved on
        res = res or {}
        self._ai_res = res
        self._frame.animal = res.get("box")
        self._ai.show_result(res)
        if self._result:
            self._render()           # the spot moves to the animal
        else:
            self._frame.update()

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
        afi = res.get("af") or {}
        f.af = afi.get("box")
        f.af_focus = list(afi.get("in_focus") or [])

        # The spot: where you clicked or held it, else the animal the AI found
        # (sharp grass in front of a soft bird is no sharp picture), else the
        # subject area of the sharpest tiles.
        box = subject_box(tiles)
        if self._clicked_spot is not None:
            cx, cy = self._clicked_spot
        elif self._keep.isChecked() and self._held_spot is not None:
            cx, cy = self._held_spot
        elif f.animal:
            cx, cy = (f.animal[0] + f.animal[2]) / 2, (f.animal[1] + f.animal[3]) / 2
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
        self._legend.setText(self._legend_text(sc, afi, fine, iw, ih))
        mine = " (your choice)" if self._yours else ""
        self._info.setText({"purple": f"<b style='color:#b39ddb'>AI…: could go{mine}</b>",
                            "keep": f"<b style='color:#81c784'>AI…: kept{mine}</b>"
                            }.get(self._suggestion, ""))

    def _legend_text(self, sc, afi, fine, iw, ih) -> str:
        """One line per colour: what it marks, and what was found there."""
        def line(colour, name, fact):
            return f"<span style='color:{colour}'>■</span> <b>{name}</b> — {fact}"
        rows = [line("#00e5ff", "Sharpest tiles",
                     f"where the picture is sharpest; the score: "
                     f"<b>{sc.get('sharp', 0):.0f}</b> (at 1600 px)")]
        where = ("where you clicked" if self._clicked_spot is not None
                 else "held while stepping" if self._keep.isChecked() and self._held_spot
                 else "on the animal" if self._frame.animal
                 else "on the sharpest area")
        spot = f"shown at 100 % on the right, {where}"
        if fine is not None:
            spot += f"; sharpness there: <b>{fine:.0f}</b> (on the preview, {iw}×{ih})"
        rows.append(line("#ff6d00", "The spot", spot))
        af_box, found = afi.get("box"), afi.get("found")
        if af_box is None:
            af = "no AF point recorded in this picture"
        elif afi.get("estimated"):
            af = (f"the camera focused on point {afi.get('name')}; its place on the frame is "
                  f"estimated for this camera"
                  + ("; dashed: the other points in focus" if afi.get("in_focus") else ""))
        else:
            af = ("the camera focused here" + (
                "; its bird detection found the bird" if found else
                "; its bird detection did not lock on" if found is False else ""))
        rows.append(line("#ffeb3b", "AF point", af))
        ai = self._ai_res or {}
        if ai.get("box"):
            native = (afi or {}).get("native") or 0
            full = f" on the full picture ({native} px wide)" if native else ""
            from engine.day_ai import cut_off
            from engine.ai_measure import ELSEWHERE
            cut = "; <b>cut off by the edge</b>" if cut_off(ai["box"]) else ""
            ratio = ai.get("sharp_ratio")
            away = ("; <b>the sharpest part of the picture is elsewhere</b>"
                    if ratio is not None and ratio < ELSEWHERE else "")
            rows.append(line("#66bb6a", "The animal", f"found by the AI; <b>{ai.get('px', 0)} "
                             f"px</b> long{full}{cut}{away}"))
        elif ai and not ai.get("missing") and not ai.get("error"):
            rows.append(line("#66bb6a", "The animal", "the AI found none"))
        return "<br>".join(rows)

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

    def _toggle_animal(self, on):
        self._frame.show_animal = on
        self._frame.update()

    def _toggle_af(self, on):
        self._frame.show_af = on
        self._frame.update()

    def stop(self):
        """Before the Editor closes: let a running measurement finish quietly."""
        self._gen += 1
        for t in list(self._threads):
            t.wait(10000)
