"""Sidecar Behaviour settings dialog — controls what SpeciesViewer writes to a
picture's DxO PhotoLab `.dop` and Lightroom `.xmp` sidecars.

Validated: PhotoLab reads colour label + rating from the `.dop` (Item-level
ColorLabel/Rating) when you Ctrl-A → import sidecars, but mistreats them in the
`.xmp` (ignores / loses them / filter-hides the image). Lightroom ignores `.dop`.
So the choices are a 2×2 — {colour&rating, crop} × {.dop, .xmp}. Keywords and
descriptions are not shown here: they always go to `.xmp` + JPG (the species
pipeline and website need them, and they never caused the PL trouble).

Stored in machine config [sidecars]; read live at each write. See
utils/sidecar_policy.py.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QGroupBox, QLabel, QCheckBox,
    QDialogButtonBox, QFrame,
)
from utils.sidecar_policy import load_sidecar_config, save_sidecar_config


class SidecarSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sidecar Behaviour")
        self.setMinimumWidth(560)

        cfg = load_sidecar_config()
        root = QVBoxLayout(self)
        root.setSpacing(8)

        intro = QLabel(
            "Choose where SpeciesViewer writes colour labels, ratings and crops. "
            "PhotoLab reads them from its own .dop file when you select all "
            "(Ctrl-A) and import sidecars; it mistreats colour/rating in the .xmp, "
            "so that channel is off by default. Lightroom reads the .xmp and "
            "ignores .dop.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(intro)

        grid_box = QGroupBox("What to write, and where")
        g = QGridLayout(grid_box)
        g.setHorizontalSpacing(24)
        g.addWidget(QLabel("<b>PhotoLab .dop</b>"), 0, 1)
        g.addWidget(QLabel("<b>Lightroom .xmp</b>"), 0, 2)

        g.addWidget(QLabel("Colour label / rating"), 1, 0)
        self._cr_dop = QCheckBox(); self._cr_dop.setChecked(cfg["colorrating_dop"])
        self._cr_xmp = QCheckBox(); self._cr_xmp.setChecked(cfg["colorrating_xmp"])
        g.addWidget(self._cr_dop, 1, 1)
        g.addWidget(self._cr_xmp, 1, 2)

        g.addWidget(QLabel("Crop"), 2, 0)
        self._crop_dop = QCheckBox(); self._crop_dop.setChecked(cfg["crop_dop"])
        self._crop_xmp = QCheckBox(); self._crop_xmp.setChecked(cfg["crop_xmp"])
        g.addWidget(self._crop_dop, 2, 1)
        g.addWidget(self._crop_xmp, 2, 2)
        g.setColumnStretch(3, 1)
        root.addWidget(grid_box)

        note = QLabel(
            "Recommended (PhotoLab workflow): everything to the .dop, nothing to the "
            ".xmp. Prepare in SpeciesViewer, then in PhotoLab press Ctrl-A and import "
            "sidecars to pull colour, rating and crop in at once.<br>"
            "The .xmp columns are for a Lightroom workflow only — PhotoLab mistreats "
            "colour/rating there and ignores our crop.<br><br>"
            "<i>Keywords and descriptions are always written to the .xmp and JPGs "
            "(needed by the species pipeline and website); they are not affected by "
            "these switches.</i>")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(note)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self):
        save_sidecar_config({
            "colorrating_dop": self._cr_dop.isChecked(),
            "colorrating_xmp": self._cr_xmp.isChecked(),
            "crop_dop":        self._crop_dop.isChecked(),
            "crop_xmp":        self._crop_xmp.isChecked(),
        })
        self.accept()
