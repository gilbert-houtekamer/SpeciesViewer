"""Pictures SV cannot read (Canon .HIF, Pentax .PEF) are named as PICTURES.

Two promises: the card read never calls them "not a picture", and Empty a card
never calls a card holding them safe to format.
"""
import logging

from batch.sv0ReadSDCard import _warn_ignored, scan_days, split_ignored
from engine.card_empty import can_empty, classify_card


def _card(tmp_path, *names):
    d = tmp_path / "card" / "DCIM" / "100CANON"
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_bytes(b"x")
    return tmp_path / "card"


def test_split_keeps_pictures_apart():
    pics, rest = split_ignored({".hif": 3, ".dat": 1})
    assert pics == {".hif": 3} and rest == {".dat": 1}


def test_card_read_names_hif_as_pictures(tmp_path, caplog):
    card = _card(tmp_path, "IMG_0001.HIF", "IMG_0002.HIF", "CANONMSC.DAT")
    caplog.set_level(logging.WARNING, logger="speciesviewer")
    days, total, ignored = scan_days(card, use_card_cache=False)
    assert total == 0 and ignored == {".hif": 2, ".dat": 1}
    text = caplog.text
    assert "2 Canon HEIF (.hif) in DCIM/100CANON" in text
    assert "Do not format this card" in text
    assert ".hif" not in text.split("not a picture")[0].split("Not copied:")[-1]


def test_empty_a_card_blocks_on_hif(tmp_path):
    card = _card(tmp_path, "IMG_0001.HIF")
    report = classify_card(card, tmp_path / "project")
    assert any("1 Canon HEIF (.hif) in DCIM/100CANON" in b for b in report["blockers"])
    assert not can_empty(report)


def test_warning_once_per_card(tmp_path):
    log = logging.getLogger("t_unreadable")
    seen = []
    log.warning = lambda m: seen.append(m)       # noqa: E731
    log.debug = lambda m: None                   # noqa: E731
    _warn_ignored({".pef": 1}, tmp_path / "x", log)
    _warn_ignored({".pef": 1}, tmp_path / "x", log)
    assert len(seen) == 1 and "Pentax RAW" in seen[0]
