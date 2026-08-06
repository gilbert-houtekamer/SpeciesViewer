"""Shared visual tokens for SpeciesViewer (ch.15 global QSS theme, Phase 1).

The palette below is not new — it's the one gui/trip_lifecycle_board.py,
gui/biota_browser.py and gui/species_panel.py already each defined locally,
independently, and near-identically (spot-verified byte-for-byte on the
neutrals and the olive accent). This module is the single source of truth
going forward. Those three files still carry their own local copies for now
— Phase 1 deliberately doesn't touch them (see the ch.15 plan/memory notes
for why) — but every new consumer should import from here instead of
re-defining hex constants.
"""

from pathlib import Path
from PyQt6.QtGui import QPalette, QColor
from utils.config import get_setting

# checkbox-check.png: white checkmark glyph drawn over the ACCENT-filled
# checked indicator below. QSS can't tint a glyph, so the checkmark itself
# has to ship as its own asset rather than a color token.
_CHECKMARK = (Path(__file__).resolve().parent.parent / "assets" / "checkbox-check.png").as_posix()


def is_dark() -> bool:
    """True when the user picked the dark theme ([lookfeel] theme). Read once at
    import — like the colour scheme, a change applies on the next restart, since
    these tokens are captured into module constants across the app."""
    return (get_setting("lookfeel", "theme", fallback="light")
            or "light").strip().lower() == "dark"


# Two token sets, same KEYS. Everything downstream reads the module constants
# below (INK, SURFACE, ACCENT, …), so flipping the active set re-themes the app.
# Dark values are lifted from the Biota-browser dark mockup + reasoned status hues.
_LIGHT = dict(
    INK="#1c211b", INK_SOFT="#59634f", INK_MUTED="#5b6b56", INK_FAINT="#8b9784",
    LINE="#d7ddce", SURFACE="#ffffff", SURFACE_2="#eef1ea", BG="#f5f7f3",
    ROW_HOVER="#eef0ea", NEUTRAL_GRAY="#777777",
    ACCENT="#6d8764", ACCENT_RGB="109, 135, 100", ACCENT_HOVER="#5c7454",
    ACCENT_SOFT="#e3ead9", ACCENT_SOFT_BORDER="#c9d6bb",
    ACCENT_INK="#ffffff", ACCENT_INK_SOFT="#2f3f29",
    DONE_FG="#2f7d43", DONE_BG="#e6f1e8", READY_FG="#2563a8", READY_BG="#e4eef8",
    LOCKED_FG="#7a8172", LOCKED_BG="#f0f2ec", LOCKED_BORDER="#c9d0be",
    GATE_FG="#2f9e57", GATE_BG="#e7f4ec", WARN="#b45309",
    ACTIVE_BG="#fff8d2", ACTIVE_BORDER="#e6dfa8", LOG_BG="#d0f0c0", LOG_INK="#2b3b22",
    PANEL_BG="#f9f9f9",
)
# Medium-dark neutral grey with a faint olive warmth — modelled on the tone of
# PhotoLab / Photoshop's dark UI (the previous near-black read as "too black").
_DARK = dict(
    INK="#e7e9e2", INK_SOFT="#a9af9f", INK_MUTED="#99a08d", INK_FAINT="#7e8576",
    LINE="#474a40", SURFACE="#32352e", SURFACE_2="#3b3e36", BG="#282a25",
    ROW_HOVER="#3b3e36", NEUTRAL_GRAY="#9aa38f",
    ACCENT="#8aa07d", ACCENT_RGB="138, 160, 125", ACCENT_HOVER="#9ab08d",
    ACCENT_SOFT="#3a4433", ACCENT_SOFT_BORDER="#4e5b45",
    ACCENT_INK="#f3f6ee", ACCENT_INK_SOFT="#d3e0c6",
    DONE_FG="#6cc084", DONE_BG="#28382c", READY_FG="#6fb0e8", READY_BG="#243542",
    LOCKED_FG="#909985", LOCKED_BG="#33362f", LOCKED_BORDER="#4a4d42",
    GATE_FG="#63c98a", GATE_BG="#28382c", WARN="#e0954a",
    ACTIVE_BG="#3a3620", ACTIVE_BORDER="#5a5326", LOG_BG="#2a3a26", LOG_INK="#bcd9aa",
    PANEL_BG="#2b2d27",
)
_T = _DARK if is_dark() else _LIGHT

