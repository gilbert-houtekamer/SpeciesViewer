from PyQt5.QtWidgets import QPlainTextEdit

class LogPanel(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setStyleSheet("background-color: #d0f0c0;")  # soft green
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setStyleSheet("""
            QPlainTextEdit {
                background-color: #d0f0c0;
                font-family: Consolas;
                font-size: 12px;
                padding: 5px;
            }
        """)

        # self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)        



