"""Export Trip is three steps, the mirror of Import Trip (gui.trip_export_window).
Each step is a panel inside the window, so nothing can open behind it. These
tests build it headless with no trip open; nothing is written or persisted."""

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    # No backup locations: the panels list nothing and never touch a card.
    monkeypatch.setattr("gui.work_backup_dialog.backup_location", lambda: "")
    monkeypatch.setattr("gui.raw_backup_dialog.raw_backup_location", lambda: "")
    from gui.trip_export_window import TripExportWindow
    w = TripExportWindow(QWidget(), export_spec=None)
    yield w
    w.meta_panel._thread = None
    w.close()


def test_three_steps_each_a_panel_inside_the_window(window):
    assert len(window._tiles) == 3 and window._stack.count() == 3
    for i in range(3):
        window.go(i)
        assert window._stack.currentIndex() == i
        # The step's panel lives in this window, not in a window of its own.
        assert not window._pages[i].panel.isWindow()


def test_no_trip_open_says_so(window):
    text, kind = window._pages[0].status()
    assert kind == "todo" and "open a trip" in text


def test_while_copying_the_window_stays_open_and_on_its_step(window, app):
    window.go(1)
    window.show()
    window.meta_panel._thread = object()          # a backup is running
    window.go(2)
    assert window._stack.currentIndex() == 1      # cannot wander off
    window.close()
    assert window.isVisible()                     # cannot be closed
    window.meta_panel._thread = None
    window.close()
    assert not window.isVisible()


def test_status_of_an_empty_picture_root(tmp_path, monkeypatch):
    import gui.trip_export_window as tew
    monkeypatch.setattr("gui.work_backup_dialog.backup_location", lambda: str(tmp_path / "card"))
    monkeypatch.setattr("gui.raw_backup_dialog.raw_backup_location", lambda: str(tmp_path / "raw"))
    (tmp_path / "pics" / "2026-10-10").mkdir(parents=True)
    (tmp_path / "pics" / ".trash").mkdir()
    assert tew._real_days(tmp_path / "pics") == [tmp_path / "pics" / "2026-10-10"]
    assert tew._metadata_status(tmp_path / "pics")[1] == "faint"      # nothing to back up
    assert tew._pictures_status("", tmp_path / "pics")[1] == "faint"   # no pictures here


def test_pictures_tile_counts_what_the_table_calls_copied(tmp_path, monkeypatch):
    """The tile sums the panel's own rows: a day copied before records were
    kept ('legacy', shown as Copied) counts as copied too."""
    import gui.trip_export_window as tew
    monkeypatch.setattr("gui.raw_backup_dialog.raw_backup_location", lambda: str(tmp_path))
    rows = [{"total": 5, "status": s} for s in ("legacy", "full", "not_copied", "incomplete")]
    monkeypatch.setattr("gui.raw_backup_dialog.day_statuses", lambda *a: rows)
    assert tew._pictures_status("", tmp_path) == ("2 of 4 days copied", "todo")
