"""ExposureWidget — a compact exposure-compensation control, camera-style:
-3 .. +3 EV in 1/3-stop steps (19 positions), matching the increment every
camera's own exposure-comp dial uses.

The slider's native units are THIRDS OF A STOP (integers -9..+9), not EV
floats — sidestepping float step-size rounding entirely. `value_ev` converts.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

MIN_THIRDS = -9   # -3.0 EV
MAX_THIRDS = 9    # +3.0 EV


def format_ev(thirds: int) -> str:
    """Camera-style label for a thirds-of-a-stop value, e.g. 4 -> '+1⅓ EV',
    -5 -> '−1⅔ EV', 0 -> '0 EV', 6 -> '+2 EV'."""
    if thirds == 0:
        return "0 EV"
    sign = "+" if thirds > 0 else "−"   # proper minus, not a hyphen
    whole, rem = divmod(abs(thirds), 3)
    frac = {0: "", 1: "⅓", 2: "⅔"}[rem]   # ⅓ ⅔
    body = f"{whole} {frac}".strip() if whole and frac else (str(whole) if whole else frac)
    return f"{sign}{body} EV"


class ExposureWidget(QWidget):
    """live = fires continuously while dragging, for a cheap re-render of the
    already-decoded preview (see gui/exposure_render.py) — no sidecar write.
    committed = fires once the value settles (slider released, or a
    keyboard/wheel nudge, which has no separate "release") — this is what a
    host should persist."""
    live = pyqtSignal(float)
    committed = pyqtSignal(float)

    def __init__(self, initial_ev: float = 0.0, parent=None):
        super().__init__(parent)
        self._thirds = self._clamp(round(initial_ev * 3))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._icon = QLabel("☀")   # ☀ — a lightweight "exposure" glyph, no icon asset needed
        self._icon.setToolTip("Exposure suggestion for PhotoLab / Lightroom")
        layout.addWidget(self._icon)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(MIN_THIRDS)
        self._slider.setMaximum(MAX_THIRDS)
        self._slider.setValue(self._thirds)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(3)
        self._slider.setFixedWidth(100)
        self._slider.setToolTip(
            "Suggest an exposure adjustment for PhotoLab / Lightroom, in 1/3-stop "
            "steps (-3 .. +3 EV) — the same increment as a camera's exposure-comp "
            "dial. Written to the RAW's sidecars; PhotoLab/Lightroom apply it on "
            "their next develop.")
        self._slider.valueChanged.connect(self._on_value_changed)
        self._slider.sliderReleased.connect(self._on_settled)
        layout.addWidget(self._slider)

        self._value_lbl = QLabel(format_ev(self._thirds))
        self._value_lbl.setMinimumWidth(52)
        self._value_lbl.setStyleSheet("font-size: 10px;")
        layout.addWidget(self._value_lbl)

    @staticmethod
    def _clamp(thirds: int) -> int:
        return max(MIN_THIRDS, min(MAX_THIRDS, thirds))

    def _on_value_changed(self, thirds: int):
        self._thirds = thirds
        self._value_lbl.setText(format_ev(thirds))
        self.live.emit(self.value_ev)
        # A keyboard/wheel step has no sliderReleased — it IS the settled value,
        # so commit immediately. A mouse drag gets sliderReleased instead; this
        # branch stays quiet during the drag itself (isSliderDown() is True).
        if not self._slider.isSliderDown():
            self.committed.emit(self.value_ev)

    def _on_settled(self):
        self.committed.emit(self.value_ev)

    @property
    def value_ev(self) -> float:
        return self._thirds / 3.0

    def set_value_ev(self, ev: float):
        """Set the displayed value WITHOUT emitting live/committed — for
        pre-filling from a picture's already-stored exposure when the host
        switches pictures."""
        thirds = self._clamp(round((ev or 0.0) * 3))
        if thirds == self._thirds:
            return
        self._thirds = thirds
        self._slider.blockSignals(True)
        self._slider.setValue(thirds)
        self._slider.blockSignals(False)
        self._value_lbl.setText(format_ev(thirds))
