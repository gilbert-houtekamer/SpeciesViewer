"""Create Trip, first tab: one name, the rest suggested
(docs/plans/trip_create_naming_plan.md).

ONE JOB: let the user type a trip name and dates, show the folders and web
address that follow from them — each with what will happen to it — and hand
the chosen values to Create. Creates nothing itself.

Each suggested folder follows the name until the user edits it; "↺" puts the
suggestion back. Blocking problems (no name, the data folder holds something,
the web name is taken) are listed by problems(); a pictures folder that
already exists is only a warning — cards are often copied before the trip is
made.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QVBoxLayout, QWidget)

from engine import trip_naming
from gui import theme
from utils.config import (_config, get_currentpictures_root, get_trips_root,
                          get_virttrips_root)

WEB_FOLDER = "/trips/"      # fixed in code: the site layout assumes it (plan §1)


def _trip_admin_csv() -> str:
    master = _config.get("master", "masterbiotadatabase", fallback="").strip()
    return str(Path(master) / "admin" / "trip_admin.csv") if master else ""


class TripNamePanel(QWidget):
    def __init__(self, date_field, parent=None):
        super().__init__(parent)
        self.date_field = date_field
        self.trips_root = get_trips_root().strip()
        self.pictures_root = get_currentpictures_root().strip()
        self.used = trip_naming.used_web_names(self.trips_root, get_virttrips_root(),
                                               _trip_admin_csv())
        self._follow_data = self._follow_pictures = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Trip name</b>"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Spain")
        self.name.setMaximumWidth(260)
        top.addWidget(self.name)
        top.addSpacing(20)
        top.addWidget(date_field)
        top.addStretch()
        outer.addLayout(top)
        self.name_note = self._note()
        outer.addWidget(self.name_note)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)

        # Row 0 — SpeciesViewer's own files: the folder NAME only; it always
        # lives in the Trips folder, which Create records as the trips root.
        grid.addWidget(QLabel("SpeciesViewer's files"), 0, 0)
        data_row = QHBoxLayout()
        data_row.addWidget(QLabel(self._shown_root(self.trips_root)))
        self.data_name = QLineEdit()
        self.data_name.setMaximumWidth(260)
        data_row.addWidget(self.data_name)
        data_row.addStretch()
        grid.addLayout(data_row, 0, 1)
        self.data_reset = self._reset_button(lambda: self._reset("data"))
        grid.addWidget(self.data_reset, 0, 2)
        self.data_note = self._note()
        grid.addWidget(self.data_note, 1, 1, 1, 3)

        # Row 2 — your pictures: a full path; it may already exist anywhere.
        grid.addWidget(QLabel("Your pictures"), 2, 0)
        self.pictures = QLineEdit()
        grid.addWidget(self.pictures, 2, 1)
        self.pictures_reset = self._reset_button(lambda: self._reset("pictures"))
        grid.addWidget(self.pictures_reset, 2, 2)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_pictures)
        grid.addWidget(browse, 2, 3)
        self.pictures_note = self._note()
        grid.addWidget(self.pictures_note, 3, 1, 1, 3)

        # Row 4 — the web address, derived from the name, never typed.
        grid.addWidget(QLabel("Web address"), 4, 0)
        self.web = QLabel("")
        self.web.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(self.web, 4, 1, 1, 3)
        self.web_note = self._note()
        grid.addWidget(self.web_note, 5, 1, 1, 3)

        self.name.textChanged.connect(self._refresh)
        self.data_name.textEdited.connect(lambda _: self._edited("data"))
        self.pictures.textEdited.connect(lambda _: self._edited("pictures"))
        date_field.from_date.dateChanged.connect(lambda _: self._refresh())
        self._refresh()

    # ── small builders ────────────────────────────────────────────────────────
    def _note(self) -> QLabel:
        n = QLabel("")
        n.setWordWrap(True)
        return n

    def _reset_button(self, action) -> QPushButton:
        b = QPushButton("↺")
        b.setToolTip("Back to the suggested name")
        b.setFixedWidth(32)
        b.clicked.connect(action)
        b.setVisible(False)
        return b

    @staticmethod
    def _shown_root(root: str) -> str:
        return (str(Path(root)) + ("\\" if "\\" in str(Path(root)) else "/")) if root else ""

    def _set_note(self, label: QLabel, text: str, kind: str):
        colour = {"ok": theme.INK_FAINT, "warn": "#c98a00", "bad": theme.ALERT}[kind]
        label.setStyleSheet(f"color: {colour};")
        label.setText(text)

    # ── follow-until-edited ──────────────────────────────────────────────────
    def _edited(self, which):
        if which == "data":
            self._follow_data = False
            self.data_reset.setVisible(True)
        else:
            self._follow_pictures = False
            self.pictures_reset.setVisible(True)
        self._refresh()

    def _reset(self, which):
        if which == "data":
            self._follow_data = True
            self.data_reset.setVisible(False)
        else:
            self._follow_pictures = True
            self.pictures_reset.setVisible(False)
        self._refresh()

    def _browse_pictures(self):
        start = self.pictures.text().strip() or self.pictures_root
        while start and not Path(start).is_dir() and Path(start).parent != Path(start):
            start = str(Path(start).parent)
        chosen = QFileDialog.getExistingDirectory(self, "Choose the trip's pictures folder", start)
        if chosen:
            self.pictures.setText(str(Path(chosen)))
            self._edited("pictures")

    # ── the suggestions and their state ──────────────────────────────────────
    def _start_date(self) -> str:
        return self.date_field.get_dates()[0]

    def _refresh(self, *_):
        name = self.name.text().strip()
        problem = trip_naming.name_problem(name)
        self._set_note(self.name_note, problem if name else "", "bad")
        if self._follow_data:
            self.data_name.setText(trip_naming.web_name(name) + "db" if not problem else "")
        if self._follow_pictures:
            self.pictures.setText(
                str(trip_naming.suggest_pictures_folder(self.pictures_root, name, self._start_date()))
                if not problem and self.pictures_root else "")
        self._refresh_data()
        self._refresh_pictures()
        self._refresh_web(name, problem)

    def data_folder(self) -> Path | None:
        n = self.data_name.text().strip()
        return Path(self.trips_root) / n if (n and self.trips_root) else None

    def pictures_folder(self) -> Path | None:
        p = self.pictures.text().strip()
        return Path(p) if p else None

    def slug(self) -> str:
        return trip_naming.web_name(self.name.text())

    def _refresh_data(self):
        if not self.trips_root:
            return self._set_note(self.data_note, "Choose a Trips folder first: Help ▸ "
                                  "Setup Checklist ▸ Trips folder.", "bad")
        folder = self.data_folder()
        if folder is None:
            return self._set_note(self.data_note, "", "ok")
        if trip_naming.name_problem(folder.name):
            return self._set_note(self.data_note, trip_naming.name_problem(folder.name), "bad")
        if folder.exists() and any(folder.iterdir()):
            return self._set_note(self.data_note,
                                  f"✗ {folder} already exists — choose another name.", "bad")
        if folder.exists():
            return self._set_note(self.data_note, f"{folder} exists, empty — will be used.", "ok")
        self._set_note(self.data_note, f"{folder} — will be created.", "ok")

    def _refresh_pictures(self):
        if not self.pictures_root and self._follow_pictures:
            return self._set_note(self.pictures_note, "Choose a Pictures folder first: "
                                  "Help ▸ Setup Checklist ▸ Pictures folder — or Browse.", "bad")
        folder = self.pictures_folder()
        if folder is None:
            return self._set_note(self.pictures_note, "", "ok")
        if folder.is_dir():
            days = trip_naming.day_folder_count(folder)
            return self._set_note(
                self.pictures_note,
                f"⚠ Exists, {days} day folder{'s' if days != 1 else ''} — this trip will use "
                f"{'it' if days == 1 else 'them'}.",
                "warn")
        self._set_note(self.pictures_note, "Will be created.", "ok")

    def _refresh_web(self, name, problem):
        from utils import site_config
        slug = self.slug()
        if problem or not slug:
            self.web.setText("")
            return self._set_note(self.web_note, "", "ok")
        base = site_config.site_base()
        self.web.setText((base + WEB_FOLDER + slug) if base else (WEB_FOLDER + slug))
        if slug in self.used:
            return self._set_note(
                self.web_note, f"✗ “{slug}” is already used by {self.used[slug]} — "
                "change the name. A web address can never change later.", "bad")
        self._set_note(self.web_note, "✓ Free." if base else
                       "✓ Free — it goes online once you set up a website.", "ok")

    # ── for Create ───────────────────────────────────────────────────────────
    def problems(self) -> list[str]:
        """What blocks Create, in plain words; empty = go."""
        out = []
        name = self.name.text().strip()
        problem = trip_naming.name_problem(name)
        if problem:
            return [problem]
        if not self.trips_root:
            out.append("choose a Trips folder first (Help ▸ Setup Checklist)")
        folder = self.data_folder()
        if folder is None:
            out.append("the folder for SpeciesViewer's files has no name")
        elif trip_naming.name_problem(folder.name):
            out.append(trip_naming.name_problem(folder.name))
        elif folder.exists() and any(folder.iterdir()):
            out.append(f"{folder} already exists")
        if self.pictures_folder() is None:
            out.append("the pictures folder is not set")
        if self.slug() in self.used:
            out.append(f"the web name “{self.slug()}” is already used")
        return out

    def trip_name(self) -> str:
        return self.name.text().strip()
