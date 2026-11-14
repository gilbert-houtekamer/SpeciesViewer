"""Set date/time: a dateless picture gets the chosen date, a dated one is never
touched, and no two pictures share a second. Runs the real exiftool on temp
JPGs; the write tests are skipped when it is absent."""

import shutil
from datetime import datetime

import pytest

from engine.capture_date_write import spread_times


def test_spread_times_one_second_apart_skipping_taken():
    start = datetime(2026, 11, 1, 10, 15, 0)
    taken = [datetime(2026, 11, 1, 10, 15, 1), datetime(2026, 11, 1, 10, 15, 3)]
    got = spread_times(start, 3, taken)
    assert got == [datetime(2026, 11, 1, 10, 15, 0), datetime(2026, 11, 1, 10, 15, 2),
                   datetime(2026, 11, 1, 10, 15, 4)]
    assert len(set(got)) == 3


def test_spread_times_start_taken():
    start = datetime(2026, 11, 1, 10, 15, 0)
    assert spread_times(start, 1, [start]) == [datetime(2026, 11, 1, 10, 15, 1)]


def _have_exiftool():
    try:
        import os
        from engine.exif_writer import exiftool_exe
        exe = exiftool_exe()
        return bool(exe) and bool(shutil.which(exe) or os.path.exists(exe))
    except Exception:
        return False


needs_exiftool = pytest.mark.skipif(not _have_exiftool(), reason="exiftool not installed")


def _jpg(path):
    from PyQt6.QtGui import QImage, QColor
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(QColor("green"))
    assert img.save(str(path), "JPG")
    return path


@needs_exiftool
def test_writes_dateless_and_never_touches_a_dated_file(tmp_path):
    from engine.capture_date_write import read_capture_dates, write_capture_dates, _norm
    from engine.exif_writer import run_exiftool_files
    a = _jpg(tmp_path / "whatsapp1.jpg")
    b = _jpg(tmp_path / "camera.jpg")
    run_exiftool_files(["-EXIF:DateTimeOriginal=2020:01:02 03:04:05",
                        "-overwrite_original"], [b])
    t1 = datetime(2026, 11, 1, 10, 15, 0)
    t2 = datetime(2026, 11, 1, 10, 15, 1)

    written, had_date, failed = write_capture_dates({t1: [a], t2: [b]})

    assert written == [str(a)] and had_date == [str(b)] and failed == []
    dates = read_capture_dates([a, b])
    assert dates[_norm(a)] == t1
    assert dates[_norm(b)] == datetime(2020, 1, 2, 3, 4, 5)     # untouched


@needs_exiftool
def test_a_hand_set_date_is_marked_and_may_be_replaced(tmp_path):
    from engine.capture_date_write import read_capture_info, write_capture_dates, _norm
    a = _jpg(tmp_path / "goose.jpg")
    first = datetime(2026, 11, 1, 10, 15, 0)
    better = datetime(2026, 10, 30, 16, 2, 0)

    assert write_capture_dates({first: [a]})[0] == [str(a)]
    assert read_capture_info([a])[_norm(a)] == (first, True)        # marked MANUAL

    written, had_date, failed = write_capture_dates({better: [a]})   # a better estimate
    assert written == [str(a)] and had_date == [] and failed == []
    assert read_capture_info([a])[_norm(a)] == (better, True)


@needs_exiftool
def test_a_camera_date_is_not_marked(tmp_path):
    from engine.capture_date_write import read_capture_info, _norm
    from engine.exif_writer import run_exiftool_files
    b = _jpg(tmp_path / "camera.jpg")
    run_exiftool_files(["-EXIF:DateTimeOriginal=2020:01:02 03:04:05",
                        "-overwrite_original"], [b])
    assert read_capture_info([b])[_norm(b)] == (datetime(2020, 1, 2, 3, 4, 5), False)
