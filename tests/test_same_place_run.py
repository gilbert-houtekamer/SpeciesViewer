"""same_place_run: the no-GPS pictures that carry the same written place."""
from gui.map_view import same_place_run


def test_same_text_ignores_case_and_spacing_keeps_tray_order():
    seq = [("a", "Helgoland, Germany"), ("b", "Texel"),
           ("c", "helgoland,  germany"), ("d", "Helgoland")]
    assert same_place_run(seq, "c") == ["a", "c"]


def test_no_text_means_no_group():
    seq = [("a", ""), ("b", ""), ("c", "Texel")]
    assert same_place_run(seq, "a") == []
    assert same_place_run(seq, "c") == ["c"]
    assert same_place_run(seq, "zz") == []
