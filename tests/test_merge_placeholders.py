"""A trip merge never brings back a "?" placeholder that no picture uses
(engine.speciesclassification.mergeSpeciesCollections, 2026-10-26: "? phacelia"
returned with every trip that had copied the master's list)."""

from engine.speciesclassification import SpeciesClassification


def _row(name, up, leaves=()):
    return {"ScientificName": name, "Up": up, "Leaves": list(leaves)}


def _master():
    sc = SpeciesClassification(None)
    sc.sc_dict = {"hemiptera": _row("hemiptera", "insecta", ["cicadidae"])}
    return sc


def _trip():
    return {
        "hemiptera": _row("hemiptera", "insecta", ["cicadidae", "aphididae"]),
        "aphididae": _row("aphididae", "hemiptera", ["? phacelia"]),
        "? phacelia": _row("? phacelia", "aphididae"),
        "cicadidae": _row("cicadidae", "hemiptera", ["lyristes plebejus", "? cicada"]),
        "lyristes plebejus": _row("lyristes plebejus", "cicadidae"),
        "? cicada": _row("? cicada", "cicadidae"),
    }


def test_unused_placeholder_and_its_empty_family_stay_out():
    sc = _master()
    skipped = sc.mergeSpeciesCollections(_trip(), used_names={"lyristes plebejus"})
    assert skipped == {"? phacelia", "aphididae", "? cicada"}
    assert "? phacelia" not in sc.sc_dict and "aphididae" not in sc.sc_dict
    assert "lyristes plebejus" in sc.sc_dict
    assert sc.sc_dict["cicadidae"]["Leaves"] == ["lyristes plebejus"]


def test_a_placeholder_a_picture_uses_still_travels():
    sc = _master()
    sc.mergeSpeciesCollections(_trip(), used_names={"? phacelia"})
    assert "? phacelia" in sc.sc_dict and "aphididae" in sc.sc_dict


def test_without_picture_names_the_merge_is_unchanged():
    sc = _master()
    assert sc.mergeSpeciesCollections(_trip()) == set()
    assert "? phacelia" in sc.sc_dict
