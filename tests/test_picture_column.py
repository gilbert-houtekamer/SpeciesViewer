"""The shared picture-on-the-left layout gives way on a narrow screen
(gui.wait_picture.picture_column). The offscreen platform's screen is 800 px."""

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QDialog, QLabel


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _dialog(app, content_width):
    from gui.wait_picture import picture_column
    d = QDialog()
    column = picture_column(d, "jay")
    label = QLabel("x")
    label.setMinimumWidth(content_width)
    column.addWidget(label)
    d.resize(1200, 400)
    d.show()
    app.processEvents()
    app.processEvents()
    pic = d.layout().itemAt(0).widget()
    return d, pic


def test_the_picture_stays_when_there_is_room(app):
    d, pic = _dialog(app, 300)
    assert pic.isVisible()
    d.close()


def test_the_picture_goes_and_the_window_shrinks_on_a_narrow_screen(app):
    d, pic = _dialog(app, 700)
    room = (d.screen() or app.primaryScreen()).availableGeometry().width()
    assert not pic.isVisible()
    assert d.width() <= room
    d.close()
