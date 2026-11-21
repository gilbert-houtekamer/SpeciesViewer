"""Master Trash… and Rebuild Websites… (backlog 1.9 / 1.3): one window each.
Built headless against an empty tmp master; nothing is written or persisted."""

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_master_trash_restores_and_empties_in_one_window(app, tmp_path):
    from settings.trash_restore import RestoreTrashDialog
    parent = QWidget()
    dlg = RestoreTrashDialog(parent, str(tmp_path))
    assert dlg.windowTitle() == "Master Trash"
    assert dlg._restore_btn.text() == "Restore selected"
    assert dlg._empty_btn.text() == "Empty trash…"
    dlg.close()


def test_rebuild_websites_offers_the_master_and_the_albums(app):
    from gui.rebuild_all_dialog import RebuildAllWebsitesDialog
    parent = QWidget()
    dlg = RebuildAllWebsitesDialog(parent)
    assert dlg.windowTitle() == "Rebuild Websites"
    assert dlg.master_cb.isEnabled() and dlg.albums_cb.isEnabled()
    assert dlg.run_btn.text() == "Rebuild…"
    dlg.close()
