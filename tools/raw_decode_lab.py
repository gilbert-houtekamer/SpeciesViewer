#!/usr/bin/env python3
"""Standalone lab to experiment with RAW 1:1 decode quality.

NOT part of the app — a throwaway tuning tool. Tweak the rawpy postprocess knobs
(demosaic / FBDD / noise_thr / median / brightness) and optionally run an OpenCV
NL-means or BM3D denoise pass, then pixel-peep the result at 100%. Whatever
settings you like, copy into the FULLRES_* constants in qt/gui/fullres.py.

Run:  python tools/raw_decode_lab.py ["path\\to\\file.ORF"]
"""

import sys
import time
from pathlib import Path

import numpy as np
import rawpy
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QScrollArea, QFileDialog)
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


class _Worker(QObject):
    done = pyqtSignal(object, str)  # ndarray (uint8 HxWx3) or None, info string

    def __init__(self, path, params, denoise):
        super().__init__()
        self.path, self.params, self.denoise = path, params, denoise

    def run(self):
        try:
            t = time.time()
            with rawpy.imread(self.path) as raw:
                rgb = raw.postprocess(**self.params)
            info = f"{rgb.shape[1]}x{rgb.shape[0]}  |  decode {time.time() - t:.1f}s"

            d = self.denoise
            if d["crop"]:  # center crop so OpenCV/BM3D iterate fast
                h, w = rgb.shape[:2]
                c = d["crop"]
                y0, x0 = max(0, h // 2 - c // 2), max(0, w // 2 - c // 2)
                rgb = rgb[y0:y0 + c, x0:x0 + c].copy()
                info += f"  |  crop {rgb.shape[1]}x{rgb.shape[0]}"

            if d["mode"] == "OpenCV NL-means":
                import cv2
                t2 = time.time()
                rgb = cv2.fastNlMeansDenoisingColored(
                    np.ascontiguousarray(rgb), None, d["h"], d["hColor"], 7, 21)
                info += f"  |  OpenCV h={d['h']} hC={d['hColor']} {time.time() - t2:.1f}s"
            elif d["mode"] == "BM3D":
                import bm3d
                t2 = time.time()
                arr = rgb.astype(np.float32) / 255.0
                arr = bm3d.bm3d(arr, d["sigma"] / 255.0)
                rgb = (arr.clip(0, 1) * 255).astype(np.uint8)
                info += f"  |  BM3D sigma={d['sigma']} {time.time() - t2:.1f}s"

            self.done.emit(np.ascontiguousarray(rgb), info)
        except Exception as e:
            self.done.emit(None, f"ERROR: {type(e).__name__}: {e}")


class Lab(QWidget):
    def __init__(self, path):
        super().__init__()
        self.setWindowTitle("RAW decode lab")
        self.resize(1200, 900)
        self._path = path
        self._rgb = None
        self._thread = self._worker = None

        root = QVBoxLayout(self)

        # File row
        file_row = QHBoxLayout()
        self.file_lbl = QLabel(path)
        open_btn = QPushButton("Open…")
        open_btn.clicked.connect(self._open)
        file_row.addWidget(QLabel("File:"))
        file_row.addWidget(self.file_lbl, 1)
        file_row.addWidget(open_btn)
        root.addLayout(file_row)

        # Controls grid
        g = QGridLayout()
        self.demosaic = QComboBox(); self.demosaic.addItems(_DEMOSAIC); self.demosaic.setCurrentText("DHT")
        self.fbdd = QComboBox(); self.fbdd.addItems(_FBDD); self.fbdd.setCurrentText("Full")
        self.noise_thr = QSpinBox(); self.noise_thr.setRange(0, 5000); self.noise_thr.setValue(150)
        self.median = QSpinBox(); self.median.setRange(0, 8); self.median.setValue(0)
        self.no_auto_bright = QCheckBox("no_auto_bright"); self.no_auto_bright.setChecked(True)
        self.bright = QDoubleSpinBox(); self.bright.setRange(0.1, 4.0); self.bright.setSingleStep(0.1); self.bright.setValue(1.0)
        for col, (lbl, w) in enumerate([
                ("demosaic", self.demosaic), ("fbdd", self.fbdd), ("noise_thr", self.noise_thr),
                ("median", self.median), ("bright", self.bright)]):
            g.addWidget(QLabel(lbl), 0, col); g.addWidget(w, 1, col)
        g.addWidget(self.no_auto_bright, 1, 5)
        root.addLayout(g)

        # Denoise row
        dn = QHBoxLayout()
        self.denoise_mode = QComboBox()
        modes = ["None", "OpenCV NL-means"] + (["BM3D"] if _has_bm3d() else [])
        self.denoise_mode.addItems(modes)
        self.h = QSpinBox(); self.h.setRange(1, 60); self.h.setValue(6)
        self.hColor = QSpinBox(); self.hColor.setRange(1, 60); self.hColor.setValue(6)
        self.sigma = QSpinBox(); self.sigma.setRange(1, 80); self.sigma.setValue(15)
        self.crop = QCheckBox("center-crop 1200px (fast denoise)"); self.crop.setChecked(True)
        dn.addWidget(QLabel("denoise:")); dn.addWidget(self.denoise_mode)
        dn.addWidget(QLabel("h")); dn.addWidget(self.h)
        dn.addWidget(QLabel("hColor")); dn.addWidget(self.hColor)
        dn.addWidget(QLabel("bm3d σ")); dn.addWidget(self.sigma)
        dn.addWidget(self.crop)
        if not _has_bm3d():
            dn.addWidget(QLabel("(pip install bm3d for BM3D)"))
        dn.addStretch()
        self.decode_btn = QPushButton("Decode")
        self.decode_btn.clicked.connect(self._decode)
        dn.addWidget(self.decode_btn)
        root.addLayout(dn)

        self.status = QLabel("Ready — press Decode.")
        root.addWidget(self.status)

        # 1:1 image
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(False)
        self.img = QLabel(); self.img.setStyleSheet("background:#222;")
        self.scroll.setWidget(self.img)
        root.addWidget(self.scroll, 1)

    def _open(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Open RAW", str(Path(self._path).parent),
            "RAW (*.orf *.ORF *.cr3 *.CR3 *.nef *.NEF *.arw *.ARW *.raf *.dng *.rw2)")
        if p:
            self._path = p
            self.file_lbl.setText(p)

    def _decode(self):
        if self._thread and self._thread.isRunning():
            return
        params = dict(
            use_camera_wb=True,
            output_color=rawpy.ColorSpace.sRGB,
            no_auto_bright=self.no_auto_bright.isChecked(),
            bright=self.bright.value(),
            median_filter_passes=self.median.value(),
            demosaic_algorithm=getattr(rawpy.DemosaicAlgorithm, self.demosaic.currentText()),
            fbdd_noise_reduction=getattr(rawpy.FBDDNoiseReductionMode, self.fbdd.currentText()),
        )
        if self.noise_thr.value() > 0:
            params["noise_thr"] = float(self.noise_thr.value())
        denoise = dict(
            mode=self.denoise_mode.currentText(),
            h=self.h.value(), hColor=self.hColor.value(), sigma=self.sigma.value(),
            crop=1200 if self.crop.isChecked() else 0)

        self.status.setText("Decoding…")
        self.decode_btn.setEnabled(False)
        self._thread = QThread()
        self._worker = _Worker(self._path, params, denoise)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_done)
        self._thread.start()

    def _on_done(self, rgb, info):
        self._thread.quit(); self._thread.wait()
        self.decode_btn.setEnabled(True)
        if rgb is None:
            self.status.setText(info)
            return
        self._rgb = rgb  # keep ref alive for QImage
        h, w = rgb.shape[:2]
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self.img.setPixmap(QPixmap.fromImage(qimg))
        self.img.resize(w, h)
        self.status.setText(info)

    def closeEvent(self, e):
        if self._thread and self._thread.isRunning():
            self._thread.quit(); self._thread.wait(5000)
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
