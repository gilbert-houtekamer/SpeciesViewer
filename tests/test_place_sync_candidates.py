"""Hand-placed GPS not in the list (backlog 1.17): a camera fix never needs the
movable flag, so it is not counted for that — only for a missing place name."""

from gui.place_sync import sync_candidates


class _FakeDB:
    def pictures_missing_place_or_provenance(self):
        return [
            {"upn": "cam_named", "need_place": False, "need_prov": True},   # camera, fine
            {"upn": "cam_noname", "need_place": True, "need_prov": True},   # camera, no name
            {"upn": "placed", "need_place": False, "need_prov": True},      # hand-placed
        ]

    def upns_with_camera_fix(self, upns):
        return {u for u in upns if u.startswith("cam_")}


def test_camera_fixes_are_not_counted_for_the_flag():
    got = {c["upn"]: c for c in sync_candidates(_FakeDB())}
    assert "cam_named" not in got                                   # the old 8,599
    assert got["cam_noname"] == {"upn": "cam_noname", "need_place": True, "need_prov": False}
    assert got["placed"]["need_prov"] is True


def test_manage_caches_shows_the_master_read_copy():
    import pytest
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    from gui.cache_manager import ManageCachesDialog
    parent = QWidget()
    dlg = ManageCachesDialog(parent)
    assert dlg._db_size_lbl.text()            # "not built" in the test sandbox
    dlg.close()


def test_copy_reuses_a_recent_read_only_for_the_same_master():
    import time
    import gui.place_sync as ps
    plan = {"loc_updates": {"a": {"City": "X"}}, "prov_updates": {}, "scanned": 1}
    ps._last_plan.clear()
    ps._last_plan.update(root="M1", plan=plan, ts=time.time())
    assert ps._recent_plan("M1") is plan
    assert ps._recent_plan("M2") is None
    ps._last_plan["ts"] = time.time() - ps._FRESH_SECONDS - 1
    assert ps._recent_plan("M1") is None
    ps._last_plan.clear()
