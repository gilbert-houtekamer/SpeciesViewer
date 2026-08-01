"""Shared collapse/expand button used by the left panel stack, the right info
rail, and the log console (ch.15 main-window layout pass) so the same
affordance reads identically in all three places instead of three ad-hoc
styles."""
from PyQt6.QtWidgets import QPushButton, QWidget
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtCore import Qt, QSize

_CHIP_STYLE = """
QPushButton {
    background-color: #e3ead9;
    border: 1px solid #c9d6bb;
    border-radius: 12px;
    color: #2f3f29;
    font-weight: bold;
    font-size: 11pt;
    padding: 0px;
}
QPushButton:hover {
    background-color: #6d8764;
    border-color: #6d8764;
    color: white;
}
"""


class FoldChipButton(QPushButton):
    def __init__(self, collapse_char="‹", expand_char="›", collapsed=False, parent=None):
        super().__init__(parent)
        self._collapse_char = collapse_char
        self._expand_char = expand_char
        self.setFixedSize(24, 24)
        self.setStyleSheet(_CHIP_STYLE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_collapsed(collapsed)

    def set_collapsed(self, collapsed):
        self._collapsed = collapsed
        self.setText(self._expand_char if collapsed else self._collapse_char)
        self.setToolTip("Expand" if collapsed else "Collapse")


class VerticalLabel(QWidget):
    """Small bottom-to-top rotated label — shown in place of a collapsed
    panel's content so a collapsed left column still reads (e.g. "TRIP")
    instead of going fully blank."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._text = text
        self.setMinimumWidth(20)

    def setText(self, text):
        self._text = text
        self.update()

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(20, fm.horizontalAdvance(self._text) + 24)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QColor("#8b9784"))
        font = self.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.translate(self.width() / 2 + 4, self.height() - 8)
        painter.rotate(-90)
        painter.drawText(0, 0, self._text.upper())
        painter.end()
