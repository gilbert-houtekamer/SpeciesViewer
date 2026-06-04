"""Decode Quality settings dialog — decode quality presets for the 1:1 full-res viewer.

Settings are stored in the machine-specific config under [fullres] and are read
live by qt/gui/fullres.py each time a RAW is decoded.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QComboBox, QDoubleSpinBox, QSpinBox, QRadioButton, QPushButton,
    QButtonGroup, QDialogButtonBox, QFrame, QMessageBox,
)
from utils.config import _config, persist_config

_SECTION = "fullres"

_ALGORITHMS = ["DHT", "AHD", "VNG", "PPG", "LINEAR"]
_FBDD      = ["Off", "Light", "Full"]
_DENOISE   = ["Off", "OpenCV NL"]

# (display_name, time_hint, algorithm, fbdd, denoise, h, hC, bright)
_PRESETS = [
    ("Fast, basic",         "≈1 s/photo",   "DHT", "Off",   "Off",             3, 3, 1.0),
    ("Fast with denoise",   "≈2 s/photo",   "DHT", "Off",   "OpenCV NL", 3, 6, 1.0),
    ("More detail, slower", "≈3–4 s/photo", "DHT", "Light", "Off",             3, 3, 1.0),
    ("Sharp and smoothest", "≈8 s/photo",   "DHT", "Full",  "OpenCV NL", 3, 6, 1.0),
]

_DEFAULTS = dict(algorithm="DHT", fbdd="Off", denoise="Off",
                 denoise_h=3, denoise_hc=3, bright=1.0)


def load_fullres_config() -> dict:
    return {
        "algorithm":  _config.get    (_SECTION, "algorithm",  fallback=_DEFAULTS["algorithm"]),
        "fbdd":       _config.get    (_SECTION, "fbdd",       fallback=_DEFAULTS["fbdd"]),
        "denoise":    _config.get    (_SECTION, "denoise",    fallback=_DEFAULTS["denoise"]),
        "denoise_h":  _config.getint (_SECTION, "denoise_h",  fallback=_DEFAULTS["denoise_h"]),
        "denoise_hc": _config.getint (_SECTION, "denoise_hc", fallback=_DEFAULTS["denoise_hc"]),
        "bright":     _config.getfloat(_SECTION, "bright",   fallback=_DEFAULTS["bright"]),
    }


def save_fullres_config(cfg: dict):
    if not _config.has_section(_SECTION):
        _config.add_section(_SECTION)
    for k, v in cfg.items():
        _config.set(_SECTION, k, str(v))
    persist_config()


class RawProcessingDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Decode Quality")
        self.setMinimumWidth(500)

        cfg = load_fullres_config()
        self._blocking = False

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Presets ───────────────────────────────────────────────────────
        preset_box = QGroupBox("Quick presets")
        pb = QVBoxLayout(preset_box)
        pb.setSpacing(4)
        self._preset_grp = QButtonGroup(self)
        for i, (name, hint, *_) in enumerate(_PRESETS):
            row = QHBoxLayout()
            rb = QRadioButton(name)
            self._preset_grp.addButton(rb, i)
            row.addWidget(rb)
            row.addStretch()
            hl = QLabel(f"<i>{hint}</i>")
            hl.setStyleSheet("color: #888;")
            row.addWidget(hl)
            pb.addLayout(row)
        self._custom_rb = QRadioButton("Select your preferences:")
        self._preset_grp.addButton(self._custom_rb, len(_PRESETS))
        pb.addWidget(self._custom_rb)
        root.addWidget(preset_box)

        # ── Settings form ─────────────────────────────────────────────────
        settings_box = QGroupBox("Settings")
        g = QGridLayout(settings_box)
        g.setColumnStretch(1, 1)
        r = 0

        g.addWidget(QLabel("Image processing algorithm (demosaic):"), r, 0)
        self._algo = QComboBox()
        self._algo.addItems(_ALGORITHMS)
        self._algo.setCurrentText(cfg["algorithm"])
        g.addWidget(self._algo, r, 1); r += 1

        g.addWidget(QLabel("Noise reduction in RAW (FBDD):"), r, 0)
        self._fbdd = QComboBox()
        self._fbdd.addItems(_FBDD)
        self._fbdd.setCurrentText(cfg["fbdd"])
        g.addWidget(self._fbdd, r, 1); r += 1

        g.addWidget(QLabel("Brightness:"), r, 0)
        self._bright = QDoubleSpinBox()
        self._bright.setRange(0.5, 2.5)
        self._bright.setSingleStep(0.1)
        self._bright.setDecimals(1)
        self._bright.setValue(cfg["bright"])
        g.addWidget(self._bright, r, 1); r += 1

        g.addWidget(QLabel("Denoise resulting image (OpenCV NL):"), r, 0)
        self._denoise = QComboBox()
        self._denoise.addItems(_DENOISE)
        self._denoise.setCurrentText(cfg["denoise"])
        g.addWidget(self._denoise, r, 1); r += 1

        self._h_lbl = QLabel("  Luma strength (h):")
        self._h = QSpinBox(); self._h.setRange(1, 30); self._h.setValue(cfg["denoise_h"])
        self._hc_lbl = QLabel("  Chroma strength (hC):")
        self._hc = QSpinBox(); self._hc.setRange(1, 30); self._hc.setValue(cfg["denoise_hc"])
        g.addWidget(self._h_lbl, r, 0); g.addWidget(self._h, r, 1); r += 1
        g.addWidget(self._hc_lbl, r, 0); g.addWidget(self._hc, r, 1)

        root.addWidget(settings_box)

        # ── Cache note ────────────────────────────────────────────────────
        from gui.fullres import fullres_cache_stats
        n_files, n_bytes = fullres_cache_stats()
        cache_str = (f"{n_files} cached photo(s) — {n_bytes / 1048576:.1f} MB"
                     if n_files else "cache is empty")
        self._note = QLabel(
            f"Settings apply to newly decoded photos only. "
            f"Clear the cache to re-decode with the new settings ({cache_str}).")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(self._note)

        cache_row = QHBoxLayout()
        self._clear_btn = QPushButton("Clear full-res cache…")
        self._clear_btn.clicked.connect(self._clear_cache)
        self._clear_btn.setEnabled(n_files > 0)
        cache_row.addWidget(self._clear_btn)
        cache_row.addStretch()
        root.addLayout(cache_row)

        # ── Dialog buttons ────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        # Wire signals
        for w in (self._algo, self._fbdd, self._denoise):
            w.currentTextChanged.connect(self._on_user_edit)
        for w in (self._bright, self._h, self._hc):
            w.valueChanged.connect(self._on_user_edit)
        self._denoise.currentTextChanged.connect(self._update_denoise_visibility)
        self._preset_grp.idClicked.connect(self._on_preset_clicked)

        # Set initial preset radio state
        self._blocking = True
        self._sync_preset_radio()
        self._blocking = False
        self._update_denoise_visibility()

    # ── internals ─────────────────────────────────────────────────────────

    def _update_denoise_visibility(self, _=None):
        show = self._denoise.currentText() != "Off"
        for w in (self._h_lbl, self._h, self._hc_lbl, self._hc):
            w.setVisible(show)

    def _current_values(self):
        return (self._algo.currentText(), self._fbdd.currentText(),
                self._denoise.currentText(), self._h.value(), self._hc.value(),
                round(self._bright.value(), 1))

    def _sync_preset_radio(self):
        vals = self._current_values()
        for i, (_, _, algo, fbdd, denoise, h, hc, bright) in enumerate(_PRESETS):
            if vals == (algo, fbdd, denoise, h, hc, round(bright, 1)):
                self._preset_grp.button(i).setChecked(True)
                return
        self._custom_rb.setChecked(True)

    def _on_user_edit(self, _=None):
        if not self._blocking:
            self._sync_preset_radio()

    def _on_preset_clicked(self, i):
        if i >= len(_PRESETS):
            return  # "Select your preferences" — leave the form fields alone
        _, _, algo, fbdd, denoise, h, hc, bright = _PRESETS[i]
        self._blocking = True
        self._algo.setCurrentText(algo)
        self._fbdd.setCurrentText(fbdd)
        self._denoise.setCurrentText(denoise)
        self._h.setValue(h)
        self._hc.setValue(hc)
        self._bright.setValue(bright)
        self._blocking = False
        self._update_denoise_visibility()

    def _clear_cache(self):
        from gui.fullres import clear_fullres_cache, fullres_cache_stats
        n_files, _ = fullres_cache_stats()
        if n_files == 0:
            QMessageBox.information(self, "Clear cache", "The full-res cache is already empty.")
            return
        if QMessageBox.question(
                self, "Clear full-res cache",
                f"Remove {n_files} cached photo(s)?\nThey will be re-decoded on next view.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        removed = clear_fullres_cache()
        self._clear_btn.setEnabled(False)
        n2, nb2 = fullres_cache_stats()
        self._note.setText(
            f"Settings apply to newly decoded photos only. "
            f"Cache cleared — {removed} file(s) removed.")
        QMessageBox.information(self, "Clear cache", f"Removed {removed} file(s).")

    def _save(self):
        save_fullres_config({
            "algorithm":  self._algo.currentText(),
            "fbdd":       self._fbdd.currentText(),
            "denoise":    self._denoise.currentText(),
            "denoise_h":  self._h.value(),
            "denoise_hc": self._hc.value(),
            "bright":     round(self._bright.value(), 1),
        })
        self.accept()
