"""The colour-delete confirm: pictures with a JPG go only when the box is ticked.

Headless (offscreen). No thumbnails, so no collection is needed; the dialog's
own state (header, Delete button, include_jpg) is what decides the delete.
"""
from PyQt6.QtWidgets import QApplication

# Keep the application referenced: an unreferenced QApplication is collected.
_APP = QApplication.instance() or QApplication([])

from gui.rating_editor import DeletePreviewDialog  # noqa: E402


def _dlg(keys, jpg_keys, last=()):
    return DeletePreviewDialog(None, None, keys, color="Blue", scope="flamingo",
                               show_thumbnails=False,
                               jpg_keys=jpg_keys, last_jpg_names=last)


def test_box_is_off_by_default_and_adds_the_jpg_pictures():
    d = _dlg(["a", "b"], ["j1", "j2", "j3"])
    assert d.include_jpg() is False
    assert d._delete.text() == "Delete 2"
    d._jpg_box.setChecked(True)
    assert d.include_jpg() is True
    assert d._delete.text() == "Delete 5"


def test_only_jpg_pictures_cannot_delete_until_ticked():
    d = _dlg([], ["j1"])
    assert not d._delete.isEnabled()
    d._jpg_box.setChecked(True)
    assert d._delete.isEnabled() and d._delete.text() == "Delete 1"


def test_no_jpg_pictures_means_no_box():
    d = _dlg(["a"], [])
    assert d._jpg_box is None and d.include_jpg() is False
