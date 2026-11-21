"""Days ▸ Trash (backlog 1.22): a Day keeps its deletes inside itself,
<day>/.trash/<picture>/ — list, restore (never overwrite), discard."""

import pytest

from engine import trip_trash


def _picture(folder, stem, exts=(".ORF", ".xmp")):
    folder.mkdir(parents=True, exist_ok=True)
    for e in exts:
        (folder / f"{stem}{e}").write_bytes(b"x" * 10)


def test_lists_both_layouts_and_restores_into_the_day(tmp_path):
    day = tmp_path / "2026-06-26"
    day.mkdir()
    _picture(day / ".trash" / "p1", "P1")
    _picture(day / ".trash" / "2026-06-26" / "p2", "P2")      # older, one level deeper
    pics = trip_trash.own_trash_pictures(day)
    assert [p.name for p, _f in pics] == ["p1", "p2"]
    assert trip_trash.own_trash_size(day) == (2, 40)
    moved, clash = trip_trash.restore_picture(pics[0][0], day)
    assert sorted(moved) == ["P1.ORF", "P1.xmp"] and (day / "P1.ORF").exists()


def test_discard_removes_pictures_and_empty_folders_not_the_day(tmp_path):
    day = tmp_path / "2026-06-27"
    day.mkdir()
    (day / "KEEP.ORF").write_bytes(b"k")
    _picture(day / ".trash" / "p1", "P1")
    assert trip_trash.discard_own_trash(day) == 1
    assert not (day / ".trash").exists()
    assert (day / "KEEP.ORF").exists()


def test_window_in_one_day_mode(tmp_path):
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    from gui.trip_trash_dialog import TripTrashDialog
    day = tmp_path / "2026-06-28"
    day.mkdir()
    _picture(day / ".trash" / "p1", "P1", exts=(".jpg", ".xmp"))
    parent = QWidget()
    dlg = TripTrashDialog(parent, None, None, day.name, day_folder=day)
    assert dlg._day_combo.count() == 1 and dlg._keys == ["p1"]
    assert not dlg._day_locked(day.name)
    dlg._loader.cancel_all()
    dlg.close()
    app.processEvents()
