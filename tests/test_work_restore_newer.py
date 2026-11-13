"""Import Trip, metadata step: newer wins, deleted pictures stay deleted, and a
backup one folder too deep is still found. All in a temp folder."""
import os
import time

from engine import work_backup as wb
from engine import work_restore as wr
from batch.sv0ReadSDCard import target_name

P = "2026 madagascar - "


def _day(tmp_path):
    day = tmp_path / "pics" / "2026 Madagascar" / "2026-11-02"
    day.mkdir(parents=True)
    for stem in ("A001", "A002", "A003", "A004"):
        (day / f"{P}{stem}.ORF").write_bytes(b"raw")
        (day / f"{P}{stem}.xmp").write_text(f"card {stem}")
    return day


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_newer_wins_and_deleted_stays_deleted(tmp_path):
    day = _day(tmp_path)
    for f in day.iterdir():
        _age(f, 3600)                      # the backup's copies are an hour old
    card = tmp_path / "card"
    wb.backup_day(day, card)
    zip_path = next((card / "2026 Madagascar").glob("*.zip"))

    # Here: A001 edited later (newer here), A002 older than the card's copy,
    # A003 deleted (in the day's trash), A004 untouched.
    (day / f"{P}A001.xmp").write_text("edited here")
    (day / f"{P}A002.xmp").write_text("older here")
    _age(day / f"{P}A002.xmp", 7200)
    trash = day.parent / ".trash" / day.name / f"{P}a003".lower()
    trash.mkdir(parents=True)
    for ext in (".ORF", ".xmp"):
        (day / f"{P}A003{ext}").rename(trash / f"{P}A003{ext}")

    plan = wr.plan_day(zip_path, day)
    state = {r["name"]: r["state"] for r in plan["files"]}
    assert state[f"{P}A001.xmp"] == wr.HERE_NEWER
    assert state[f"{P}A002.xmp"] == wr.CARD_NEWER
    assert state[f"{P}A003.xmp"] == wr.DELETED_HERE
    assert state[f"{P}A004.xmp"] == wr.SAME

    res = wr.restore_day(zip_path, day)
    assert (day / f"{P}A001.xmp").read_text() == "edited here"      # never overwritten
    assert (day / f"{P}A002.xmp").read_text() == "card A002"         # card was newer
    assert not (day / f"{P}A003.xmp").exists()                       # no orphan
    assert res["replaced"] == 1 and res["deleted_here"] == 1
    kept = list((day.parent / ".trash" / "replaced").rglob("*.xmp"))
    assert [k.read_text() for k in kept] == ["older here"]           # older copy kept
    # The written file carries the card's time, so a second plan sees it as SAME.
    again = {r["name"]: r["state"] for r in wr.plan_day(zip_path, day)["files"]}
    assert again[f"{P}A002.xmp"] == wr.SAME


