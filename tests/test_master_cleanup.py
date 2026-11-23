"""engine.master_cleanup: the all-species runs on a fake master (no ExifTool,
no real files written — set_label / delete_picture are stand-ins)."""
from __future__ import annotations

import pytest

from engine import master_cleanup as mc
from engine import purple_marks


class FakeDB:
    def __init__(self, rows):
        self.rows = {r["UniquePictureName"]: r for r in rows}

    def pictures_in_daterange(self, lo, hi):
        out = {}
        for r in self.rows.values():
            out.setdefault(r["DateTimeOriginal"][:10], []).append(r)
        return out

    def picture(self, upn):
        return self.rows.get(upn)


def _row(tmp_path, upn, sci="Larus ridibundus", sec=0, label="", rating=0, jpg=False,
         day="2020:05:01"):
    folder = tmp_path / sci
    folder.mkdir(exist_ok=True)
    files = [f"{upn}.ORF"] + ([f"{upn}.jpg"] if jpg else [])
    for f in files:
        (folder / f).write_bytes(b"x" * 1000)
    return {"UniquePictureName": upn, "WetSoortNaam": sci, "TargetPath": sci,
            "Files": files, "Label": label, "Rating": str(rating),
            "BestJPG": f"{upn}.jpg" if jpg else "",
            "DateTimeOriginal": f"{day} 10:{sec // 60:02d}:{sec % 60:02d}"}


@pytest.fixture
def fake_scores(monkeypatch):
    """Every frame of one burst looks alike; frame '...-00' is the sharpest."""
    import engine.picture_score as ps

    def score(pics, cache, **kw):
        for p in pics:
            p.update({"sharp": 100.0 - int(p["upn"][-2:]), "dhash": "0" * 16,
                      "tiles": None, "fine": None})
        return 0
    monkeypatch.setattr(ps, "score_pictures", score)


def _labeller(db, log=None):
    def set_label(_db, _root, upn, label):
        db.rows[upn]["Label"] = label
        if log is not None:
            log.append((upn, label))
    return set_label


def test_mark_only_candidates_and_record_first(tmp_path, fake_scores):
    rows = [_row(tmp_path, f"gull-{i:02d}", sec=i) for i in range(20)]
    rows[3]["Label"] = "Yellow"                    # a keeper
    rows[4]["Rating"] = "3"                        # a keeper (above the day's usual)
    rows[5] = _row(tmp_path, "gull-05", sec=5, jpg=True)   # has a JPG: a keeper
    db = FakeDB(rows)
    by_sci, floors = mc.master_species(db)
    lines, written = [], []
    tot = mc.mark_all(db, tmp_path, by_sci, floors, report=lines.append,
                      cache=object(), set_label=_labeller(db, written))
    marked = {u for u, c in written}
    assert tot["done"] == len(marked) > 0
    assert not marked & {"gull-03", "gull-04", "gull-05"}
    assert all(c == "Purple" for _u, c in written)
    run, batches = purple_marks.last_run(str(tmp_path))
    assert run and set(batches[0]["before"]) == marked
    assert lines[0]["species"] == "Larus ridibundus" and lines[0]["done"] == len(marked)


def test_delete_skips_jpg_and_other_colours(tmp_path):
    rows = [_row(tmp_path, "a-01", label="Purple"),
            _row(tmp_path, "a-02", label="Purple", jpg=True),
            _row(tmp_path, "a-03", label="Green")]
    db = FakeDB(rows)
    by_sci, _ = mc.master_species(db)
    gone, lines = [], []
    tot = mc.delete_all_purple(db, tmp_path, by_sci, report=lines.append,
                               delete_picture=lambda _db, _r, upn: gone.append(upn))
    assert gone == ["a-01"]
    assert tot["with JPG left"] == 1 and tot["done"] == 1
    assert mc.purple_count(by_sci) == (1, 1)


def test_undo_only_where_still_purple(tmp_path, fake_scores):
    rows = [_row(tmp_path, f"gull-{i:02d}", sec=i, label="Green") for i in range(20)]
    db = FakeDB(rows)
    by_sci, floors = mc.master_species(db)
    mc.mark_all(db, tmp_path, by_sci, floors, report=lambda d: None,
                cache=object(), set_label=_labeller(db))
    purple = [u for u, r in db.rows.items() if r["Label"] == "Purple"]
    assert len(purple) >= 2
    db.rows[purple[0]]["Label"] = "Red"            # you changed one since
    tot = mc.undo_last_run(db, tmp_path, report=lambda d: None,
                           set_label=_labeller(db))
    assert db.rows[purple[0]]["Label"] == "Red"
    assert all(db.rows[u]["Label"] == "Green" for u in purple[1:])
    assert tot["changed since"] == 1
    assert purple_marks.last_run(str(tmp_path)) == ("", [])


def test_stop_keeps_what_is_done(tmp_path, fake_scores):
    rows = [_row(tmp_path, f"gull-{i:02d}", sec=i) for i in range(20)]
    db = FakeDB(rows)
    by_sci, floors = mc.master_species(db)
    written = []
    stop = lambda: len(written) >= 2                  # noqa: E731
    mc.mark_all(db, tmp_path, by_sci, floors, report=lambda d: None, cache=object(),
                cancelled=stop, set_label=_labeller(db, written))
    _run, batches = purple_marks.last_run(str(tmp_path))
    assert len(written) == 2 and set(batches[0]["before"]) == {u for u, _ in written}


def test_window_opens_read_only(monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])   # noqa: F841
    from gui import master_cleanup as w
    monkeypatch.setattr(w, "master_writable", lambda: False)
    dlg = w.MasterCleanupWindow(None, str(tmp_path))
    assert not any(b.isEnabled() for b in dlg._buttons.values())
    assert "may not be changed" in dlg._status.text()
    dlg.reject()
