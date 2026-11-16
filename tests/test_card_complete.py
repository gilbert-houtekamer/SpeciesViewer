"""A card read says whether every picture it was meant to bring is here."""
from batch.sv0ReadSDCard import copy_card


def _card(tmp_path):
    card = tmp_path / "card" / "DCIM"
    card.mkdir(parents=True)
    for i in range(3):
        (card / f"IMG_000{i}.JPG").write_bytes(b"\xff\xd8" + bytes([i]) * 1000)
    return tmp_path / "card"


def test_all_present_after_copy_and_after_a_reread(tmp_path):
    card, dest = _card(tmp_path), tmp_path / "dest"
    dest.mkdir()
    s = copy_card(card, dest, write_sidecars=False, use_card_cache=False)
    assert s["copied"] == 3 and s["expected"] == 3 and s["present"] == 3
    s = copy_card(card, dest, write_sidecars=False, use_card_cache=False)
    assert s["copied"] == 0 and s["skipped"] == 3 and s["present"] == s["expected"] == 3


def test_complete_sentence(tmp_path):
    from gui.sdcard_read import ReadSDCardDialog
    say = ReadSDCardDialog._complete_txt
    assert "All 3 picture(s)" in say({"expected": 3, "present": 3})
    assert "Only 2 of the 3" in say({"expected": 3, "present": 2})
    assert say({"expected": 3, "present": 0, "error": "disk full"}) == ""
    assert say({}) == ""
