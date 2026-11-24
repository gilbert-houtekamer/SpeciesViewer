"""engine.day_ai: the AI's suggestion for one day (made-up measurements)."""
from __future__ import annotations

from datetime import datetime, timedelta

from engine import day_ai
from engine.day_ai import DaySettings

T0 = datetime(2026, 11, 23, 13, 0, 0)


def pic(i, sec, *, sharp=80.0, px=600, box=True, name=("Buteo buteo", 0.98),
        stars=0, label=""):
    return {"key": f"k{i:02d}", "upn": f"k{i:02d}", "time": T0 + timedelta(seconds=sec),
            "dhash": "0" * 16, "stars": stars, "label": label, "jpg": False,
            "bytes": 20_000_000, "box": (0.4, 0.4, 0.5, 0.5) if box else None,
            "sharp_pct": sharp if box else 0.0, "px": px if box else 0,
            "names": [{"sci": name[0], "name": name[0], "prob": name[1], "how": "exact"}]
            if box else []}


def test_a_burst_keeps_the_sharpest_two():
    pics = [pic(i, i, sharp=s) for i, s in enumerate([50, 90, 70, 85, 60])]
    groups = day_ai.decide(pics, DaySettings(per_pose=2))
    assert len(groups) == 1
    keep = {p["key"] for p in pics if p["verdict"] == "keep"}
    assert keep == {"k01", "k03"}
    assert all(p["why"] == day_ai.TOO_MANY for p in pics if p["verdict"] == "purple")
    assert all(p["name"]["sci"] == "Buteo buteo" for p in pics)


def test_out_of_focus_and_no_animal_go_first():
    pics = [pic(0, 0, sharp=5), pic(1, 1, box=False), pic(2, 2, sharp=60)]
    day_ai.decide(pics, DaySettings(per_pose=3, sharp_cut=15))
    by = {p["key"]: (p["verdict"], p["why"]) for p in pics}
    assert by["k00"] == ("purple", day_ai.OUT_OF_FOCUS)
    assert by["k01"] == ("purple", day_ai.NO_ANIMAL)
    assert by["k02"] == ("keep", "")


def test_your_keepers_count_and_are_never_marked():
    pics = [pic(0, 0, sharp=40, stars=3), pic(1, 1, sharp=90), pic(2, 2, sharp=80),
            pic(3, 3, sharp=70, label="Yellow")]
    day_ai.decide(pics, DaySettings(per_pose=2))
    by = {p["key"]: p["verdict"] for p in pics}
    assert by["k00"] is None and by["k03"] is None        # yours
    assert by["k01"] == "purple" and by["k02"] == "purple"   # two kept already


def test_a_weak_name_is_not_sure_and_poses_split_on_time():
    pics = [pic(0, 0, name=("Anser caerulescens", 0.22)), pic(1, 200)]
    groups = day_ai.decide(pics, DaySettings(threshold=45))
    assert len(groups) == 2
    assert pics[0]["name"] is None and pics[0]["sure"] is False
    assert pics[1]["name"]["sci"] == "Buteo buteo"
    st = day_ai.summary(pics, groups)
    assert st["not_sure_poses"] == 1 and st["species"]["Buteo buteo"]["poses"] == 1


def test_small_only_when_the_pose_has_a_bigger_one():
    pics = [pic(0, 0, px=300, sharp=95), pic(1, 1, px=900, sharp=60)]
    day_ai.decide(pics, DaySettings(per_pose=2, small_px=500))
    by = {p["key"]: (p["verdict"], p["why"]) for p in pics}
    assert by["k00"] == ("purple", day_ai.SMALL) and by["k01"] == ("keep", "")
    alone = [pic(0, 0, px=300)]
    day_ai.decide(alone, DaySettings(small_px=500))
    assert alone[0]["verdict"] == "keep"


def test_card_name_tag():
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])   # noqa: F841
    from gui.rating_editor import _FramedPhoto
    ph = _FramedPhoto()
    ph.resize(300, 200)
    ph.set_name_tag("common buzzard 98%", True)
    assert ph._name_tag.text() == "common buzzard 98%" and not ph._name_tag.isHidden()
    ph.set_name_tag("")
    assert ph._name_tag.isHidden()


def test_common_species_thinned_by_the_master():
    # 10 poses of a buzzard, each 2 frames kept by the pose rule
    pics = [pic(i, i * 100 + (i % 2), sharp=50 + i) for i in range(20)]
    day_ai.decide(pics, DaySettings(per_pose=2), master_days={"buteo buteo": 200})
    kept = [p for p in pics if p["verdict"] == "keep"]
    assert len(kept) == day_ai.day_limit(200) == 4
    assert all(p["why"] in (day_ai.PLENTY, day_ai.TOO_MANY)
               for p in pics if p["verdict"] == "purple")
    assert min(p["sharp_pct"] for p in kept) > max(
        p["sharp_pct"] for p in pics if p["why"] == day_ai.PLENTY)   # the sharpest stay
    # a species the master never had keeps up to 12
    day_ai.decide(pics, DaySettings(per_pose=2), master_days={})
    assert sum(p["verdict"] == "keep" for p in pics) == 12
    # switched off: only the pose rule
    day_ai.decide(pics, DaySettings(per_pose=2, use_master=False),
                  master_days={"buteo buteo": 200})
    assert sum(p["verdict"] == "keep" for p in pics) == 20


def test_cut_off_by_the_edge_goes_when_the_pose_has_a_whole_one():
    a, b = pic(0, 0, sharp=95), pic(1, 1, sharp=60)
    a["box"] = (0.7, 0.3, 1.0, 0.6)                      # half outside on the right
    assert day_ai.cut_off(a["box"]) and not day_ai.cut_off(b["box"])
    day_ai.decide([a, b], DaySettings(per_pose=2))
    assert (a["verdict"], a["why"]) == ("purple", day_ai.CUT_OFF)
    assert b["verdict"] == "keep"
    # only cut-off frames: they are all there is, so the sharpest stay
    c = pic(0, 0, sharp=95)
    c["box"] = (0.0, 0.3, 0.3, 0.6)
    day_ai.decide([c], DaySettings())
    assert c["verdict"] == "keep"


def test_kept_leaves_out_what_is_marked_to_go():
    pics = [pic(0, 0, label="Purple"), pic(1, 1, label="Green"), pic(2, 2, label="Blue")]
    groups = day_ai.decide(pics, DaySettings(per_pose=3))
    st = day_ai.summary(pics, groups)
    assert st["species"]["Buteo buteo"]["kept"] == 1
    assert st["marked"] == 2
