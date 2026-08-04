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

# checkbox-check.png: white checkmark glyph drawn over the ACCENT-filled
# checked indicator below. QSS can't tint a glyph, so the checkmark itself
# has to ship as its own asset rather than a color token.
_CHECKMARK = (Path(__file__).resolve().parent.parent / "assets" / "checkbox-check.png").as_posix()

# Neutrals
INK = "#1c211b"
INK_SOFT = "#59634f"
INK_MUTED = "#5b6b56"   # unchecked/unselected pill text — between INK_SOFT and INK_FAINT
INK_FAINT = "#8b9784"  # secondary/label text, e.g. a collapsed panel's caption
LINE = "#d7ddce"
SURFACE = "#ffffff"
BG = "#f5f7f3"
ROW_HOVER = "#eef0ea"  # subtle hover background for a not-yet-selected row/pill
NEUTRAL_GRAY = "#777777"  # a plain (non-olive-tinted) secondary-label grey

# Brand accent (olive — matches the published website's own photo-frame color)
ACCENT = "#6d8764"
ACCENT_RGB = "109, 135, 100"  # same color as ACCENT, for an rgba(...) tint
ACCENT_HOVER = "#5c7454"
ACCENT_SOFT = "#e3ead9"
ACCENT_SOFT_BORDER = "#c9d6bb"
ACCENT_INK = "#ffffff"       # text/foreground on a FILLED accent surface
ACCENT_INK_SOFT = "#2f3f29"  # text/foreground on the pale ACCENT_SOFT surface

# Status colors, shared by the Trip lifecycle board / Biota Browser / Species Panel
DONE_FG = "#2f7d43"
DONE_BG = "#e6f1e8"
READY_FG = "#2563a8"
READY_BG = "#e4eef8"
LOCKED_FG = "#7a8172"
LOCKED_BG = "#f0f2ec"
LOCKED_BORDER = "#c9d0be"
GATE_FG = "#2f9e57"
GATE_BG = "#e7f4ec"
WARN = "#b45309"

# Kept as deliberate accents, not folded into the neutral palette above — the
# "active" trip/album panel (yellow) and the log console (green) are
# intentional per the original ch.15 backlog note.
ACTIVE_BG = "#fff8d2"
ACTIVE_BORDER = "#e6dfa8"
LOG_BG = "#d0f0c0"
LOG_INK = "#2b3b22"

# Plain neutral background used by the left-column list panels.
PANEL_BG = "#f9f9f9"

# Previously duplicated verbatim in TripInfoPanel/DaysPanel/SidebarInfoRail/VirtTripInfoPanel.
FONT_MONO = "font-size: 10pt; font-family: Consolas, monospace;"


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
            color: #333;
            border: 1px solid #ccc;
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
