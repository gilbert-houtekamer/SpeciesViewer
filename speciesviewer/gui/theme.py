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
    FRAME_UNRATED="#b0b0b0",  # picture frame for an unlabelled card — plain grey, deliberately not the olive ACCENT
    ACCENT="#6d8764", ACCENT_RGB="109, 135, 100", ACCENT_HOVER="#5c7454",
    ACCENT_SOFT="#e3ead9", ACCENT_SOFT_BORDER="#c9d6bb",
    ACCENT_INK="#ffffff", ACCENT_INK_SOFT="#2f3f29",
    DONE_FG="#2f7d43", DONE_BG="#e6f1e8", READY_FG="#2563a8", READY_BG="#e4eef8",
    LOCKED_FG="#7a8172", LOCKED_BG="#f0f2ec", LOCKED_BORDER="#c9d0be",
    GATE_FG="#2f9e57", GATE_BG="#e7f4ec", WARN="#b45309", ALERT="#b91c1c",
    ACTIVE_BG="#fff8d2", ACTIVE_BORDER="#e6dfa8", LOG_BG="#d0f0c0", LOG_INK="#2b3b22",
    PANEL_BG="#f9f9f9", ACTIVE_FG="#800080",  # current trip/album/day in the nav list
)
# Medium-dark neutral grey with a faint olive warmth — modelled on PhotoLab /
# Photoshop's dark UI. SURFACE sits clearly ABOVE BG so cards/panels/text-areas
# read as raised, not merged into the window; text + lines lean brighter for
# readability (the near-black + low-contrast first pass read as "too black").
_DARK = dict(
    INK="#ecefe6", INK_SOFT="#bcc2b1", INK_MUTED="#a6ad99", INK_FAINT="#969d88",
    LINE="#565a4c", SURFACE="#393c34", SURFACE_2="#454840", BG="#2c2e28",
    ROW_HOVER="#454840", NEUTRAL_GRAY="#a6af9b",
    FRAME_UNRATED="#75786f",  # picture frame for an unlabelled card — plain grey, deliberately not the olive ACCENT
    ACCENT="#8fa681", ACCENT_RGB="143, 166, 129", ACCENT_HOVER="#9fb691",
    ACCENT_SOFT="#3f4a37", ACCENT_SOFT_BORDER="#56634c",
    ACCENT_INK="#f3f6ee", ACCENT_INK_SOFT="#d6e2ca",
    DONE_FG="#6cc084", DONE_BG="#2e3d31", READY_FG="#6fb0e8", READY_BG="#29394a",
    LOCKED_FG="#9aa28d", LOCKED_BG="#3a3d35", LOCKED_BORDER="#52564a",
    GATE_FG="#63c98a", GATE_BG="#2e3d31", WARN="#e0954a", ALERT="#ff8a75",
    ACTIVE_BG="#3f3a22", ACTIVE_BORDER="#605826", LOG_BG="#30402b", LOG_INK="#c4dfb2",
    PANEL_BG="#303229", ACTIVE_FG="#e3c766",  # readable warm gold (darkMagenta was unreadable on dark)
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
FRAME_UNRATED = _T["FRAME_UNRATED"]  # thick picture frame for an unlabelled card (was the olive ACCENT — too close to the green label)

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
ALERT = _T["ALERT"]   # something the user must act on — brighter than WARN. Both
                      # are picked to stay readable on LOG_BG, where the log
                      # console prints them (plain "blue"/"red" did not).

# Kept as deliberate accents — the "active" trip/album panel (yellow) and the
# log console (green) are intentional per the original ch.15 backlog note.
ACTIVE_BG = _T["ACTIVE_BG"]
ACTIVE_BORDER = _T["ACTIVE_BORDER"]
LOG_BG = _T["LOG_BG"]
LOG_INK = _T["LOG_INK"]
ACTIVE_FG = _T["ACTIVE_FG"]   # the current/active item in the nav lists

# Plain neutral background used by the left-column list panels.
PANEL_BG = _T["PANEL_BG"]

# Multi-picture selection ring for framed culling cards (Species Image Preview
# grid + the Large Image View filmstrip) — a light brown, deliberately far from
# both the app-wide olive ACCENT (already "selected" everywhere else, but too
# close to the Green rating colour here) and the "Blue" rating colour. Same
# hex in both themes — it's a status colour, not a themed surface tone.
SELECT_MULTI = "#B97A57"

# The CURSOR — the one picture ←/→, s, c and z act on — in every grid, and the
# current cell of the Editor's filmstrip. A 2px ring, light on dark and dark on
# light; it wins over SELECT_MULTI, so the cursor stays findable inside a
# selection. One token so the colour can be tried and changed in one place.
CURRENT_RING = "#8fd3ff" if is_dark() else "#1f5fa8"

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

        /* Dialog buttons (Yes / No / OK / Save / Cancel) are short words, and the
           QPushButton rule above costs them the native style's minimum width — so
           "Yes" came out barely wider than the word itself. Wider side padding
           gives them a decent size. NOT min-width: inside a QMessageBox Qt
           applies that as the button's WIDTH, so every longer label ("Keep merged
           master", "Restore previous master") was cut to 76px and unreadable
           (found 2026-10-14). Padding grows with the text instead. */
        QMessageBox QPushButton, QDialogButtonBox QPushButton {{
            padding: 6px 26px;
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

        /* A disabled tick box kept the full-strength label and the ACCENT-green
           box, so an option that could not be clicked looked exactly like one
           that could (the album sub-options under "Rebuild the albums"). Grey
           the label and drain the box; the checkmark stays visible, because a
           disabled option still has to show whether it is on or off. */
        QCheckBox:disabled, QRadioButton:disabled {{
            color: {INK_FAINT};
        }}
        QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
            border-color: {LOCKED_BORDER};
            background-color: {LOCKED_BG};
        }}
        QCheckBox::indicator:checked:disabled,
        QRadioButton::indicator:checked:disabled {{
            background-color: {LOCKED_BORDER};
            border-color: {LOCKED_BORDER};
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
    """ + (f"""
        /* Dark only: give text inputs a visible edge so they don't merge into
           the panel (they were hard to spot). Light keeps its native look. */
        QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit {{
            background-color: {SURFACE};
            color: {INK};
            border: 1px solid {LINE};
            border-radius: 4px;
            selection-background-color: {ACCENT};
            selection-color: {ACCENT_INK};
        }}
    """ if is_dark() else "")
