#!/usr/bin/env python3
"""Standalone lab to experiment with RAW 1:1 decode quality — A/B side-by-side.

NOT part of the app — a throwaway tuning tool. Two independent panes (A and B),
each with its own rawpy postprocess knobs (demosaic / FBDD / noise_thr / median /
brightness) and optional OpenCV NL-means or BM3D denoise pass. Press "Decode both"
and the two results render at 1:1; the scroll areas pan in lock-step so you can
pixel-peep the SAME spot under two recipes. Each pane prints a copy-ready recipe
string — paste the winner into the FULLRES_* constants in qt/gui/fullres.py.

Run:  python tools/raw_decode_lab.py ["path\\to\\file.ORF"]
"""

import re
import sys
import time
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "lab_out"

import numpy as np
import rawpy
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QScrollArea, QFileDialog, QFrame)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal

DEFAULT = r"D:\CurrentPictures\2026\2026 test1\2026-05-31\2026 bussum - N5310001.ORF"

_DEMOSAIC = ["AHD", "DHT", "AAHD", "VNG", "DCB", "LMMSE", "AMAZE", "PPG", "LINEAR"]
_FBDD = ["Off", "Light", "Full"]


def _has_bm3d():
    try:
        import bm3d  # noqa: F401
        return True
    except Exception:
        return False


_HAS_BM3D = _has_bm3d()


