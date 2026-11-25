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


def test_pick_animal_takes_the_sharpest_animal():
    import numpy as np
    fine = np.zeros((36, 48), np.float32)
    fine[30:34, 40:46] = 50.0                     # sharp at the bottom right
    boxes = [{"cat": "animal", "conf": .9, "box": [.0, .0, .3, .3]},    # blurry, sure
             {"cat": "animal", "conf": .4, "box": [.8, .8, 1., 1.]},    # sharp, less sure
             {"cat": "animal", "conf": .1, "box": [.8, .8, 1., 1.]},    # not believed
             {"cat": "person", "conf": .9, "box": [.8, .8, 1., 1.]}]
    assert am.pick_animal(boxes, fine) == (.8, .8, 1., 1.)
    assert am.pick_animal(boxes, None) == (.0, .0, .3, .3)   # no grid: most confident
    assert am.pick_animal(boxes[2:], fine) is None


def test_legend_says_the_sharpest_part_is_elsewhere():
    from gui.sharpness_view import SharpnessView
    v = SharpnessView()
    v._ai_res = {"box": (.4, .4, .6, .6), "px": 500, "sharp_ratio": 0.1}
    assert "elsewhere" in v._legend_text({"sharp": 10}, {}, None, 100, 100)
    v._ai_res["sharp_ratio"] = 0.8
    assert "elsewhere" not in v._legend_text({"sharp": 10}, {}, None, 100, 100)


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
                       "among": "eBird's list for NL (588 bird species)",
                       "names": [{"sci": "Circus macrourus", "name": "steppekiekendief",
                                  "prob": 0.97, "how": "exact"},
                                 {"sci": "Circus cyaneus", "name": "Hen Harrier",
                                  "prob": 0.02, "how": ""}]})
    assert panel._bars["sharp"].value == 74.0
    assert panel._bars["species"].value == 97.0
    text = panel._names.text()
    assert "steppekiekendief" in text and "not in your list" in text and "NL" in text
    assert "Not sure" not in text
    panel.show_result({"box": (.1, .1, .3, .3), "px": 900, "size_pct": 68.0,
                       "sharp_pct": 10.0, "names": [{"sci": "Anser caerulescens",
                       "name": "Snow Goose", "prob": 0.25, "how": "exact"}]})
    assert "Not sure" in panel._names.text()
    panel.show_result({"box": None})
    assert panel._vals["sharp"].text() == "no animal"
    panel.show_result({"missing": True})
    assert "not installed" in panel._names.text()


def test_names_match_your_list(tmp_path, monkeypatch):
    from engine import ai_names
    monkeypatch.setattr(ai_names, "_lists", {})
    import db.master_mutate as mm
    rows = {
        "tauraco violaceus": {"Level": "Level.SPECIES", "ScientificName": "tauraco violaceus",
                              "DutchName": "violette toerako", "EnglishName": "Violet Turaco",
                              "Up": "tauraco"},
        "tauraco": {"Level": "Level.GENUS", "Up": "musophagidae"},
        "pica europaea": {"Level": "Level.SPECIES", "ScientificName": "pica europaea",
                          "Up": "pica"},
        "pica": {"Level": "Level.GENUS", "Up": "corvidae"},
    }
    monkeypatch.setattr(mm, "_read_collection", lambda root: rows)
    how, row = ai_names.match("Musophaga violacea", tmp_path, family="musophagidae")
    assert how == "renamed" and row["DutchName"] == "violette toerako"   # -a / -us
    assert ai_names.match("Musophaga rossae", tmp_path, family="musophagidae") == ("", None)
    # the same epithet in ANOTHER family is no rename
    assert ai_names.match("Sitta europaea", tmp_path, family="sittidae") == ("", None)
    d = ai_names.display("Tauraco violaceus", "Violet Turaco", tmp_path, profile="nl_en")
    assert d == {"name": "violette toerako", "sci": "Tauraco violaceus", "how": "exact"}


def test_ebird_names_in_newer_genera_find_bioclips_older_ones():
    clip = ai_models.BioClip.__new__(ai_models.BioClip)
    clip.sci = ["Bubulcus ibis", "Accipiter gentilis", "Charadrius alexandrinus",
                "Ardea alba", "Sylvia communis", "Phylloscopus communis"]
    clip.common = ["Cattle Egret", "Northern Goshawk", "Kentish Plover", "Great Egret",
                   "Common Whitethroat", ""]
    clip.group = {"Bubulcus ibis": ("aves", "ardeidae"), "Accipiter gentilis": ("aves", "accipitridae"),
                  "Charadrius alexandrinus": ("aves", "charadriidae"),
                  "Ardea alba": ("aves", "ardeidae"), "Sylvia communis": ("aves", "sylviidae"),
                  "Phylloscopus communis": ("aves", "phylloscopidae")}
    clip._by_common = None
    taxonomy = {"ardea ibis": ("Western Cattle Egret", "Ardeidae"),
                "astur gentilis": ("Eurasian Goshawk", "Accipitridae"),
                "anarhynchus alexandrinus": ("Kentish Plover", "Charadriidae"),
                "curruca communis": ("Greater Whitethroat", "Sylviidae"),
                "anthus japonicus": ("Siberian Pipit", "Motacillidae")}
    got = clip.match_list({"ardea alba", "ardea ibis", "astur gentilis",
                           "anarhynchus alexandrinus", "curruca communis", "anthus japonicus"},
                          taxonomy)
    assert got == {"ardea alba", "bubulcus ibis", "accipiter gentilis",
                   "charadrius alexandrinus", "sylvia communis"}     # family decides, not epithet


def test_vectors_for_batches_and_skips_cached(tmp_path, monkeypatch):
    from PIL import Image
    runs = []

    class Clip:
        def picture_vectors(self, ims, batch=8):
            runs.append(len(ims))
            return np.ones((len(ims), 4), np.float32)
    monkeypatch.setattr(ai_models, "bioclip", lambda: Clip())
    cache = am.AiCache(tmp_path / "c.sqlite")
    srcs = []
    for i in range(11):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"x" * (i + 1))
        srcs.append(p)
    box = (0.1, 0.1, 0.5, 0.5)
    cache.put_vector(srcs[0], box, np.zeros(4, np.float32))     # seen before
    decoded = []

    def load(p):
        decoded.append(p)
        return Image.new("RGB", (100, 80))
    made = am.vectors_for([(p, lambda p=p: load(p), box) for p in srcs], cache, batch=4)
    assert made == 10 and runs == [4, 4, 2]
    assert srcs[0] not in decoded                                # not decoded again
    assert all(cache.vector(p, box) is not None for p in srcs)
    cache.close()
