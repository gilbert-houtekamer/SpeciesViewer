"""gui.sharpness_view: measures in the background, draws the spot, holds it."""
import time

import cv2
import numpy as np


def _wait(app, v, secs=30):
    t0 = time.time()
    while v._result is None and time.time() - t0 < secs:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def test_view_measures_and_holds_the_spot(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.sharpness_view import SharpnessView
    rng = np.random.default_rng(5)
    paths = []
    for i, (y, x) in enumerate([(300, 1200), (900, 300)]):      # the "bird" moves
        img = np.full((1600, 2400), 120, np.uint8)
        img[y:y + 300, x:x + 300] = rng.integers(0, 255, (300, 300), dtype=np.uint8)
        p = tmp_path / f"N{i}.jpg"
        cv2.imwrite(str(p), img)
        paths.append(p)
    v = SharpnessView()
    v.resize(1200, 600)
    v.set_picture(paths[0], "purple")
    _wait(app, v)
    spot = v._frame.spot
    assert spot is not None and spot[0] < 1350 / 2400 < spot[2]   # on the subject
    assert "could go" in v._info.text() and "sharpness there" in v._legend.text()
    assert not v._crop.pixmap().isNull()
    v._keep.setChecked(True)                                       # hold it …
    v.set_picture(paths[1])
    _wait(app, v)
    assert v._frame.spot == spot                                   # … on the next frame
    v._keep.setChecked(False)                                      # follow the subject again
    assert v._frame.spot != spot
    v.set_picture(None)
    assert "No picture" in v._info.text()
    v.stop()


def test_verdict_frame_follows_your_choice(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.sharpness_view import SharpnessView
    img = np.full((600, 900), 120, np.uint8)
    img[200:400, 300:500] = np.random.default_rng(1).integers(0, 255, (200, 200), dtype=np.uint8)
    p = tmp_path / "N.jpg"
    cv2.imwrite(str(p), img)
    v = SharpnessView()
    v.set_picture(p, "keep")
    _wait(app, v)
    assert v._frame.verdict == "keep" and not v._frame.yours
    v.set_verdict("purple", True)                  # b in the Editor
    assert (v._frame.verdict, v._frame.yours) == ("purple", True)
    assert "could go (your choice)" in v._info.text()
    v.set_verdict("", False)                       # the AI… dialog closed
    assert "AI…" not in v._info.text()
    v.stop()


def test_click_puts_the_spot_on_the_bird(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui.sharpness_view import SharpnessView
    img = np.full((1600, 2400), 120, np.uint8)
    rng = np.random.default_rng(4)
    img[200:500, 200:500] = rng.integers(0, 255, (300, 300), dtype=np.uint8)   # twigs
    p = tmp_path / "N.jpg"
    cv2.imwrite(str(p), img)
    v = SharpnessView()
    v.resize(1200, 600)
    v.show()
    v.set_picture(p)
    _wait(app, v)
    auto = v._frame.spot
    v._on_frame_clicked(0.75, 0.75)            # the bird, where the score did not look
    s = v._frame.spot
    assert s != auto and s[0] < 0.75 < s[2] and s[1] < 0.75 < s[3]
    assert "where you clicked" in v._legend.text()
    v.set_picture(p)                           # next picture: follows the subject again
    _wait(app, v)
    assert v._frame.spot == auto
    v.stop()


def test_strip_cell_shows_the_frame():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from gui.large_image_view import _StripItem

    class _Item:
        key, display_name, color, is_video = "k1", "k1", "", False

        def request_thumbnail(self, w, cb):
            pass

    cell = _StripItem(_Item())
    cell.set_suggestion("purple", True)
    assert cell._thumb_lbl._veil.isVisibleTo(cell._thumb_lbl)
    cell.set_suggestion("", False)
    assert not cell._thumb_lbl._veil.isVisibleTo(cell._thumb_lbl)
