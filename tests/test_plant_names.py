"""Plants from the whole picture (AI… on a day): named only when BioCLIP's open
first name agrees; never offered as a name for an animal. No models needed."""
from datetime import datetime, timedelta

from engine import ai_measure, ai_models, day_ai, inat_lists


class _Clip:
    """top(): the plant list's answer, and the open answer."""
    group = {}

    def __init__(self, plant, wide):
        self.plant, self.wide = plant, wide

    def top(self, vec, k=3, allowed=None):
        return [self.plant] if allowed is not None else [self.wide]


def _setup(monkeypatch, plant, wide):
    monkeypatch.setattr(ai_models, "bioclip", lambda: _Clip(plant, wide))
    monkeypatch.setattr(ai_measure, "_as_bioclip_inat",
                        lambda r, g: frozenset({"adansonia rubrostipa"}) if g else frozenset())
    monkeypatch.setattr(ai_measure, "_shown",
                        lambda clip, top, root, outside=False: [{"sci": s, "prob": p} for s, _c, p in top])


def test_plant_named_when_open_name_agrees(monkeypatch):
    _setup(monkeypatch, ("Adansonia rubrostipa", "Fony baobab", 0.6),
           ("Adansonia rubrostipa", "Fony baobab", 0.59))
    names, among = ai_measure.plant_names(None, "MG", groups=["plants"])
    assert names[0]["sci"] == "Adansonia rubrostipa" and "whole picture" in among


def test_scenery_left_out(monkeypatch):
    # a beach: the plant list says Casuarina 82 %, BioCLIP itself says an antlion
    _setup(monkeypatch, ("Casuarina equisetifolia", "", 0.82), ("Synclisis baetica", "", 0.11))
    assert ai_measure.plant_names(None, "MG", groups=["plants"]) == ([], "")
    # the same plant, but BioCLIP hardly believes it (a street with a palm)
    _setup(monkeypatch, ("Phoenix dactylifera", "", 0.6), ("Phoenix dactylifera", "", 0.04))
    assert ai_measure.plant_names(None, "MG", groups=["plants"]) == ([], "")


def test_plants_never_name_an_animal(monkeypatch):
    from engine import ebird
    monkeypatch.setattr(ai_measure, "_matched", {})
    monkeypatch.setattr(ebird, "species", lambda r: frozenset({"lophotibis cristata"}))
    monkeypatch.setattr(ai_measure, "_as_bioclip", lambda r, l: frozenset(l))
    seen = []
    monkeypatch.setattr(ai_measure, "_as_bioclip_inat",
                        lambda r, g: (seen.append(tuple(g)), frozenset())[1])
    ai_measure._allowed("MG", ["mammals", "plants", "fungi"])
    assert seen == [("mammals",)]


def test_still_groups_only_when_chosen(monkeypatch):
    monkeypatch.setattr(inat_lists, "chosen_groups",
                        lambda r: ["mammals", "plants"] if r == "MG" else ["mammals"])
    assert ai_measure.still_groups("MG") == ["plants"]
    assert ai_measure.still_groups("NL") == []
    assert ai_measure.still_groups("") == []


def _pic(i, t, box=None, names=None, sharp=1.0):
    return {"key": f"k{i}", "src": f"p{i}", "time": t, "dhash": f"{i:016x}", "box": box,
            "names": names or [], "sharp": sharp, "stars": 0, "label": "", "jpg": False}


def test_day_names_a_plant_pose_and_counts_it():
    t0 = datetime(2026, 11, 7, 10)
    bird = [{"sci": "Lophotibis cristata", "name": "kuifibis", "prob": 0.9}]
    tree = [{"sci": "Adansonia rubrostipa", "name": "Fony baobab", "prob": 0.6}]
    pics = [_pic(1, t0, box=(0.3, 0.3, 0.6, 0.6), names=bird),
            _pic(2, t0 + timedelta(minutes=10), sharp=1.0),
            _pic(3, t0 + timedelta(minutes=10, seconds=1), sharp=5.0),
            _pic(4, t0 + timedelta(minutes=30))]
    lead = day_ai.still_leaders(pics)
    assert [p["key"] for p in lead if p["key"] in ("k2", "k3")] == ["k3"]   # the sharpest
    pics[2]["names"] = tree
    groups = day_ai.decide(pics, day_ai.DaySettings(), None)
    st = day_ai.summary(pics, groups)
    assert pics[1]["name"]["sci"] == "Adansonia rubrostipa" and pics[1]["plant_pose"]
    assert "Fony baobab" in st["species"] and "kuifibis" in st["species"]
    assert pics[3]["name"] is None and not pics[3]["plant_pose"]
    assert pics[3]["verdict"] in (None, "purple")
