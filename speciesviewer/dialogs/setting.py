from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton

class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About")
        self.setFixedSize(300, 200)

        layout = QVBoxLayout()

        label = QLabel("🚀 Species Viewer 0.1 with PyQt6\n(c) 2025 Gilbert Inc.")
        label.setWordWrap(True)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        layout.addWidget(label)
        layout.addWidget(close_button)
        self.setLayout(layout)