"""engine.ai_measure / ai_yardstick / ai_models (no model files needed) and the
AI panel of the Editor's Sharpness tab."""
from __future__ import annotations

import numpy as np

from engine import ai_measure as am
from engine import ai_models
from engine.ai_yardstick import Yardstick, norm_camera


def test_size_scale():
    assert am.size_pct(80) == 0
    assert am.size_pct(2500) == 100
    assert am.size_pct(9000) == 100
    assert 45 < am.size_pct(500) < 55            # log scale: 500 px is halfway


def test_long_side_uses_the_sensor_size():
    # a 3200-px preview of a 5220-px picture; the box is a quarter of the width
    assert am.long_side_px((0.0, 0.0, 0.25, 0.1), (3200, 2400), 5220) == 1305
    assert am.long_side_px((0.0, 0.0, 0.25, 0.1), (3200, 2400), 0) == 800


def test_animal_box_ignores_weak_and_other_kinds():
    boxes = [{"cat": "person", "conf": 0.9, "box": [0, 0, 1, 1]},
             {"cat": "animal", "conf": 0.15, "box": [0, 0, .5, .5]},
             {"cat": "animal", "conf": 0.6, "box": [.1, .1, .2, .2]}]
    assert am.animal_box(boxes) == (.1, .1, .2, .2)
    assert am.animal_box(boxes[:2]) is None


def test_yardstick_percentile_per_camera():
    y = Yardstick({"om-1markii": list(range(1000)), "": list(range(1000)),
                   "few": [1, 2, 3]})
    assert y.percentile("OM-1 Mark II", 250) == 25.0
    assert y.percentile("OM-1MarkII", 2000) == 100.0
    assert y.percentile("few", 2) is None          # too few master pictures
    assert y.percentile("", 5) is None             # no camera name
    assert norm_camera("OM-1 Mark II") == "om-1markii"


def test_bioclip_top_within_a_list():
    b = object.__new__(ai_models.BioClip)
    b.scale = 10.0
    b.vectors = np.eye(3, dtype=np.float32)          # 3 names, 3 dimensions
    b.sci = ["Circus cyaneus", "Circus macrourus", "Buteo buteo"]
    b.common = ["hen harrier", "pallid harrier", "common buzzard"]
    b._subsets = {}
    import threading
    b._sub_lock = threading.Lock()
    vec = np.array([0.2, 1.0, 0.0], np.float32)
    top = b.top(vec, k=2)
    assert top[0][0] == "Circus macrourus" and top[1][0] == "Circus cyaneus"
    only = b.top(vec, k=3, allowed={"circus cyaneus", "buteo buteo"})
    assert [t[0] for t in only] == ["Circus cyaneus", "Buteo buteo"]
    assert abs(sum(t[2] for t in only) - 1) < 1e-6


def test_missing_models_are_reported(tmp_path):
    assert set(ai_models.missing_files(tmp_path)) == {ai_models.DETECTOR_FILE,
                                                       *ai_models.BIOCLIP_FILES}


def test_ai_panel_shows_bars_and_names():
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])   # noqa: F841
    from gui.sharpness_view import _AiPanel
    panel = _AiPanel()
    panel.show_result({"box": (.1, .1, .3, .3), "px": 900, "size_pct": 68.0,
                       "sharp_pct": 74.0,
                       "names": [("Circus macrourus", "Pallid Harrier", 0.97),
                                 ("Circus cyaneus", "Hen Harrier", 0.02)]})
    assert panel._bars["sharp"].value == 74.0
    assert panel._bars["species"].value == 97.0
    assert "Pallid Harrier" in panel._names.text()
    panel.show_result({"box": None})
    assert panel._vals["sharp"].text() == "no animal"
    panel.show_result({"missing": True})
    assert "not installed" in panel._names.text()
