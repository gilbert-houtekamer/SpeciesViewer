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

All four yellow-panel tables come from here: Unfiled, Days, Trip, Album. Trip and
Album came second and had to adopt it without changing behaviour, which is what
the `select` / `edit_cells` / `row_header` / `min_section` arguments are for —
each one names a thing that differs between an eight-year-old pipeline table and
a two-day-old inbox, rather than quietly forcing them to be the same.
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
                        min_section=MIN_SECTION_W, row_height=28,
                        select="rows", edit_cells=True, row_header=False):
    """A styled day table with `headers`.

    `fit_columns` are the indices that size to their content — short glyph
    columns like "7" or "📁" look wrong stretched. Column 0 (the day) always
    fits its content; everything else stretches to fill the panel width.
    Nothing goes narrower than `min_section`, content-sized or not.

    The four knobs exist because Trip and Album came second and must not change
    behaviour when they adopt this. Pass `None` to `min_section`/`row_height` to
    leave Qt's own defaults alone — that is what "port without changing the look"
    means for a table that already ships.

      select      "rows"  single row at a time (Unfiled, Days, Album)
                  "cells" extended, per-cell (Trip: its context menu runs every
                          pipeline cell you selected, so it needs the cells)
      edit_cells  False turns editing off at the view. Say False for any table
                  that builds plain QTableWidgetItems — they are editable by
                  default, and a double-click meant to run a step would open an
                  editor over it instead.
      row_header  True keeps the vertical header. Trip needs it VISIBLE: a
                  double-click on a row number re-runs that day's steps
                  (handle_row_rerun), which is unreachable if it is hidden.
    """
    table = QTableWidget()
    table.setColumnCount(len(headers))
    table.setRowCount(rows)
    table.setHorizontalHeaderLabels(list(headers))

    # Sorting stays OFF everywhere: rows are built in the order the panel means
    # them to be read, and a stray header click that reorders them would break
    # the row<->folder mapping the double-click handlers rely on.
    table.setSortingEnabled(False)
    # DoubleClicked, not NoEditTriggers, when any cell is editable: the view has
    # to allow an editor at all for the note column to work. WHICH cells accept
    # one is then decided by ItemIsEditable, which every helper below strips
    # unless asked.
    table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked if edit_cells
                          else QAbstractItemView.EditTrigger.NoEditTriggers)
    if select == "cells":
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    else:
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    table.verticalHeader().setVisible(row_header)

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
    # computed against it rather than shrinking past it. None = leave Qt's own
    # default; a 10-column table on a laptop cannot afford a generous floor.
    if min_section is not None:
        header.setMinimumSectionSize(min_section)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    for col in fit_columns:
        if 0 <= col < len(headers):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
    header.setStretchLastSection(stretch_last)
    # Rows sized to breathe rather than to the font's bare minimum — these tables
    # are read down a column, and a cramped row height fights that.
    if row_height is not None:
        table.verticalHeader().setDefaultSectionSize(row_height)
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