def test_nested_backup_is_found_and_card_root(tmp_path):
    day = _day(tmp_path)
    card = tmp_path / "card"
    trip_folder = card / "2026 Madagascar"
    wb.backup_day(day, trip_folder)        # destination set to the trip folder
    assert (trip_folder / "2026 Madagascar").is_dir()               # the nesting
    (trip_folder / "madagascardb.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    found = wr.find_backups(card)["trips"]
    mine = [t for t in found if t["folder"] == "2026 Madagascar"][0]
    assert [d["day"] for d in mine["days"]] == ["2026-11-02"]
    assert mine["identity"].name == "madagascardb.zip"
    assert wb.card_root(str(trip_folder / "2026 Madagascar")) == str(card).replace("\\", "/")
    assert wb.card_root(str(card)) == str(card)


def test_prefix_ignores_case():
    assert target_name("2026 madagascar - X.JPG", "2026 Madagascar") == "2026 madagascar - X.JPG"
    assert target_name("IMG_1.JPG", "2026 Madagascar") == "2026 Madagascar - IMG_1.JPG"


def test_card_read_takes_newer_sidecar_only_when_asked(tmp_path):
    from batch.sv0ReadSDCard import copy_card
    src, dest = tmp_path / "dump", tmp_path / "day"
    src.mkdir(); dest.mkdir()
    for d, text, age in ((dest, "older here", 7200), (src, "newer on dump", 0)):
        (d / f"{P}B001.ORF").write_bytes(b"same raw")
        (d / f"{P}B001.xmp").write_text(text)
        _age(d / f"{P}B001.xmp", age)
    kw = dict(prefix="2026 Madagascar", write_sidecars=False, use_card_cache=False)
    copy_card(src, dest, **kw)
    assert (dest / f"{P}B001.xmp").read_text() == "older here"       # default: never
    copy_card(src, dest, newer_wins=True, **kw)
    assert (dest / f"{P}B001.xmp").read_text() == "newer on dump"
    assert [p.read_text() for p in (tmp_path / ".trash" / "replaced").rglob("*.xmp")] \
        == ["older here"]


def test_newer_render_wins_camera_jpg_never(tmp_path):
    from batch.sv0ReadSDCard import copy_card
    src, dest = tmp_path / "dump", tmp_path / "day"
    src.mkdir(); dest.mkdir()
    for d, tag, age in ((dest, b"old", 7200), (src, b"new", 0)):
        for name in (f"{P}C001_DxO.jpg", f"{P}C002.JPG"):   # a render, a camera JPG
            (d / name).write_bytes(tag + b" " + name.encode())
            _age(d / name, age)
    # an OLDER render on the dump: left alone, no "_1" copy
    (src / f"{P}C003_DxO.jpg").write_bytes(b"older render")
    (dest / f"{P}C003_DxO.jpg").write_bytes(b"newer render here")
    _age(src / f"{P}C003_DxO.jpg", 9000)
    copy_card(src, dest, prefix="2026 madagascar", write_sidecars=False,
              use_card_cache=False, newer_wins=True)
    assert (dest / f"{P}C001_DxO.jpg").read_bytes().startswith(b"new")
    assert (dest / f"{P}C003_DxO.jpg").read_bytes() == b"newer render here"
    assert (dest / f"{P}C002.JPG").read_bytes().startswith(b"old")   # camera JPG kept…
    assert (dest / f"{P}C002 B.JPG").exists()                        # …the other is a new shot
    assert not (dest / f"{P}C003_DxO B.jpg").exists()
    kept = [p.name for p in (tmp_path / ".trash" / "replaced").rglob("*.jpg")]
    assert kept == [f"{P}C001_DxO.jpg"]


def test_trip_read_keeps_deleted_pictures_deleted_and_previews(tmp_path):
    from batch.sv0ReadSDCard import copy_card, preview_read
    src = tmp_path / "dump"
    day = tmp_path / "pics" / "2026-11-04"
    src.mkdir(); day.mkdir(parents=True)
    for stem in ("D001", "D002", "D003"):
        (src / f"{P}{stem}.ORF").write_bytes(stem.encode())
    (src / f"{P}D003.xmp").write_text("newer on dump")
    (day / f"{P}D003.ORF").write_bytes(b"D003")
    (day / f"{P}D003.xmp").write_text("older here")
    _age(day / f"{P}D003.xmp", 7200)
    trash = day.parent / ".trash" / day.name / f"{P}d002"
    trash.mkdir(parents=True)                       # D002 was deleted here
    t = time.mktime((2026, 11, 4, 12, 0, 0, 0, 0, -1))
    for f in src.glob("*.ORF"):
        os.utime(f, (t, t))

    pv = preview_read({"2026-11-04": sorted(src.glob("*.ORF"))}, tmp_path / "pics",
                      prefix="2026 madagascar")["2026-11-04"]
    assert (pv["to_copy"], pv["deleted_here"], pv["here"], pv["meta_newer"]) == (1, 1, 1, 1)

    copy_card(src, tmp_path / "pics", prefix="2026 madagascar", bucket_by_date=True,
              write_sidecars=False, use_card_cache=False, newer_wins=True)
    assert (day / f"{P}D001.ORF").exists()
    assert not (day / f"{P}D002.ORF").exists()      # stays deleted
    assert (day / f"{P}D003.xmp").read_text() == "newer on dump"
