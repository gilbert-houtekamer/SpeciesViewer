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

    def handed(day, paths, last):
        # every picture of the day is on disk when the day is handed over
        assert all(os.path.exists(p) for p in paths)
        assert last                     # short days: never in parts
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

    def broken(day, paths, last):
        raise RuntimeError("AI fell over")

    s = copy_card(tmp_path / "card", dest, bucket_by_date=True, write_sidecars=False,
                  use_card_cache=False, on_day_copied=broken)
    assert s["copied"] == 1 and not s["failed"]


def test_a_long_day_comes_in_parts_each_picture_once(tmp_path, monkeypatch):
    """A long day goes to the AI in parts while the rest copies; a RAW and its
    camera JPG stay in one part; together the parts are the whole day."""
    import batch.sv0ReadSDCard as sv0
    monkeypatch.setattr(sv0, "DAY_PART", 3)
    card, dest = tmp_path / "card" / "DCIM", tmp_path / "dest"
    card.mkdir(parents=True)
    dest.mkdir()
    for i in range(10):
        _shot(card, f"A{i}.JPG", "2023-08-03", bytes([i]))
        _shot(card, f"A{i}.ORF", "2023-08-03", bytes([100 + i]))
    _shot(card, "B0.JPG", "2023-08-04", b"b")
    calls = []

    def handed(day, paths, last):
        assert all(os.path.exists(p) for p in paths)
        calls.append((day, sorted(os.path.basename(p) for p in paths), last))

    s = copy_card(tmp_path / "card", dest, on_day_copied=handed, bucket_by_date=True,
                  write_sidecars=False, use_card_cache=False)
    assert s["copied"] == 21
    day = [c for c in calls if c[0] == "2023-08-03"]
    assert len(day) >= 2 and [c[2] for c in day] == [False] * (len(day) - 1) + [True]
    names = [n for c in day for n in c[1]]
    assert sorted(names) == sorted(f"A{i}.{x}" for i in range(10) for x in ("JPG", "ORF"))
    for _d, part, _last in day:          # both files of a picture in one part
        stems = [n.split(".")[0] for n in part]
        assert all(stems.count(st) == 2 for st in stems)
    assert [c for c in calls if c[0] == "2023-08-04"] == [("2023-08-04", ["B0.JPG"], True)]


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


def test_the_ai_counts_a_day_in_parts_once(monkeypatch):
    """CardAiWorker: parts of one day add up; the day counts once, at its end."""
    import engine.day_measure as dm
    from gui.card_ai import CardAiWorker

    monkeypatch.setattr(dm, "card_batch", lambda paths: [{"src": p} for p in paths])

    def measure_day(pics, *a, **k):
        for p in pics:
            p["box"] = (0, 0, 1, 1) if p["src"].startswith("a") else None
    monkeypatch.setattr(dm, "measure_day", measure_day)
    w = CardAiWorker("")
    states, done = [], []
    w.state.connect(lambda d, t: states.append((d, t)))
    w.all_done.connect(done.append)
    w.add("2023-08-03", ["a1", "a2", "x3"], last=False)
    w.add("2023-08-03", ["a4", "x5"], last=True)
    w.add("2023-08-04", ["a6"])
    w.no_more()
    w.run()                                   # on this thread: no event loop needed
    assert done[0]["days"] == 2 and done[0]["pictures"] == 6 and done[0]["animals"] == 4
    assert ("2023-08-03", "✓ 3 pictures so far — copying the rest") in states
    assert ("2023-08-03", "✓ 3 of 5 with an animal") in states
    assert sum(1 for d, t in states if t == "waiting") == 2      # once per day