def _decode(path, params, denoise):
    """Decode one RAW with the given rawpy params + optional denoise pass.
    Returns (rgb uint8 HxWx3, info string). Raises on failure."""
    t = time.time()
    with rawpy.imread(path) as raw:
        rgb = raw.postprocess(**params)
    info = f"{rgb.shape[1]}x{rgb.shape[0]}  |  decode {time.time() - t:.1f}s"

    if denoise["crop"]:  # center crop so OpenCV/BM3D iterate fast
        h, w = rgb.shape[:2]
        c = denoise["crop"]
        y0, x0 = max(0, h // 2 - c // 2), max(0, w // 2 - c // 2)
        rgb = rgb[y0:y0 + c, x0:x0 + c].copy()
        info += f"  |  crop {rgb.shape[1]}x{rgb.shape[0]}"

    if denoise["mode"] == "OpenCV NL-means":
        import cv2
        t2 = time.time()
        rgb = cv2.fastNlMeansDenoisingColored(
            np.ascontiguousarray(rgb), None, denoise["h"], denoise["hColor"], 7, 21)
        info += f"  |  NL-means {time.time() - t2:.1f}s"
    elif denoise["mode"] == "BM3D":
        import bm3d
        t2 = time.time()
        arr = rgb.astype(np.float32) / 255.0
        arr = bm3d.bm3d(arr, denoise["sigma"] / 255.0)
        rgb = (arr.clip(0, 1) * 255).astype(np.uint8)
        info += f"  |  BM3D {time.time() - t2:.1f}s"

    return np.ascontiguousarray(rgb), info


class _Worker(QObject):
    done = pyqtSignal(int, object, str)  # pane index, ndarray or None, info string

    def __init__(self, index, path, params, denoise):
        super().__init__()
        self.index, self.path, self.params, self.denoise = index, path, params, denoise

    def run(self):
        try:
            rgb, info = _decode(self.path, self.params, self.denoise)
            self.done.emit(self.index, rgb, info)
        except Exception as e:
            self.done.emit(self.index, None, f"ERROR: {type(e).__name__}: {e}")


class RecipePane(QWidget):
    """One column: rawpy + denoise knobs, a recipe label, and a 1:1 scroll view."""

    def __init__(self, title, defaults):
        super().__init__()
        self.label = title
        self._rgb = None
        self.on_save = None  # Lab sets a callback(pane)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        hdr_row = QHBoxLayout()
        hdr_row.addWidget(QLabel(f"<b>{title}</b>"))
        hdr_row.addStretch()
        self.save_btn = QPushButton("Save PNG"); self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(lambda: self.on_save and self.on_save(self))
        hdr_row.addWidget(self.save_btn)
        v.addLayout(hdr_row)

        # rawpy knobs
        g = QGridLayout()
        self.demosaic = QComboBox(); self.demosaic.addItems(_DEMOSAIC)
        self.demosaic.setCurrentText(defaults["demosaic"])
        self.fbdd = QComboBox(); self.fbdd.addItems(_FBDD)
        self.fbdd.setCurrentText(defaults["fbdd"])
        self.noise_thr = QSpinBox(); self.noise_thr.setRange(0, 5000)
        self.noise_thr.setValue(defaults["noise_thr"])
        self.median = QSpinBox(); self.median.setRange(0, 8)
        self.median.setValue(defaults["median"])
        self.bright = QDoubleSpinBox(); self.bright.setRange(0.1, 4.0)
        self.bright.setSingleStep(0.1); self.bright.setValue(defaults["bright"])
        self.no_auto_bright = QCheckBox("no_auto_bright")
        self.no_auto_bright.setChecked(defaults["no_auto_bright"])
        for col, (lbl, w) in enumerate([
                ("demosaic", self.demosaic), ("fbdd", self.fbdd),
                ("noise_thr", self.noise_thr), ("median", self.median),
                ("bright", self.bright)]):
            g.addWidget(QLabel(lbl), 0, col); g.addWidget(w, 1, col)
        g.addWidget(self.no_auto_bright, 1, 5)
        v.addLayout(g)

        # denoise knobs
        dn = QHBoxLayout()
        self.denoise_mode = QComboBox()
        self.denoise_mode.addItems(["None", "OpenCV NL-means"] + (["BM3D"] if _HAS_BM3D else []))
        self.denoise_mode.setCurrentText(defaults["denoise"])
        self.h = QSpinBox(); self.h.setRange(1, 60); self.h.setValue(defaults["h"])
        self.hColor = QSpinBox(); self.hColor.setRange(1, 60); self.hColor.setValue(defaults["hColor"])
        self.sigma = QSpinBox(); self.sigma.setRange(1, 80); self.sigma.setValue(defaults["sigma"])
        dn.addWidget(QLabel("denoise")); dn.addWidget(self.denoise_mode)
        dn.addWidget(QLabel("h")); dn.addWidget(self.h)
        dn.addWidget(QLabel("hC")); dn.addWidget(self.hColor)
        dn.addWidget(QLabel("σ")); dn.addWidget(self.sigma)
        dn.addStretch()
        v.addLayout(dn)

        self.recipe = QLabel(""); self.recipe.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.recipe.setStyleSheet("color:#8c8;")
        v.addWidget(self.recipe)
        self.status = QLabel("Ready.")
        v.addWidget(self.status)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(False)
        self.img = QLabel(); self.img.setStyleSheet("background:#222;")
        self.scroll.setWidget(self.img)
        v.addWidget(self.scroll, 1)

    def params(self):
        p = dict(
            use_camera_wb=True,
            output_color=rawpy.ColorSpace.sRGB,
            no_auto_bright=self.no_auto_bright.isChecked(),
            bright=self.bright.value(),
            median_filter_passes=self.median.value(),
            demosaic_algorithm=getattr(rawpy.DemosaicAlgorithm, self.demosaic.currentText()),
            fbdd_noise_reduction=getattr(rawpy.FBDDNoiseReductionMode, self.fbdd.currentText()),
        )
        if self.noise_thr.value() > 0:
            p["noise_thr"] = float(self.noise_thr.value())
        return p

    def denoise(self, crop):
        return dict(mode=self.denoise_mode.currentText(), h=self.h.value(),
                    hColor=self.hColor.value(), sigma=self.sigma.value(), crop=crop)

    def recipe_str(self):
        s = (f"{self.demosaic.currentText()} | FBDD={self.fbdd.currentText()} | "
             f"noise_thr={self.noise_thr.value()} | median={self.median.value()}")
        m = self.denoise_mode.currentText()
        if m == "OpenCV NL-means":
            s += f" | NL-means h={self.h.value()} hC={self.hColor.value()}"
        elif m == "BM3D":
            s += f" | BM3D σ={self.sigma.value()}"
        return s

    def recipe_slug(self):
        return re.sub(r"[^A-Za-z0-9]+", "_", self.recipe_str()).strip("_")

    def show_result(self, rgb, info):
        if rgb is None:
            self.save_btn.setEnabled(False)
            self.status.setText(info)
            return
        self._rgb = rgb  # keep ref alive for QImage
        h, w = rgb.shape[:2]
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self.img.setPixmap(QPixmap.fromImage(qimg))
        self.img.resize(w, h)
        self.save_btn.setEnabled(True)
        self.status.setText(info)


class Lab(QWidget):
    def __init__(self, path):
        super().__init__()
        self.setWindowTitle("RAW decode lab — A/B")
        self.resize(1700, 950)
        self._path = path
        self._threads = []
        self._pending = 0

        root = QVBoxLayout(self)

        # File + shared controls row
        top = QHBoxLayout()
        self.file_lbl = QLabel(path)
        open_btn = QPushButton("Open…"); open_btn.clicked.connect(self._open)
        self.crop = QCheckBox("center-crop 1200px (fast denoise)"); self.crop.setChecked(True)
        self.lockstep = QCheckBox("lock-step pan"); self.lockstep.setChecked(True)
        self.decode_btn = QPushButton("Decode both"); self.decode_btn.clicked.connect(self._decode_both)
        top.addWidget(QLabel("File:")); top.addWidget(self.file_lbl, 1)
        top.addWidget(open_btn); top.addWidget(self.crop)
        top.addWidget(self.lockstep); top.addWidget(self.decode_btn)
        root.addLayout(top)

        # Two panes
        a_def = dict(demosaic="DHT", fbdd="Full", noise_thr=150, median=0, bright=1.0,
                     no_auto_bright=True, denoise="None", h=6, hColor=6, sigma=15)
        b_def = dict(a_def, denoise="OpenCV NL-means" if not _HAS_BM3D else "BM3D")
        self.paneA = RecipePane("A", a_def)
        self.paneB = RecipePane("B", b_def)
        self.paneA.on_save = self.paneB.on_save = self._save_pane
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
        panes = QHBoxLayout()
        panes.addWidget(self.paneA, 1); panes.addWidget(sep); panes.addWidget(self.paneB, 1)
        root.addLayout(panes, 1)

        self._wire_lockstep()

    def _wire_lockstep(self):
        a, b = self.paneA.scroll, self.paneB.scroll
        for src, dst in [(a, b), (b, a)]:
            src.horizontalScrollBar().valueChanged.connect(
                lambda v, d=dst: self._mirror(d.horizontalScrollBar(), v))
            src.verticalScrollBar().valueChanged.connect(
                lambda v, d=dst: self._mirror(d.verticalScrollBar(), v))

    def _mirror(self, bar, v):
        if self.lockstep.isChecked() and bar.value() != v:
            bar.setValue(v)

    def _save_pane(self, pane):
        pix = pane.img.pixmap()
        if pix is None or pix.isNull():
            return
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stem = Path(self._path).stem
        out = OUT_DIR / f"{stem}__{pane.label}__{pane.recipe_slug()}.png"
        if pix.save(str(out), "PNG"):
            pane.status.setText(f"Saved → {out}")
        else:
            pane.status.setText(f"Save FAILED → {out}")

    def _open(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Open RAW", str(Path(self._path).parent),
            "RAW (*.orf *.ORF *.cr3 *.CR3 *.nef *.NEF *.arw *.ARW *.raf *.dng *.rw2)")
        if p:
            self._path = p
            self.file_lbl.setText(p)

    def _decode_both(self):
        if self._pending:
            return
        crop = 1200 if self.crop.isChecked() else 0
        self.decode_btn.setEnabled(False)
        self._pending = 2
        for idx, pane in enumerate((self.paneA, self.paneB)):
            pane.recipe.setText(pane.recipe_str())
            pane.status.setText("Decoding…")
            thread = QThread()
            worker = _Worker(idx, self._path, pane.params(), pane.denoise(crop))
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.done.connect(self._on_done)
            worker.done.connect(lambda *a, t=thread: (t.quit(),))
            thread.finished.connect(thread.deleteLater)
            self._threads.append((thread, worker))
            thread.start()

    def _on_done(self, index, rgb, info):
        (self.paneA if index == 0 else self.paneB).show_result(rgb, info)
        self._pending -= 1
        if self._pending <= 0:
            self._pending = 0
            self.decode_btn.setEnabled(True)
            self._threads = [(t, w) for t, w in self._threads if t.isRunning()]

    def closeEvent(self, e):
        for t, w in self._threads:
            if t.isRunning():
                t.quit(); t.wait(5000)
        super().closeEvent(e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    if not Path(path).exists():
        path, _ = QFileDialog.getOpenFileName(None, "Open RAW", "", "RAW (*.orf *.ORF *.cr3 *.nef *.arw *.raf *.dng *.rw2)")
        if not path:
            sys.exit(0)
    win = Lab(path)
    win.show()
    sys.exit(app.exec())
