"""Location (Nominatim) settings dialog — caption language, detail level (zoom),
and whether 'Get location' replaces existing place text.

Stored in [geolocate] of the machine config and read live by gui/geolocate.py
each time locations are looked up (so a change takes effect on the next run)."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QGroupBox, QLabel,
    QComboBox, QSpinBox, QCheckBox, QDialogButtonBox, QFrame,
)
from gui.geolocate import load_geolocate_config, save_geolocate_config

# (accept-language code, display label) — values Nominatim understands; the code
# controls the SCRIPT of the returned names (without it Nominatim returns the
# local language, e.g. Greek), so this is what makes a caption readable.
_LANGUAGES = [
    ("en", "English"), ("nl", "Nederlands"), ("de", "Deutsch"),
    ("fr", "Français"), ("es", "Español"), ("it", "Italiano"),
]


def _zoom_hint(z: int) -> str:
    """Rough granularity of a Nominatim reverse-geocode zoom, for the hint label."""
    if z <= 4:
        return "country"
    if z <= 7:
        return "state / region"
    if z <= 9:
        return "county"
    if z <= 11:
        return "city / town"
    if z <= 13:
        return "village / suburb"
    if z <= 15:
        return "neighbourhood / locality"
    return "street / building"


class LocationSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Location Settings")
        self.setMinimumWidth(460)

        cfg = load_geolocate_config()
        root = QVBoxLayout(self)
        root.setSpacing(8)

        box = QGroupBox("Nominatim (OpenStreetMap) lookup")
        g = QGridLayout(box)
        g.setColumnStretch(1, 1)
        r = 0

        g.addWidget(QLabel("Caption language:"), r, 0)
        self._lang = QComboBox()
        for code, label in _LANGUAGES:
            self._lang.addItem(label, code)
        idx = self._lang.findData(cfg["lang"])
        if idx < 0:                       # an unlisted code in the config — keep it
            self._lang.addItem(cfg["lang"], cfg["lang"])
            idx = self._lang.findData(cfg["lang"])
        self._lang.setCurrentIndex(idx)
        g.addWidget(self._lang, r, 1); r += 1

        g.addWidget(QLabel("Detail level (zoom):"), r, 0)
        self._zoom = QSpinBox()
        self._zoom.setRange(3, 18)
        self._zoom.setValue(cfg["zoom"])
        self._zoom_hint = QLabel()
        self._zoom_hint.setStyleSheet("color: #888;")
        self._zoom.valueChanged.connect(
            lambda z: self._zoom_hint.setText(f"  ≈ {_zoom_hint(z)}"))
        self._zoom_hint.setText(f"  ≈ {_zoom_hint(self._zoom.value())}")
        from PyQt6.QtWidgets import QHBoxLayout
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(self._zoom)
        zoom_row.addWidget(self._zoom_hint)
        zoom_row.addStretch()
        g.addLayout(zoom_row, r, 1); r += 1

        self._overwrite = QCheckBox("Replace existing location text")
        self._overwrite.setChecked(cfg["overwrite"])
        g.addWidget(self._overwrite, r, 1); r += 1

        root.addWidget(box)

        note = QLabel(
            "Off (recommended): a 'Get location' run only fills pictures that have "
            "no location yet, so hand-typed places (Edit location…) are never "
            "overwritten. Turn on to re-geocode and replace existing text.")
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
        save_geolocate_config({
            "lang": self._lang.currentData(),
            "zoom": self._zoom.value(),
            "overwrite": self._overwrite.isChecked(),
        })
        self.accept()
