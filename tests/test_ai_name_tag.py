"""The AI's name pill on a grid card: as wide as its name (a reused card used
to keep the width of the first name it showed), shortened only when it does
not fit the picture."""
import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_name_tag_follows_each_name(qapp):
    from gui.rating_editor import _FramedPhoto
    photo = _FramedPhoto()
    photo.resize(380, 280)
    photo.show()
    photo.set_name_tag("woudaap 90%")
    short = photo._name_tag.width()
    photo.set_name_tag("White-tailed bumblebee 52%")
    assert photo._name_tag.width() > short
    assert photo._name_tag.text() == "White-tailed bumblebee 52%"     # whole, not cut
    photo.set_name_tag("woudaap 90%")
    assert photo._name_tag.width() == short                            # and back


def test_name_tag_shortened_only_when_too_long(qapp):
    from gui.rating_editor import _FramedPhoto
    photo = _FramedPhoto()
    photo.resize(120, 90)
    photo.show()
    photo.set_name_tag("Northern white-tailed bumblebee 16%")
    assert photo._name_tag.width() <= 120 - 16
    assert photo._name_tag.text().endswith("…")
