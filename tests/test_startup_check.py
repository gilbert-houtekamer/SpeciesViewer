"""Validate the master's lists at start: silent when clean, a warning with examples
and the last clean time when something drifted. Pure report logic — no master."""

import pytest

pytest.importorskip("PyQt6")


def test_clean_is_silent_and_remembers_when():
    from gui.startup_check import report
    state = {}
    found = {"disk": (0, "", []), "orphans": (0, "", [])}
    assert report("M", found, state, now=1000.0) == []
    assert state["M"]["clean"] == 1000.0


def test_drift_warns_with_examples_last_clean_and_what_is_new():
    from gui.startup_check import report
    state = {"M": {"clean": 1000.0, "last": {"disk": 0, "orphans": 3}}}
    found = {"disk": (15, "15 rows for files that are gone", ["gone: a.xmp"]),
             "orphans": (3, "3 .xmp files", []),
             "gps": (None, "could not check: drive gone", [])}
    lines = report("M", found, state, now=2000.0)
    assert "last clean on this computer" in lines[0]
    text = "\n".join(lines)
    assert "Lists vs. Files on Disk: 15 rows" in text and "new since the last start" in text
    assert "gone: a.xmp" in text
    assert "same as the last start" in text                 # orphans 3 → 3
    assert "could not check" in text
    assert state["M"]["clean"] == 1000.0                     # not clean now
    assert state["M"]["last"] == {"disk": 15, "orphans": 3}


def test_off_setting_starts_nothing(monkeypatch):
    import threading
    import gui.startup_check as sc
    from utils.config import _config
    monkeypatch.setattr(_config, "get", lambda *a, **k: "no")
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))
    sc.start(object())
    assert started == []
