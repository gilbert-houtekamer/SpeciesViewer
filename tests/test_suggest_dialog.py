"""gui.suggest_dialog: reads, measures in the background, suggests — and changes
nothing. A fake master in tmp; the score cache goes to the sandboxed app-data."""
import time

import cv2
import numpy as np
import pytest


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def pictures_for_species_and_subspecies(self, sci):
        return self.rows


def _master(tmp_path, n=8):
    folder = tmp_path / "biota" / "aves" / "passer simplex"
    folder.mkdir(parents=True)
    rng = np.random.default_rng(3)
    rows = []
    for i in range(n):
        img = np.full((300, 400), 120, np.uint8)
        img[100:200, 150:250] = rng.integers(0, 255, (100, 100), dtype=np.uint8)
        img = cv2.GaussianBlur(img, (0, 0), 0.5 + i)           # ever softer frames
        name = f"NA1228{i:02d}.jpg"
        cv2.imwrite(str(folder / name), img)
        rows.append({"UniquePictureName": f"na1228{i:02d}", "TargetPath": "biota/aves/passer simplex",
                     "Files": [name], "DateTimeOriginal": (f"2024:10:12 09:49:{i:02d}" if i < 7
                                             else "2024:10:12 09:55:00"),   # own pose
                     "Label": "Red" if i == 7 else "Green", "Rating": "0", "BestJPG": "",
                     "Location": "Hassilabied"})
    return rows


def _wait(app, dlg, secs=60):
    t0 = time.time()
    while (dlg._thread.isRunning() or not dlg._scored) and time.time() - t0 < secs:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def test_dialog_suggests_and_changes_nothing(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.suggest_dialog import SuggestDialog
    rows = _master(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    dlg = SuggestDialog(None, _FakeDB(rows), str(tmp_path), "passer simplex", "Desert sparrow")
    _wait(app, dlg)
    assert dlg._scored
    assert "8</b> pictures on <b>1</b> day" in dlg._stats.text()
    assert dlg._per_day.value() == 20                     # one day: the rarity default
    dlg._per_day.setValue(1)
    dlg._per_pose.setValue(1)
    verdicts = [p["verdict"] for p in dlg._pics]
    assert verdicts[7] == "keeper"                         # Red is never suggested
    assert verdicts[0] == "keep"                           # the sharpest frame stays
    assert verdicts.count("purple") == 6
    assert dlg._table.rowCount() == 1 and dlg._table.item(0, 5).text() == "6"
    dlg.close()
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before                                 # nothing in the master changed


@pytest.mark.parametrize("n", [0])
def test_dialog_with_no_pictures(tmp_path, n):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.suggest_dialog import SuggestDialog
    dlg = SuggestDialog(None, _FakeDB([]), str(tmp_path), "x", "Nothing")
    t0 = time.time()
    while dlg._thread.isRunning() and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert "No pictures" in dlg._stats.text()
    dlg.close()


class _FakeGrid:
    def __init__(self):
        self.shown, self.jumps, self.cleared = None, [], 0

    def show_suggestions(self, sci, by_upn):
        self.shown = dict(by_upn)
        return 0

    def jump_to_upns(self, upns):
        self.jumps.append(list(upns))
        return len(upns), 0

    def clear_suggestions(self):
        self.cleared += 1


def test_dialog_draws_on_the_grid_and_jumps_to_a_day(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.suggest_dialog import SuggestDialog
    grid = _FakeGrid()
    dlg = SuggestDialog(None, _FakeDB(_master(tmp_path)), str(tmp_path), "passer simplex",
                        "Desert sparrow", grid=grid)
    _wait(app, dlg)
    dlg._per_day.setValue(1)
    dlg._per_pose.setValue(1)
    assert sorted(grid.shown.values()) == ["keep"] + ["purple"] * 6
    assert "na122807" not in grid.shown                    # your keeper: no mark
    dlg._table.selectRow(0)
    app.processEvents()
    assert grid.jumps and set(grid.jumps[-1]) == {u for u, v in grid.shown.items()
                                                  if v == "purple"}
    dlg.close()
    assert grid.cleared == 1


def test_framed_photo_overlay():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from gui.rating_editor import _FramedPhoto
    ph = _FramedPhoto()
    ph.resize(200, 150)
    ph.set_suggestion("purple")
    assert ph._veil.isVisibleTo(ph) and ph._bin.isVisibleTo(ph)
    assert ph._veil.geometry() == ph.rect()
    ph.set_suggestion("keep")
    assert not ph._bin.isVisibleTo(ph)
    ph.set_suggestion(None)
    assert not ph._veil.isVisibleTo(ph)
