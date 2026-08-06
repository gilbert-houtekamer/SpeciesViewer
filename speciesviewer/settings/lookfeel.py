"""Look & Feel settings — the shared, user-facing visual preferences.

v1 covers the picture-label COLOUR SCHEME (re-tints Red/Yellow/Green/Blue/Purple
across every viewer). The choice is persisted to ``[lookfeel] color_scheme`` and
read at import, so it applies on the next restart (see gui/label_colors.py).

Light / dark THEME is shown here as the planned second control, but disabled:
app-wide dark is a larger, separate effort, so we don't offer a switch that does
nothing yet.
"""

import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QButtonGroup,
    QPushButton, QWidget, QFrame,
)
from PyQt6.QtCore import Qt

from utils.config import get_setting, set_setting, persist_config
from gui import theme
from gui.label_colors import COLORS, COLOR_SCHEMES, active_scheme_name

logger = logging.getLogger("speciesviewer")

_MONO = "'Cascadia Mono','Cascadia Code',Consolas,monospace"


def _section(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"font-family:{_MONO}; font-size:9.5px; font-weight:600;"
        f" letter-spacing:1.4px; color:{theme.INK_SOFT}; padding:4px 0 2px 0;")
    return lbl


def _swatches(scheme: dict[str, str]) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(5)
    for c in COLORS:
        dot = QLabel()
        dot.setFixedSize(16, 16)
        dot.setStyleSheet(
            f"background:{scheme[c]}; border-radius:8px; border:1px solid {theme.LINE};")
        h.addWidget(dot)
    h.addStretch(1)
    return row


class LookFeelDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Look & Feel")
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)

        intro = QLabel(
            "The colour of the picture labels — Red, Yellow, Green, Blue, Purple — "
            "across every viewer. “Vivid” is the current set.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{theme.INK}; font-size:12.5px;")
        lay.addWidget(intro)

        lay.addWidget(_section("PICTURE LABEL COLOURS"))
        self._scheme_group = QButtonGroup(self)
        current = active_scheme_name()
        for name, scheme in COLOR_SCHEMES.items():
            r = QWidget()
            rl = QHBoxLayout(r)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(10)
            rb = QRadioButton(name)
            rb.setStyleSheet("font-size:12.5px;")
            rb.setChecked(name == current)
            self._scheme_group.addButton(rb)
            rb._scheme_name = name          # stash for read-back
            rl.addWidget(rb, 0)
            rl.addStretch(1)
            rl.addWidget(_swatches(scheme), 0)
            lay.addWidget(r)

        # THEME — planned, not yet wired (app-wide dark is a separate effort)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{theme.LINE}; margin:6px 0;")
        lay.addWidget(sep)
        lay.addWidget(_section("THEME"))
        self._theme_group = QButtonGroup(self)
        self._light_rb = QRadioButton("Light")
        self._dark_rb = QRadioButton("Dark")
        for rb in (self._light_rb, self._dark_rb):
            rb.setStyleSheet("font-size:12.5px;")
            self._theme_group.addButton(rb)
            lay.addWidget(rb)
        (self._dark_rb if theme.is_dark() else self._light_rb).setChecked(True)

        note = QLabel("↻  Changes apply after restarting SpeciesViewer.")
        note.setStyleSheet(f"color:{theme.INK_SOFT}; font-size:11px; padding-top:6px;")
        lay.addWidget(note)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addLayout(btns)

    def _save(self):
        chosen = next((b for b in self._scheme_group.buttons() if b.isChecked()), None)
        name = getattr(chosen, "_scheme_name", None) if chosen else None
        if name:
            set_setting("lookfeel", "color_scheme", name)
        theme_val = "dark" if self._dark_rb.isChecked() else "light"
        set_setting("lookfeel", "theme", theme_val)
        persist_config()
        logger.info("Look & feel: scheme='%s', theme='%s' (applies on restart).",
                    name, theme_val)
        self.accept()
