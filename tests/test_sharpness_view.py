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
    assert "could go" in v._info.text() and "on the spot" in v._info.text()
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
