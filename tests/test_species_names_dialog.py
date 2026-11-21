"""Other Species Names… (backlog 1.1): one row per name, a filter over every
column, removed names greyed but listed. Headless, on a sandbox table."""

import csv

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


NAMES = {"anas platyrhynchos": ("mallard", "wilde eend"),
         "lepidopygia nana": ("madagascar munia", "")}
ROWS = [
    {"Name": "drijfsijs", "Language": "local", "PointsTo": "anas platyrhynchos",
     "Kind": "local name", "Source": "2026najaardb"},
    {"Name": "anas boschas", "Language": "sci", "PointsTo": "anas platyrhynchos",
     "Kind": "renamed", "Source": "AviList"},
]


def test_filter_matches_any_column_and_mine_only():
    from gui.species_names_dialog import matches
    assert matches(ROWS[0], NAMES, "wilde eend", True)             # via the species' Dutch name
    assert not matches(ROWS[1], NAMES, "wilde eend", True)         # taxonomy row, mine only
    assert matches(ROWS[1], NAMES, "wilde eend", False)
    assert matches(ROWS[1], NAMES, "avilist boschas", False)       # every word, any column
    assert not matches(ROWS[0], NAMES, "munia", False)


def test_window_lists_greys_removed_and_counts(app, monkeypatch, tmp_path):
    import gui.species_names_dialog as snd
    from engine import species_aliases as sa
    table = tmp_path / "speciesnames.csv"
    with open(table, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sa.FIELDS[:5], delimiter=";")
        w.writeheader()
        w.writerows(ROWS)
    sa.remove_aliases([str(table)], [("drijfsijs", "local", "anas platyrhynchos")])
    monkeypatch.setattr(snd, "table_paths", lambda: (str(table), ""))
    import gui.culling_species as cs
    monkeypatch.setattr(cs, "load_species_classification", lambda: None)
    parent = QWidget()
    dlg = snd.SpeciesNamesDialog(parent)
    assert dlg._table.rowCount() == 1                       # mine only: the removed local name
    assert dlg._table.item(0, 7).text()                     # its Removed date shows
    dlg._mine.setChecked(False)
    assert dlg._table.rowCount() == 2
    dlg._show_removed.setChecked(False)
    assert dlg._table.rowCount() == 1
    assert "1 names in use" in dlg._count.text() and "1 removed" in dlg._count.text()
    dlg.close()
