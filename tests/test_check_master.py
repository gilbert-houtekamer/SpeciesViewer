"""Check the Master… (backlog 1.9): every repair as a report first. Headless,
with the checks replaced by fakes — nothing reads or writes a real master."""

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _window(app, monkeypatch, results):
    import gui.check_master as cm

    def fake(key):
        def fn(_master):
            r = results[key]
            if isinstance(r, Exception):
                raise r
            return r
        return fn
    monkeypatch.setattr(cm, "CHECKS", [(k, t, w, fake(k), f) for k, t, w, _fn, f in cm.CHECKS])
    fixed = []
    parent = QWidget()
    win = cm.CheckMasterWindow(parent, "M", {k: (lambda k=k: fixed.append(k))
                                             for k in ("refresh_review", "refresh_apply",
                                                       "recount", "audit", "fill_gps", "orphans")})
    win._thread.wait()
    app.processEvents()
    return win, fixed, parent


def test_each_check_reports_on_its_own_line(app, monkeypatch):
    win, _fixed, _p = _window(app, monkeypatch, {
        "disk": (3, "3 rows"), "counters": (0, ""), "gps": (2, "2 pictures"),
        "orphans": RuntimeError("drive gone"), "readcopy": (0, "")})
    assert win._status["disk"].text().startswith("⚠")
    assert "nothing to repair" in win._status["counters"].text()
    assert "could not check" in win._status["orphans"].text()   # the others still ran
    assert "nothing to repair" in win._status["readcopy"].text()
    # A fix only when there is something to fix; a report always.
    recount, report = win._buttons["counters"]
    assert not recount.isEnabled() and report.isEnabled()
    assert all(b.isEnabled() for b in win._buttons["disk"])
    win._thread = None
    win.close()


def test_a_fix_runs_its_tool_and_checks_that_row_again(app, monkeypatch):
    win, fixed, _p = _window(app, monkeypatch, {
        "disk": (0, ""), "counters": (0, ""), "gps": (2, "2 pictures"),
        "orphans": (0, ""), "readcopy": (0, "")})
    win._buttons["gps"][0].click()
    assert fixed == ["fill_gps"]
    assert win._status["gps"].text() == "checking…"
    win._thread.wait()
    app.processEvents()
    win._thread = None
    win.close()
