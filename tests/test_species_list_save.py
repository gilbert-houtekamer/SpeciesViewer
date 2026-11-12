"""Edit Species List save (SpeciesCollectionDialog.on_save): every column the
file has survives, and a failed save leaves the file as it was — a hard-coded
header once emptied the master list (2026-11-12)."""
import csv

import pytest

from gui import trip_process
from gui.trip_process import SpeciesCollectionDialog

HEADER = ["Level", "ScientificName", "DutchName", "EnglishName", "Up", "Leaves",
          "Sequence", "SubspeciesOf"]


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, delimiter=";")
        w.writeheader()
        w.writerows(rows)


def _rows():
    return [dict(zip(HEADER, ["species", "oplurus cuvieri", "", "collared iguana",
                              "opluridae", "[]", "12", ""]))]


class _Fake:                     # just what on_save touches
    on_save = SpeciesCollectionDialog.on_save

    def __init__(self, path, rows, fieldnames):
        self.csv_path, self.all_rows, self._fieldnames = str(path), rows, fieldnames
        self.save_btn = type("B", (), {"setEnabled": lambda *_: None})()


@pytest.fixture(autouse=True)
def no_dialogs(monkeypatch):
    monkeypatch.setattr(trip_process.QMessageBox, "warning", lambda *a, **k: None)


def test_save_keeps_every_column(tmp_path):
    path = tmp_path / "speciescollection.csv"
    _write(path, _rows())
    rows = _rows()
    rows[0]["DutchName"] = "halsbandleguaan"
    _Fake(path, rows, HEADER).on_save()
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        back = list(reader)
    assert reader.fieldnames == HEADER
    assert back[0]["DutchName"] == "halsbandleguaan" and back[0]["Sequence"] == "12"


def test_failed_save_leaves_the_file_untouched(tmp_path):
    path = tmp_path / "speciescollection.csv"
    _write(path, _rows())
    before = path.read_bytes()
    bad = _rows()
    bad[0][None] = ["stray"]          # DictWriter cannot write this row
    _Fake(path, bad, HEADER).on_save()
    assert path.read_bytes() == before


def test_empty_list_is_never_written(tmp_path):
    path = tmp_path / "speciescollection.csv"
    _write(path, _rows())
    before = path.read_bytes()
    _Fake(path, [], HEADER).on_save()
    assert path.read_bytes() == before
