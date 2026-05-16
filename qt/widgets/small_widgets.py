from PyQt6.QtWidgets import QLabel, QSizePolicy
from PyQt6.QtCore import Qt

class HTMLLabel(QLabel):
    def __init__(self, html_text: str, parent=None):
        super().__init__(parent)
        self.setText(html_text)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(True)  # Enables clickable hyperlinks
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
