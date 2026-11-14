"""Move to day: a picture's files (and their .dop) move together, nothing is
ever overwritten, and a failure halfway puts everything back."""

import shutil

from engine.day_move import move_picture


def _day(tmp_path, name):
    d = tmp_path / name
    d.mkdir()
    return d


def test_moves_files_and_their_recipes(tmp_path):
    a, b = _day(tmp_path, "2026-11-01"), _day(tmp_path, "2026-10-30")
    jpg = a / "LGHN7828.JPG"
    jpg.write_bytes(b"jpg")
    (a / "LGHN7828.JPG.dop").write_text("dop")
    moved, problem = move_picture([jpg], b)
    assert problem == ""
    assert sorted(p.name for p in moved) == ["LGHN7828.JPG", "LGHN7828.JPG.dop"]
    assert not any(a.iterdir())
    assert (b / "LGHN7828.JPG").read_bytes() == b"jpg"


def test_never_overwrites_and_moves_nothing_on_a_clash(tmp_path):
    a, b = _day(tmp_path, "2026-11-01"), _day(tmp_path, "2026-10-30")
    jpg = a / "X.JPG"
    jpg.write_bytes(b"new")
    xmp = a / "X.xmp"
    xmp.write_text("x")
    (b / "X.xmp").write_text("already there")
    moved, problem = move_picture([jpg, xmp], b)
    assert moved == [] and "already exists" in problem
    assert jpg.exists() and xmp.exists()                    # nothing moved
    assert (b / "X.xmp").read_text() == "already there"     # nothing overwritten


def test_missing_target_folder(tmp_path):
    a = _day(tmp_path, "2026-11-01")
    jpg = a / "X.JPG"
    jpg.write_bytes(b"x")
    moved, problem = move_picture([jpg], tmp_path / "2026-10-29")
    assert moved == [] and "does not exist" in problem and jpg.exists()


def test_failure_halfway_puts_back(tmp_path, monkeypatch):
    a, b = _day(tmp_path, "2026-11-01"), _day(tmp_path, "2026-10-30")
    f1, f2 = a / "Y.JPG", a / "Y.xmp"
    f1.write_bytes(b"1")
    f2.write_text("2")
    real_move = shutil.move
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_move(src, dst)

    monkeypatch.setattr("engine.day_move.shutil.move", flaky)
    moved, problem = move_picture([f1, f2], b)
    assert moved == [] and "nothing was moved" in problem
    assert f1.exists() and f2.exists() and not any(b.iterdir())
