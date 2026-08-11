"""The one way a yellow-panel day table is built.

ONE JOB: hand back a QTableWidget that already looks and behaves like every
other yellow-panel table — same selection colour against the yellow background,
same "rows are read, cells are double-clicked" contract, same column-sizing
habits — so a fourth panel cannot quietly drift from the first three.

INVARIANT — every table in the yellow panel acts on DOUBLE-click, never single.
Single-click selects, and selecting must never do anything.

Editing is opt-IN per cell. The view's only edit trigger is a double-click, and
every cell these helpers build has ItemIsEditable STRIPPED unless it asks for it
(text_cell(editable=True) — in practice just the day-note column). So a
double-click on a note opens an editor while the same gesture on the cell beside
it runs an action, and no table can become accidentally typeable by inheriting a
default.

This module knows nothing about days, trips or the pipeline. It is styling and
Qt boilerplate, deliberately — the moment it starts knowing what a column MEANS
it stops being usable by the panel that needs a different column.

Trip and Album predate this module and still build their tables inline
(trip_overview.py, virttrip_overview.py). Pointing them here is a follow-up held
back only because they are shipped and tested; the setup they duplicate is the
setup below, so the swap is mechanical when the time comes.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (QTableWidget, QTableWidgetItem, QAbstractItemView,
                             QHeaderView)
from PyQt6.QtCore import Qt

from gui import theme


# A floor under every column. ResizeToContents alone sizes a glyph column to the
# glyph, which packs 📁 📅 → into a few pixels each and makes them read as one
# smudge rather than three separate things you can hit. There is plenty of width
# going spare in the note column, so spend some of it here.
MIN_SECTION_W = 74


def make_overview_table(headers, *, rows=0, fit_columns=(), stretch_last=False,
                        min_section=MIN_SECTION_W):
    """A styled, non-editable day table with `headers`.

    `fit_columns` are the indices that size to their content — short glyph
    columns like "7" or "📁" look wrong stretched. Column 0 (the day) always
    fits its content; everything else stretches to fill the panel width.
    Nothing goes narrower than `min_section`, content-sized or not.
    """
    table = QTableWidget()
    table.setColumnCount(len(headers))
    table.setRowCount(rows)
    table.setHorizontalHeaderLabels(list(headers))

    # Sorting stays OFF everywhere: rows are built in the order the panel means
    # them to be read, and a stray header click that reorders them would break
    # the row<->folder mapping the double-click handlers rely on.
    table.setSortingEnabled(False)
    # DoubleClicked, not NoEditTriggers: the view has to allow an editor at all
    # for the note column to work. Which cells actually accept one is decided by
    # ItemIsEditable, which every helper below strips unless asked.
    table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    table.verticalHeader().setVisible(False)

    # Strong selection colour — the default translucent highlight is invisible
    # against the yellow tab background.
    table.setStyleSheet(f"""
        QTableWidget::item:selected {{
            background-color: {theme.ACCENT};
            color: {theme.ACCENT_INK};
        }}
    """)

    header = table.horizontalHeader()
    # Set the floor BEFORE the resize modes, so the content-sized columns are
    # computed against it rather than shrinking past it.
    header.setMinimumSectionSize(min_section)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    for col in fit_columns:
        if 0 <= col < len(headers):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(stretch_last)
    # Rows sized to breathe rather than to the font's bare minimum — these tables
    # are read down a column, and a cramped row height fights that.
    table.verticalHeader().setDefaultSectionSize(28)
    return table


def action_cell(symbol, tooltip, *, inert=False):
    """One clickable action cell: a glyph, centred, explained by its tooltip.

    `inert=True` strips every flag, which is how a cell says "this action does
    not apply to this row" — it cannot be selected or double-clicked, so no
    handler has to re-check. The glyph still shows, because a blank cell reads
    as a bug rather than as a deliberate no.
    """
    item = QTableWidgetItem(symbol)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    if tooltip:
        item.setToolTip(tooltip)
    if inert:
        item.setFlags(Qt.ItemFlag.NoItemFlags)
    else:
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def text_cell(text, tooltip="", *, editable=False):
    """A plain text cell, NOT editable unless asked.

    `editable=True` is the deliberate opt-in for the day note. Everything else
    has the flag stripped, so a double-click on it reaches the action handler
    instead of opening an editor over the top of it.
    """
    item = QTableWidgetItem(text)
    if tooltip:
        item.setToolTip(tooltip)
    flags = item.flags()
    item.setFlags(flags | Qt.ItemFlag.ItemIsEditable if editable
                  else flags & ~Qt.ItemFlag.ItemIsEditable)
    return item


def order_like_panel(main_window, folders):
    """Put `folders` in the order the left column is showing them.

    The two scan-backed lanes show the SAME collection in the left list and the
    yellow table, so they have to agree — a mirrored selection between two
    differently-sorted lists reads as a bug. The left lists obey the shared
    A-Z / Recent / Frequent strip, so the table follows the same setting rather
    than inventing an order of its own.

    (Trip and Album are not affected: their tables are the days of one trip and
    are chronological by nature, not a list the strip sorts.)
    """
    from utils.sort_modes import sort_items, DEFAULT_MODE
    from utils.gui_config import get_all_gui_usage
    mode = getattr(main_window, "_current_sort_mode", DEFAULT_MODE)
    try:
        return sort_items(list(folders), mode, get_all_gui_usage())
    except Exception:
        return list(folders)


def row_folder(table, row, *, column=0, role=Qt.ItemDataRole.UserRole + 1):
    """The folder a table row stands for, or "".

    Every panel stashes the folder on the day cell rather than re-deriving it
    from the visible text — the text is a date, and two lanes can hold the same
    date in different directories.
    """
    item = table.item(row, column)
    return (item.data(role) or "") if item is not None else ""


def select_row_for_folder(table, folder, *, column=0, role=Qt.ItemDataRole.UserRole + 1):
    """Select and scroll to the row standing for `folder`. Returns True if found.

    Used by the left-column lists to mirror their selection into the table.
    """
    import os
    if not folder:
        return False
    want = os.path.normcase(os.path.normpath(str(folder)))
    for row in range(table.rowCount()):
        got = row_folder(table, row, column=column, role=role)
        if got and os.path.normcase(os.path.normpath(str(got))) == want:
            table.selectRow(row)
            table.scrollToItem(table.item(row, column))
            return True
    return False
