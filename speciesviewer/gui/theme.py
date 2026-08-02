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
    # QPushButton deliberately has NO `padding` property: many buttons
    # app-wide call setFixedSize() with their own local border-only
    # stylesheet (e.g. gui/image_viewer.py's 40x24 "All" / 64x24 "Species"
    # toggles). App-level padding cascades into whatever THEY didn't set
    # locally and grows the wanted content size past the fixed box, clipping
    # the text — measured live: padding:4px 14px pushed "Species"'s natural
    # sizeHint from 88px to 116px against its 64px budget. If you ever want
    # to add padding back, re-run the headless sizeHint measurement first
    # (see the ch.15 theme memory) against every setFixedSize() QPushButton.
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
            background-color: {ACCENT};
            border-color: {ACCENT};
            color: {ACCENT_INK};
        }}
        QPushButton:default:hover {{
            background-color: {ACCENT_HOVER};
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
