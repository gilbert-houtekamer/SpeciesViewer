"""A file dialog never opens in the program's own folder (utils.start_folder)."""

import sys
from pathlib import Path

import pytest

from utils import start_folder as sf


@pytest.fixture(autouse=True)
def no_config(monkeypatch):
    """No open trip, no CurrentPictures root: only the last fallback is left."""
    monkeypatch.setattr(sf, "_fallbacks", lambda: [Path.home().anchor if sys.platform == "win32" else Path.home()])


def _last_resort():
    return str(Path(Path.home().anchor if sys.platform == "win32" else Path.home()))


def test_existing_folder_is_used(tmp_path):
    assert sf.start_folder(str(tmp_path)) == str(tmp_path)


def test_missing_leaf_walks_up(tmp_path):
    assert sf.start_folder(str(tmp_path / "2027" / "2027 unfiled")) == str(tmp_path)


def test_a_file_gives_its_folder(tmp_path):
    f = tmp_path / "list.xlsx"
    f.write_text("x")
    assert sf.start_folder(str(f)) == str(tmp_path)


def test_first_usable_candidate_wins(tmp_path):
    assert sf.start_folder("", None, str(tmp_path)) == str(tmp_path)


@pytest.mark.parametrize("bad", ["", "   ", None, ".", "relative/folder"])
def test_empty_or_relative_never_means_the_current_folder(bad):
    assert sf.start_folder(bad) == _last_resort()


def test_program_folder_is_skipped(tmp_path):
    inside = sf._PROGRAM_DIR / "speciesviewer" / "assets"
    assert sf.start_folder(str(inside), str(tmp_path)) == str(tmp_path)
    assert sf.start_folder(str(sf._PROGRAM_DIR)) == _last_resort()


def test_start_file_adds_the_name(tmp_path):
    assert sf.start_file([str(tmp_path)], "Species table.xlsx") == str(tmp_path / "Species table.xlsx")


def test_trip_picture_folder_is_the_first_fallback(tmp_path, monkeypatch):
    day = tmp_path / "2026 najaar" / "2026-10-19"
    day.mkdir(parents=True)
    from utils.config import _config
    monkeypatch.undo()   # the real _fallbacks, reading the config
    if not _config.has_section("project"):
        _config.add_section("project")
    old = _config.get("project", "picturedirectory", fallback=None)
    _config.set("project", "picturedirectory", str(day))
    try:
        assert sf.start_folder("") == str(tmp_path / "2026 najaar")
    finally:
        if old is None:
            _config.remove_option("project", "picturedirectory")
        else:
            _config.set("project", "picturedirectory", old)


def test_card_chooser_prefers_a_card_with_dcim_not_the_keeper(tmp_path, monkeypatch):
    keeper, plain, camera = tmp_path / "K", tmp_path / "S", tmp_path / "C"
    for d in (keeper / "DCIM", plain, camera / "DCIM"):
        d.mkdir(parents=True)
    # Each fake card is its own "drive"; the keeper (with a DCIM too) comes first.
    monkeypatch.setattr(sf, "_drive", lambda p: str(p).split("DCIM")[0].rstrip("\/"))
    monkeypatch.setattr(sf, "_removable_roots", lambda: [keeper, plain, camera])
    assert sf.card_folder("", not_on=str(keeper)) == str(camera)
    assert sf.card_folder("") == str(keeper)


def test_card_chooser_last_card_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, "_removable_roots", lambda: [])
    assert sf.card_folder(str(tmp_path)) == str(tmp_path)
    assert sf.card_folder("") == _last_resort()
