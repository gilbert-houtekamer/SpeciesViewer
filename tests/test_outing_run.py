"""outing_run: the pictures shot in one go around a picture."""
import datetime as dt
from gui.map_view import outing_run


def _t(h, m):
    return dt.datetime(2013, 10, 25, h, m)


def test_chain_stops_at_a_long_gap():
    seq = [("a", _t(16, 0)), ("b", _t(16, 50)), ("c", _t(17, 5)),
           ("d", _t(17, 20)), ("e", _t(18, 0))]
    assert outing_run(seq, "c") == ["b", "c", "d"]
    assert outing_run(seq, "a") == ["a"]


def test_undated_is_its_own_outing():
    seq = [("a", _t(16, 0)), ("x", None), ("b", _t(16, 5))]
    assert outing_run(seq, "x") == ["x"]
    assert outing_run(seq, "a") == ["a", "b"]


def test_unknown_key():
    assert outing_run([("a", _t(1, 0))], "zz") == []
