"""engine.day_ai_marks: an AI… Accept recorded first, confirmed, undone only
where the picture still has what the AI set."""
from engine import day_ai_marks as m


def test_record_confirm_and_undo_plan(tmp_path):
    log = tmp_path / "accepts.json"
    eid = m.start("D:/day", {"a": ["", "Purple"], "b": ["Green", "Purple"],
                             "c": ["", "Purple"]},
                  {"a": ["", "buizerd"], "d": ["", "kievit"]}, path=log)
    m.confirm(eid, {"a", "b"}, {"a"}, path=log)          # c and d did not take it
    e = m.last("D:/day", path=log)
    assert set(e["colours"]) == {"a", "b"} and set(e["species"]) == {"a"}
    colour_now = {"a": "Purple", "b": "Red"}.get           # b: you changed it since
    species_now = {"a": "buizerd"}.get
    colours, species, changed = m.undo_plan(e, colour_now, species_now)
    assert colours == {"a": ""} and species == {"a": ""} and changed == 1
    m.forget(eid, path=log)
    assert m.last("D:/day", path=log) is None


def test_an_accept_that_changed_nothing_is_dropped(tmp_path):
    log = tmp_path / "accepts.json"
    eid = m.start("D:/day", {"a": ["", "Purple"]}, {}, path=log)
    m.confirm(eid, set(), set(), path=log)
    assert m.last("D:/day", path=log) is None