# Neutrals
INK = _T["INK"]
INK_SOFT = _T["INK_SOFT"]
INK_MUTED = _T["INK_MUTED"]   # unchecked/unselected pill text
INK_FAINT = _T["INK_FAINT"]   # secondary/label text, e.g. a collapsed panel's caption
LINE = _T["LINE"]
SURFACE = _T["SURFACE"]
SURFACE_2 = _T["SURFACE_2"]   # subtle off-surface bg (table headers, chips, hovers)
BG = _T["BG"]
ROW_HOVER = _T["ROW_HOVER"]   # subtle hover background for a not-yet-selected row/pill
NEUTRAL_GRAY = _T["NEUTRAL_GRAY"]  # a plain (non-olive-tinted) secondary-label grey

# Brand accent (olive — matches the published website's own photo-frame color)
ACCENT = _T["ACCENT"]
ACCENT_RGB = _T["ACCENT_RGB"]  # same color as ACCENT, for an rgba(...) tint
ACCENT_HOVER = _T["ACCENT_HOVER"]
ACCENT_SOFT = _T["ACCENT_SOFT"]
ACCENT_SOFT_BORDER = _T["ACCENT_SOFT_BORDER"]
ACCENT_INK = _T["ACCENT_INK"]       # text/foreground on a FILLED accent surface
ACCENT_INK_SOFT = _T["ACCENT_INK_SOFT"]  # text/foreground on the pale ACCENT_SOFT surface

# Status colors, shared by the Trip lifecycle board / Biota Browser / Species Panel
DONE_FG = _T["DONE_FG"]
DONE_BG = _T["DONE_BG"]
READY_FG = _T["READY_FG"]
READY_BG = _T["READY_BG"]
LOCKED_FG = _T["LOCKED_FG"]
LOCKED_BG = _T["LOCKED_BG"]
LOCKED_BORDER = _T["LOCKED_BORDER"]
GATE_FG = _T["GATE_FG"]
GATE_BG = _T["GATE_BG"]
WARN = _T["WARN"]

# Kept as deliberate accents — the "active" trip/album panel (yellow) and the
# log console (green) are intentional per the original ch.15 backlog note.
ACTIVE_BG = _T["ACTIVE_BG"]
ACTIVE_BORDER = _T["ACTIVE_BORDER"]
LOG_BG = _T["LOG_BG"]
LOG_INK = _T["LOG_INK"]

# Plain neutral background used by the left-column list panels.
PANEL_BG = _T["PANEL_BG"]

# Previously duplicated verbatim in TripInfoPanel/DaysPanel/SidebarInfoRail/VirtTripInfoPanel.
FONT_MONO = "font-size: 10pt; font-family: Consolas, monospace;"


