"""Help ▸ Reference Manual finds its page (utils.manual_path): the shipped copy
first, else the checkout's docs/manual page, rebuilt when a chapter is newer.
Everything here happens in tmp_path."""

import os

import utils.manual_path as mp


def _checkout(tmp_path):
    app = tmp_path / "speciesviewer"
    (app / "utils").mkdir(parents=True)
    manual = tmp_path / "docs" / "manual"
    manual.mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    return app, manual


def test_shipped_copy_wins(tmp_path, monkeypatch):
    app, _ = _checkout(tmp_path)
    (app / "manual").mkdir()
    (app / "manual" / mp.NAME).write_text("shipped")
    monkeypatch.setattr(mp, "_app_dir", lambda: app)
    assert mp.manual_html() == app / "manual" / mp.NAME


def test_checkout_rebuilds_a_stale_page(tmp_path, monkeypatch):
    app, manual = _checkout(tmp_path)
    page = manual / mp.NAME
    page.write_text("old")
    chapter = manual / "00-concepts.md"
    chapter.write_text("# Concepts")
    os.utime(page, (1, 1))                       # the page is older than the chapter
    (tmp_path / "tools" / "svr_manual_build.py").write_text(
        "def build(manual_dir, out):\n    out.write_text('new')\n")
    monkeypatch.setattr(mp, "_app_dir", lambda: app)
    assert mp.manual_html() == page
    assert page.read_text() == "new"


def test_nothing_there_is_none(tmp_path, monkeypatch):
    app, _ = _checkout(tmp_path)
    monkeypatch.setattr(mp, "_app_dir", lambda: app)
    assert mp.manual_html() is None
