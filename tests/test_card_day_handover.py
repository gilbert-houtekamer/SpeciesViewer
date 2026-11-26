"""A card read hands each day to the AI as soon as all its pictures are on disk."""
import os
from datetime import datetime

from batch.sv0ReadSDCard import copy_card


def _shot(folder, name, day, data):
    f = folder / name
    f.write_bytes(b"\xff\xd8" + data * 500)
    t = datetime.fromisoformat(f"{day}T12:00:00").timestamp()
    os.utime(f, (t, t))


def test_each_day_once_complete_and_already_there_days_too(tmp_path):
    card, dest = tmp_path / "card" / "DCIM", tmp_path / "dest"
    card.mkdir(parents=True)
    dest.mkdir()
    for i in range(3):
        _shot(card, f"A{i}.JPG", "2023-08-03", bytes([i]))
    for i in range(2):
        _shot(card, f"B{i}.JPG", "2023-08-04", bytes([10 + i]))
    kw = dict(bucket_by_date=True, write_sidecars=False, use_card_cache=False)

    seen = []

    def handed(day, paths):
        # every picture of the day is on disk when the day is handed over
        assert all(os.path.exists(p) for p in paths)
        seen.append((day, sorted(os.path.basename(p) for p in paths)))

    s = copy_card(tmp_path / "card", dest, on_day_copied=handed, **kw)
    assert s["copied"] == 5
    assert sorted(seen) == [("2023-08-03", ["A0.JPG", "A1.JPG", "A2.JPG"]),
                            ("2023-08-04", ["B0.JPG", "B1.JPG"])]

    # A re-read copies nothing, and still hands over both days (cached = quick).
    seen.clear()
    s = copy_card(tmp_path / "card", dest, on_day_copied=handed, **kw)
    assert s["copied"] == 0
    assert sorted(d for d, _ in seen) == ["2023-08-03", "2023-08-04"]


def test_an_error_in_the_hand_over_never_stops_the_copy(tmp_path):
    card, dest = tmp_path / "card" / "DCIM", tmp_path / "dest"
    card.mkdir(parents=True)
    dest.mkdir()
    _shot(card, "A0.JPG", "2023-08-03", b"x")

    def broken(day, paths):
        raise RuntimeError("AI fell over")

    s = copy_card(tmp_path / "card", dest, bucket_by_date=True, write_sidecars=False,
                  use_card_cache=False, on_day_copied=broken)
    assert s["copied"] == 1 and not s["failed"]


def test_card_batch_one_picture_per_shot(tmp_path):
    from engine.day_measure import card_batch
    day = tmp_path / "2023-08-03"
    day.mkdir()
    for name in ("P1.ORF", "P1.JPG", "P1.xmp", "P2.JPG", "P2_DxO.jpg", "V1.MOV"):
        (day / name).write_bytes(b"x")
    pics = card_batch(sorted(day.iterdir()))
    assert sorted(p["src"].name for p in pics) == ["P1.ORF", "P2.JPG"]


def test_a_picture_that_cannot_be_read_does_not_stop_the_day(tmp_path):
    """2023 augustus-P8030779.ORF: its embedded preview is damaged ("image file
    is truncated"); that once stopped AI… for a whole read and a day of the card."""
    from datetime import datetime, timedelta
    from PIL import Image
    from engine import day_ai

    class NoCache:
        def boxes(self, src): return None
        def followed(self, src): return None
        def put_followed(self, src, box): pass

    t0 = datetime(2023, 8, 3, 12)
    pics = [{"src": tmp_path / f"P{i}.ORF", "time": t0 + timedelta(minutes=i)} for i in range(3)]
    measured = []

    def load(src):
        if src.name == "P1.ORF":
            raise OSError("image file is truncated (0 bytes not processed)")
        return Image.new("RGB", (64, 48))

    def measure_one(p, im, box):
        measured.append(p["src"].name)
        p["box"] = (0.1, 0.1, 0.5, 0.5)

    day_ai.find_animals(pics, measure_one, load, NoCache())
    assert measured == ["P0.ORF", "P2.ORF"]
    assert pics[1]["box"] is None
