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
    QPushButton, QWidget, QFrame, QComboBox, QGridLayout, QSpinBox,
)
from PyQt6.QtCore import Qt

from utils.config import get_setting, set_setting, get_int_setting, persist_config
from gui import theme
from gui.label_colors import COLORS, COLOR_SCHEMES, active_scheme_name
from gui.card_layout import (CORNER_OPTIONS, FRAME_OPTIONS, VIEWERS,
                             POSITION_LABELS, corner_config, set_corner_config)

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

        self._build_corner_section(lay)
        self._build_culling_section(lay)

        note = QLabel("↻  Colour scheme + theme apply after restarting SpeciesViewer; "
                      "card corners + culling-grid settings apply the next time you "
                      "open a viewer.")
        note.setWordWrap(True)
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

    # ── Card corners — per-viewer configurable corner set + frame colour ──────
    def _build_corner_section(self, lay):
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{theme.LINE}; margin:6px 0;")
        lay.addWidget(sep)
        lay.addWidget(_section("CARD CORNERS — PER VIEWER"))

        # in-memory copy of every viewer's config; edits land here, saved on Save.
        self._cc_state = {vk: dict(corner_config(vk)) for vk, _ in VIEWERS}

        vrow = QHBoxLayout()
        vlbl = QLabel("Viewer:"); vlbl.setStyleSheet("font-size:12.5px;")
        self._cc_viewer = QComboBox()
        for vk, vlabel in VIEWERS:
            self._cc_viewer.addItem(vlabel, vk)
        vrow.addWidget(vlbl, 0); vrow.addWidget(self._cc_viewer, 1)
        lay.addLayout(vrow)

        grid = QGridLayout()
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(14); grid.setVerticalSpacing(4)
        self._cc_corners = {}
        for pos, r, c in (("tl", 0, 0), ("tr", 0, 1), ("bl", 1, 0), ("br", 1, 1)):
            cell = QWidget(); cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(1)
            plbl = QLabel(POSITION_LABELS[pos].upper())
            plbl.setStyleSheet(f"font-family:{_MONO}; font-size:8.5px; letter-spacing:.08em; color:{theme.INK_SOFT};")
            combo = QComboBox()
            for key, label in CORNER_OPTIONS:
                combo.addItem(label, key)
            combo.currentIndexChanged.connect(self._grab_corners)
            self._cc_corners[pos] = combo
            cv.addWidget(plbl); cv.addWidget(combo)
            grid.addWidget(cell, r, c)
        gwrap = QWidget(); gwrap.setLayout(grid); lay.addWidget(gwrap)

        frow = QHBoxLayout()
        flbl = QLabel("Frame colour:"); flbl.setStyleSheet("font-size:12.5px;")
        self._cc_frame = QComboBox()
        for key, label in FRAME_OPTIONS:
            self._cc_frame.addItem(label, key)
        self._cc_frame.currentIndexChanged.connect(self._grab_corners)
        frow.addWidget(flbl, 0); frow.addWidget(self._cc_frame, 1)
        lay.addLayout(frow)

        self._cc_viewer.currentIndexChanged.connect(self._on_viewer_changed)
        self._cc_current = self._cc_viewer.currentData()
        self._load_corners(self._cc_current)

    # ── Culling grid — images per row, grid width, "Start with" default ──────
    def _build_culling_section(self, lay):
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{theme.LINE}; margin:6px 0;")
        lay.addWidget(sep)
        lay.addWidget(_section("CULLING GRID"))

        g = QGridLayout()
        g.setContentsMargins(0, 4, 0, 0)
        g.setHorizontalSpacing(14); g.setVerticalSpacing(6)

        g.addWidget(QLabel("Images per row:"), 0, 0)
        self._cg_cols = QSpinBox()
        self._cg_cols.setRange(1, 10)
        self._cg_cols.setValue(max(1, min(10, get_int_setting("culling", "grid_columns", 5))))
        g.addWidget(self._cg_cols, 0, 1)

        g.addWidget(QLabel("Grid width (% of window):"), 1, 0)
        self._cg_fill = QSpinBox()
        self._cg_fill.setRange(40, 100)
        self._cg_fill.setSuffix(" %")
        self._cg_fill.setValue(max(40, min(100, get_int_setting("culling", "grid_fill_pct", 100))))
        g.addWidget(self._cg_fill, 1, 1)

        g.addWidget(QLabel("Open a day showing:"), 2, 0)
        self._cg_start = QComboBox()
        for key, label in (("unrated", "Unrated only"),
                           ("review", "To review (unrated + R/Y/G)"),
                           ("all", "All pictures")):
            self._cg_start.addItem(label, key)
        cur = get_setting("culling", "start_subset", fallback="unrated")
        self._cg_start.setCurrentIndex(max(0, self._cg_start.findData(cur)))
        g.addWidget(self._cg_start, 2, 1)

        gwrap = QWidget(); gwrap.setLayout(g); lay.addWidget(gwrap)

    def _save_culling(self):
        set_setting("culling", "grid_columns", self._cg_cols.value())
        set_setting("culling", "grid_fill_pct", self._cg_fill.value())
        set_setting("culling", "start_subset", self._cg_start.currentData())

    def _load_corners(self, vk):
        st = self._cc_state[vk]
        for pos, combo in self._cc_corners.items():
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(st.get(pos, "none"))))
            combo.blockSignals(False)
        self._cc_frame.blockSignals(True)
        self._cc_frame.setCurrentIndex(max(0, self._cc_frame.findData(st.get("frame", "colour"))))
        self._cc_frame.blockSignals(False)

    def _grab_corners(self, *_):
        vk = getattr(self, "_cc_current", None)
        if vk is None:
            return
        for pos, combo in self._cc_corners.items():
            self._cc_state[vk][pos] = combo.currentData()
        self._cc_state[vk]["frame"] = self._cc_frame.currentData()

    def _on_viewer_changed(self, *_):
        self._cc_current = self._cc_viewer.currentData()
        self._load_corners(self._cc_current)

    def _save(self):
        chosen = next((b for b in self._scheme_group.buttons() if b.isChecked()), None)
        name = getattr(chosen, "_scheme_name", None) if chosen else None
        if name:
            set_setting("lookfeel", "color_scheme", name)
        theme_val = "dark" if self._dark_rb.isChecked() else "light"
        set_setting("lookfeel", "theme", theme_val)
        self._grab_corners()
        for vk, cfg in self._cc_state.items():
            set_corner_config(vk, cfg)
        self._save_culling()
        persist_config()
        logger.info("Look & feel: scheme='%s', theme='%s', corners saved.", name, theme_val)
        self.accept()
