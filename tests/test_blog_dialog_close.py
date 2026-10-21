"""Configure Blog Post never loses the text (2026-10-21): Close saves, the
window's X / Esc asks when something changed. A temporary day folder; the
config change is in memory only and put back."""

import logging

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    from utils.config import _config
    if not _config.has_section("project"):
        _config.add_section("project")
    old = _config.get("project", "picturedirectory", fallback=None)
    _config.set("project", "picturedirectory", str(tmp_path))

    class Bar:
        def showMessage(self, *_a):
            pass

    parent = QWidget()
    parent.logger = logging.getLogger("speciesviewer")
    parent.status_bar = Bar()
    from gui.trip_process import CreateBlogDialog
    d = CreateBlogDialog(parent, menu_spec=None)
    yield d, tmp_path / "blog.ini", monkeypatch
    d.deleteLater()
    if old is None:
        _config.remove_option("project", "picturedirectory")
    else:
        _config.set("project", "picturedirectory", old)


def _answer(monkeypatch, button):
    asked = []

    def fake_exec(box):
        asked.append(box.text())
        return button
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return asked


def test_close_saves_the_text(dialog):
    d, ini, _mp = dialog
    d.title_nl.setText("Dag 3: kraanvogels")
    d._save_and_close()
    assert "Dag 3: kraanvogels" in ini.read_text(encoding="utf-8")


def test_x_with_changes_asks_and_discard_keeps_the_file(dialog):
    d, ini, mp = dialog
    before = ini.read_text(encoding="utf-8")
    d.title_nl.setText("weg ermee")
    asked = _answer(mp, QMessageBox.StandardButton.Discard)
    d.reject()
    assert asked and ini.read_text(encoding="utf-8") == before


def test_x_with_changes_and_save_writes_them(dialog):
    d, ini, mp = dialog
    d.message_en.setPlainText("Cranes everywhere.")
    _answer(mp, QMessageBox.StandardButton.Save)
    d.reject()
    assert "Cranes everywhere." in ini.read_text(encoding="utf-8")


def test_x_without_changes_does_not_ask(dialog):
    d, _ini, mp = dialog
    asked = _answer(mp, QMessageBox.StandardButton.Cancel)
    d.reject()
    assert asked == []
