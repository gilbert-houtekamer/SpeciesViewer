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

    def pictures_in_daterange(self, a, b):
        return {a: [r for r in self.rows if r["DateTimeOriginal"][:10].replace(":", "-") == a]}


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
    assert dlg._per_day.value() == 12                     # one day: the rarity default
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

    def show_suggestions(self, sci, by_upn, yours=()):
        self.shown, self.yours = dict(by_upn), set(yours)
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


def test_your_say_overrides_the_suggestion(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.suggest_dialog import SuggestDialog
    grid = _FakeGrid()
    dlg = SuggestDialog(None, _FakeDB(_master(tmp_path)), str(tmp_path), "passer simplex",
                        "Desert sparrow", grid=grid)
    _wait(app, dlg)
    dlg._per_day.setValue(1)
    dlg._per_pose.setValue(1)
    binned = sorted(u for u, v in grid.shown.items() if v == "purple")
    kept = [u for u, v in grid.shown.items() if v == "keep"][0]
    assert dlg.override([binned[0]], "keep") == 1          # k on a bin
    assert dlg.override([kept], "purple") == 1             # b on a kept one
    assert grid.shown[binned[0]] == "keep" and grid.shown[kept] == "purple"
    assert grid.yours == {binned[0], kept}
    assert "1 kept by you and 1 binned by you" in dlg._result.text()
    dlg._per_day.setValue(3)                               # a setting does not undo it
    assert grid.shown[binned[0]] == "keep" and grid.shown[kept] == "purple"
    assert dlg.override([binned[0]], "keep") == 1          # k again: back to the suggestion
    assert grid.yours == {kept}
    assert dlg.override(["na122807"], "purple") == 0       # your Red keeper: refused
    dlg.close()


class _WritingGrid(_FakeGrid):
    def __init__(self):
        super().__init__()
        self.written = {}

    def can_write_master(self):
        return True

    def apply_colors(self, by_upn, title=""):
        self.written.update(by_upn)
        return set(by_upn), set()


def test_mark_the_bins_purple_and_undo(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    from gui.suggest_dialog import SuggestDialog
    grid = _WritingGrid()
    dlg = SuggestDialog(None, _FakeDB(_master(tmp_path)), str(tmp_path), "passer simplex",
                        "Desert sparrow", grid=grid)
    _wait(app, dlg)
    dlg._per_day.setValue(1)
    dlg._per_pose.setValue(1)
    bins = {u for u, v in grid.shown.items() if v == "purple"}
    assert len(bins) == 6 and dlg._mark_btn.isEnabled()
    dlg._on_mark()
    assert grid.written == {u: "Purple" for u in bins}
    assert not any(v == "purple" for v in grid.shown.values())   # now really Purple
    assert dlg._undo_btn.isEnabled()
    grid.written.clear()
    dlg._on_undo()
    assert grid.written == {u: "Green" for u in bins}            # each one's own colour
    assert sorted(v for v in grid.shown.values()) == ["keep"] + ["purple"] * 6
    dlg.close()


def test_an_album_looks_only_at_its_own_pictures(tmp_path):
    """Album Species Viewer: `only` = the album's pictures; how often you saw
    the species (the kept-per-day default) still counts the whole master."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.suggest_dialog import SuggestDialog
    rows = _master(tmp_path)
    rows[7]["DateTimeOriginal"] = "2023:05:01 08:00:00"      # another day, outside the album
    only = {f"NA1228{i:02d}" for i in range(4)}              # case differs on purpose
    dlg = SuggestDialog(None, _FakeDB(rows), str(tmp_path), "passer simplex", "Desert sparrow",
                        only=only, only_label="2024-10-12 … 2024-10-12")
    _wait(app, dlg)
    assert sorted(p["upn"] for p in dlg._pics) == sorted(u.lower() for u in only)
    assert dlg._per_day.value() == 9                          # 2 days in the master
    assert "in your master" in dlg._per_day_note.text()
    assert "2024-10-12" in dlg.windowTitle()
    dlg.close()