def build_palette() -> QPalette:
    """A QPalette from the active tokens. Applied app-wide at startup so bare
    widgets (QWidget/QLabel/QScrollArea backgrounds, text, inputs) follow the
    theme — build_base_qss only styles the specific rules it names, not the
    default widget backgrounds. Applied for dark; light keeps Qt's own palette."""
    p = QPalette()
    C = QColor
    p.setColor(QPalette.ColorRole.Window, C(BG))
    p.setColor(QPalette.ColorRole.WindowText, C(INK))
    p.setColor(QPalette.ColorRole.Base, C(SURFACE))
    p.setColor(QPalette.ColorRole.AlternateBase, C(ROW_HOVER))
    p.setColor(QPalette.ColorRole.Text, C(INK))
    p.setColor(QPalette.ColorRole.Button, C(SURFACE))
    p.setColor(QPalette.ColorRole.ButtonText, C(INK))
    p.setColor(QPalette.ColorRole.ToolTipBase, C(SURFACE))
    p.setColor(QPalette.ColorRole.ToolTipText, C(INK))
    p.setColor(QPalette.ColorRole.Highlight, C(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, C(ACCENT_INK))
    p.setColor(QPalette.ColorRole.PlaceholderText, C(INK_FAINT))
    p.setColor(QPalette.ColorRole.BrightText, C("#ff6b6b"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, C(INK_FAINT))
    return p


def build_base_qss() -> str:
    """The app-wide baseline stylesheet, applied once via
    QApplication.setStyleSheet(). Deliberately conservative for Phase 1:
    buttons, menus, focus/selection color, and the tooltip (previously its
    own standalone rule, now folded in here). NOT here yet, on purpose: a
    blanket font rule, per-dialog redesign, and the three already-polished
    screens (their own local styling always wins for anything they set
    themselves — Qt gives a widget's own stylesheet priority over an
    app-level one for matching properties on itself)."""
    # QPushButton padding (ch.15 Phase 3): v0.1 had no app-wide QSS, so plain
    # buttons (Browse, Close, Create Blog, ...) got the native style's own
    # comfortable content margins for free. Any QSS rule on QPushButton makes
    # Qt drop those native margins app-wide, which is what made v0.2 buttons
    # look cramped — so padding has to come back explicitly here. Every
    # button that calls setFixedSize()/setFixedWidth() locks its own box and
    # must neutralize this in ITS OWN local stylesheet (padding: 0 for an
    # icon/glyph button with no room to grow, or padding: Npx 0 to gain
    # vertical room only on a fixed-WIDTH button) — see gui/image_viewer.py,
    # gui/biota_browser.py's _back_btn for the pattern. A local stylesheet's
    # own padding always wins over this one for that widget.
    return f"""
        QToolTip {{
            background-color: {SURFACE};
            color: {INK};
            border: 1px solid {LINE};
            padding: 4px;
        }}

        QPushButton {{
            background-color: {SURFACE};
            border: 1px solid {LINE};
            border-radius: 4px;
            color: {INK};
            padding: 6px 14px;
        }}
        QPushButton:hover {{
            background-color: {ACCENT_SOFT};
            border-color: {ACCENT};
        }}
        QPushButton:pressed {{
            background-color: {ACCENT_SOFT_BORDER};
        }}
        QPushButton:disabled {{
            color: {LOCKED_FG};
            background-color: {LOCKED_BG};
            border-color: {LOCKED_BORDER};
        }}
        QPushButton:default {{
            border: 1.5px solid {ACCENT};
        }}
        QPushButton:default:hover {{
            background-color: {ACCENT_SOFT};
        }}

        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {LINE};
            border-radius: 3px;
            background-color: {SURFACE};
        }}
        QCheckBox::indicator:hover {{
            border-color: {ACCENT};
        }}
        QCheckBox::indicator:checked {{
            background-color: {ACCENT};
            border-color: {ACCENT};
            image: url({_CHECKMARK});
        }}

        QRadioButton::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {LINE};
            border-radius: 7px;
            background-color: {SURFACE};
        }}
        QRadioButton::indicator:hover {{
            border-color: {ACCENT};
        }}
        QRadioButton::indicator:checked {{
            background-color: {ACCENT};
            border-color: {ACCENT};
        }}

        QProgressBar {{
            border: 1px solid {LINE};
            border-radius: 4px;
            background-color: {BG};
            text-align: center;
            color: {INK};
        }}
        QProgressBar::chunk {{
            background-color: {ACCENT};
            border-radius: 4px;
        }}

        QMenuBar {{
            background-color: {SURFACE};
            border-bottom: 1px solid {LINE};
        }}
        QMenuBar::item:selected {{
            background-color: {ACCENT_SOFT};
        }}
        QMenu {{
            background-color: {SURFACE};
            border: 1px solid {LINE};
        }}
        QMenu::item:selected {{
            background-color: {ACCENT_SOFT};
            color: {INK};
        }}

        QLineEdit:focus, QComboBox:focus {{
            border: 1px solid {ACCENT};
        }}

        QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
            background-color: {ACCENT_SOFT};
            color: {INK};
        }}
    """
