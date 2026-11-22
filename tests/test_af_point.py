"""engine.af_point: OM System positions as recorded, Nikon names on the grid."""
import pytest

from engine import af_point as ap


def test_nikon_centre_point_is_the_middle_of_the_frame():
    for model, name in (("NIKON D7100", "C6"), ("NIKON D500", "E9 (Center)")):
        x0, y0, x1, y1 = ap.nikon_box(model, name)
        assert (x0 + x1) / 2 == pytest.approx(0.5) and (y0 + y1) / 2 == pytest.approx(0.5)


def test_nikon_rows_go_down_and_columns_right():
    c = ap.nikon_box("NIKON D7100", "C6")
    d = ap.nikon_box("NIKON D7100", "D6")         # one row lower
    c7 = ap.nikon_box("NIKON D7100", "C7")        # one column right
    assert d[1] > c[1] and d[0] == c[0]
    assert c7[0] > c[0] and c7[1] == c[1]
    assert ap.nikon_box("NIKON D90", "Center") is None      # not placed
    assert ap.nikon_box("NIKON D7100", "(none)") is None


def test_parse_info_nikon_and_om():
    nik = ap.parse_info({"Model": "NIKON D500", "PrimaryAFPoint": "E9 (Center)",
                         "AFPointsInFocus": "E7", "Orientation": 1})
    assert nik["name"] == "E9" and nik["estimated"] and len(nik["in_focus"]) == 1
    assert nik["in_focus"][0][0] < nik["box"][0]             # E7 lies left of E9
    om = ap.parse_info({"Model": "OM-1", "AFPointSelected": "(45%,52%) (45%,52%)",
                        "AISubjectTrackingMode": "Birds; Object Found", "Orientation": 1})
    assert not om["estimated"] and om["found"] is True
    assert om["box"][0] == pytest.approx(0.43)
