"""The first thing a new SpeciesViewer shows: no master collection is set, and
nothing gets far without one — so offer to create it straight away, in the
suggested folder or one the user picks. "Later" leads on to the Setup
Checklist as before. Once a collection is set, this window never appears."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from gui.widgets import wrapped
from utils import default_folders
from utils.config import _config


def needs_welcome() -> bool:
    """True only while NO master collection is set. A set one that cannot be
    found (an unplugged drive) is the Setup Checklist's business, not this."""
    return not _config.get("master", "masterbiotadatabase", fallback="").strip()


class WelcomeDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setWindowTitle("Welcome to SpeciesViewer")
        self.setMinimumWidth(520)
        suggested = default_folders.master_folder()

        title = QLabel("Welcome to SpeciesViewer")
        title.setStyleSheet("font-size: 18pt; font-weight: 600;")
        intro = wrapped(
            "First, a master collection: the folder where the best pictures of every "
            "species you photograph will live. Choose a disk with room to grow.", self)
        where = wrapped(f"Suggested folder:<br><b>{suggested}</b>", self)
        where.setTextFormat(Qt.TextFormat.RichText)
        note = wrapped(
            "Already have a collection? Choose Later, and pick it under "
            "Master collection in the Setup Checklist.", self)

        create = QPushButton("Create")
        create.setDefault(True)
        create.clicked.connect(lambda: self._create(suggested))
        other = QPushButton("Choose another folder…")
        other.clicked.connect(lambda: self._create(None))
        later = QPushButton("Later")
        later.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(create)
        buttons.addWidget(other)
        buttons.addStretch(1)
        buttons.addWidget(later)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        for w in (title, intro, where, note):
            layout.addWidget(w)
        layout.addSpacing(6)
        layout.addLayout(buttons)

    def _create(self, folder):
        """The same creation as Master ▸ Masters (it checks the folder is new
        or empty, and asks for the restart). Stay open if nothing was made."""
        from gui.master_switch import create_collection
        create_collection(self.main_window, folder=folder)
        if not needs_welcome():
            self.accept()


def show_welcome(main_window) -> None:
    WelcomeDialog(main_window).exec()
