"""gui.click_select — Ctrl/Shift click selection in the grids (backlog 1.19)."""
from gui.click_select import ClickSelector

ORDER = ["a", "b", "c", "d", "e"]


def test_plain_click_never_touches_selection():
    s = ClickSelector()
    assert s.click(ORDER, {"a", "c"}, None, "b", ctrl=False, shift=False) is None


def test_ctrl_click_toggles():
    s = ClickSelector()
    r = s.click(ORDER, {"a"}, None, "c", ctrl=True, shift=False)
    assert r.selected == {"a", "c"} and r.brackets is None
    r = s.click(ORDER, r.selected, "c", "a", ctrl=True, shift=False)
    assert r.selected == {"c"}


def test_shift_click_range_from_last_click_replaces_selection():
    s = ClickSelector()
    s.click(ORDER, set(), None, "b", ctrl=False, shift=False)
    r = s.click(ORDER, {"e"}, "b", "d", ctrl=False, shift=True)
    assert r.selected == {"b", "c", "d"}
    assert r.brackets == ("b", "d")


def test_repeated_shift_click_keeps_anchor():
    s = ClickSelector()
    s.click(ORDER, set(), None, "c", ctrl=False, shift=False)
    s.click(ORDER, set(), "c", "e", ctrl=False, shift=True)
    r = s.click(ORDER, set(), "e", "a", ctrl=False, shift=True)
    assert r.selected == {"a", "b", "c"}


def test_cursor_moved_by_keys_becomes_anchor():
    s = ClickSelector()
    s.click(ORDER, set(), None, "a", ctrl=False, shift=False)
    # arrow keys moved the cursor to "d" since
    r = s.click(ORDER, set(), "d", "e", ctrl=False, shift=True)
    assert r.selected == {"d", "e"}


def test_ctrl_shift_adds_range():
    s = ClickSelector()
    s.click(ORDER, set(), None, "b", ctrl=False, shift=False)
    r = s.click(ORDER, {"e"}, "b", "c", ctrl=True, shift=True)
    assert r.selected == {"b", "c", "e"} and r.brackets is None


def test_shift_click_without_anchor_or_filtered_anchor_selects_one():
    s = ClickSelector()
    r = s.click(ORDER, set(), None, "c", ctrl=False, shift=True)
    assert r.selected == {"c"}
    s2 = ClickSelector()
    s2.click(["x"] + ORDER, set(), None, "x", ctrl=False, shift=False)
    r = s2.click(ORDER, set(), "x", "d", ctrl=False, shift=True)   # "x" filtered out
    assert r.selected == {"d"} and r.brackets == ("d", "d")
