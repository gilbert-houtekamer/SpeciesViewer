"""Filing an unfiled day under a trip, and the two settings that make the inbox
possible at all.

ONE JOB: answer "which trip does this day belong to?" and move it there.

The inbox ITSELF is no longer here. It used to be a modal dialog you opened from
Utilities; it is now the Unfiled pill in the mode strip — gui/unfiled_overview.py
— so the days you are filing are visible in the panel while you work rather than
in a window on top of it. Two surfaces listing the same folders could disagree
after a move, so there is only one.

What stays here is what the panel calls into:
  _PickTripDialog          — choose the trip, or make one on the spot
  read_card_into_unfiled   — the other way days arrive
  ensure_currentpictures_root — the ask-once setting both depend on

Filing is a directory move. Deliberately offered BEFORE Read & Summarize: at that
point nothing in the trip or the master points at the old path, so the move costs
nothing. Once a day has been summarised or frozen, moving it is no longer this
module's business.
"""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMessageBox, QComboBox, QDialogButtonBox,
)

from engine import unfiled
from gui import theme


class _PickTripDialog(QDialog):
    """Choose the trip to file a day under. Trips whose date range covers the day
    are listed first and preselected — a suggestion, not a decision: overlapping
    ranges are normal (a virtual trip over the same dates), so the whole list
    stays reachable."""

    def __init__(self, parent, day: str, trips: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("File day under a trip")
        # Sized to match the rest of the app's dialogs rather than shrink-wrapping
        # its own contents: at the minimum Qt would pick, the trip combo truncates
        # the very date range you are here to check, and the warning wraps into a
        # wall. Room is cheap; re-reading a squeezed line is not.
        self.setMinimumWidth(660)
        self._day = day
        self._main_window = parent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(12)

        lay.addWidget(QLabel(f"<b>{day}</b>"))

        self.note = QLabel("")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        self.combo = QComboBox()
        self.combo.setMinimumHeight(28)
        # Without this the combo's sizeHint is the longest trip title plus its id
        # and date range — one verbose trip name and the dialog opens half a
        # screen wide. Let it ask for a sane width and elide the rest; the full
        # text is still there when the list drops down.
        self.combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(48)
        lay.addWidget(self.combo)

        self.dest_lbl = QLabel("")
        self.dest_lbl.setWordWrap(True)
        self.dest_lbl.setStyleSheet(f"color:{theme.INK_SOFT}; font-size:11px;")
        lay.addWidget(self.dest_lbl)
        self.combo.currentIndexChanged.connect(self._show_dest)

        lay.addStretch(1)
        row = QHBoxLayout()
        # The old dead end was a message box telling you to go to Trips ▸ Create
        # Trip and come back. The day you are filing is right here, and its date
        # is exactly what a new trip needs, so make the trip from here.
        self.new_trip_btn = QPushButton("New trip…")
        self.new_trip_btn.setToolTip(
            "Create a trip for this day, then come straight back with it selected.")
        self.new_trip_btn.clicked.connect(self._create_trip)
        row.addWidget(self.new_trip_btn)
        row.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("Move day")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        lay.addLayout(row)

        self._load(trips)
        # An explicit size, not sizeHint: the warning line appears and disappears
        # with the trip list, so a content-driven dialog would change shape under
        # the pointer between one day and the next.
        self.resize(820, 330)

    def _load(self, trips: list[dict]):
        """Fill the combo from a fresh trips_covering() result. Called again
        after a trip is created, so the new one can be preselected."""
        self._trips = trips
        self.combo.blockSignals(True)
        self.combo.clear()
        for t in trips:
            mark = "● " if t["covers"] else "   "
            span = f'  ({t["fromdate"]} … {t["todate"]})' if t["fromdate"] else ""
            self.combo.addItem(f'{mark}{t["title"]}  [{t["trip_id"]}]{span}', t)
        self.combo.blockSignals(False)

        covering = [t for t in trips if t["covers"]]
        if covering:
            self.note.setText(f"{len(covering)} trip(s) cover this date — shown first.")
            self.note.setStyleSheet("")
        elif trips:
            self.note.setText("No trip's dates cover this day — pick one anyway, "
                              "widen the trip's dates first, or make a new trip.")
            self.note.setStyleSheet(f"color:{theme.WARN};")
        else:
            self.note.setText("No trip has a pictures folder to move this day "
                              "into. Make one with “New trip…”.")
            self.note.setStyleSheet(f"color:{theme.WARN};")
        self._ok_btn.setEnabled(bool(trips))
        self._show_dest()

    def _create_trip(self):
        """Open Create Trip, then re-read the trip list and preselect what covers
        this day. Create Trip is left exactly as it is — it starts empty and
        inherits nothing, which is the rule that keeps it from overwriting a live
        trip's identity — we only ask it the question and re-read the answer."""
        from gui.trip_process import TripCreationDialog
        mw = self._main_window
        spec = None
        finder = getattr(mw, "_find_trip_menu_spec", None)
        if callable(finder):
            spec = finder("Create Trip")
        before = {t["trip_id"] for t in self._trips}
        TripCreationDialog(mw, spec, mode="create").exec()

        trips = unfiled.trips_covering(self._day)
        self._load(trips)
        # Prefer a trip that did not exist a moment ago; fall back to the first
        # covering one. Both are guesses the user can override — the combo is
        # still the whole list.
        new = [i for i, t in enumerate(trips) if t["trip_id"] not in before]
        covering = [i for i, t in enumerate(trips) if t["covers"]]
        if new:
            self.combo.setCurrentIndex(new[0])
        elif covering:
            self.combo.setCurrentIndex(covering[0])

    def _show_dest(self):
        t = self.combo.currentData()
        self.dest_lbl.setText(f"Moves into:  {t['picture_root']}" if t else "")

    def chosen(self) -> dict | None:
        return self.combo.currentData()


def _active_picture_dir() -> str:
    """A sample path to guess the pictures base from, when it is not configured."""
    try:
        from utils.config import _config
        return _config.get("project", "picturedirectory", fallback="")
    except Exception:
        return ""


def ensure_currentpictures_root(parent) -> str:
    """The pictures base, ASKED ONCE and then stored — never silently guessed.

    A guess is fine for pre-filling a field the user can see; it is not fine as
    the thing that decides where a card is written. A card of mushroom photos
    from last autumn must land in `<base>/2025/2025 unfiled/`, and that is only
    predictable if `base` is a settled fact. Stored in [currentpictures]
    rootdirectory — the same setting that makes a trip.ini's picture_root
    portable across machines. Returns "" when the user declines."""
    from utils.config import get_currentpictures_root, set_setting
    from PyQt6.QtWidgets import QFileDialog
    current = get_currentpictures_root()
    if current and os.path.isdir(current):
        return os.path.normpath(current)
    guess = unfiled.guess_currentpictures_root(_active_picture_dir())
    QMessageBox.information(
        parent, "Where do you keep your pictures?",
        "SpeciesViewer needs to know the folder that holds your picture years "
        "(the one with 2025, 2026, … inside it).\n\n"
        "Unfiled cards are read into <that folder>\\<year>\\<year> unfiled, and "
        "trips store their pictures folder relative to it — so a trip stays "
        "readable on another machine.\n\n"
        "You are asked this once."
        + (f"\n\nSuggested: {guess}" if guess else ""))
    from pathlib import Path
    from utils.start_folder import start_folder
    chosen = QFileDialog.getExistingDirectory(
        parent, "Pictures folder (the one containing 2025, 2026, …)",
        start_folder(guess, Path.home() / "Pictures"))
    if not chosen:
        return ""
    chosen = os.path.normpath(chosen)
    set_setting("currentpictures", "rootdirectory", chosen)
    try:
        parent.logger.info(f"Pictures root set to {chosen} "
                           f"([currentpictures] rootdirectory).")
    except Exception:
        pass
    return chosen


def open_unfiled_inbox(main_window):
    """Show the inbox — which now means switching the mode strip to Unfiled.

    Kept as a function because the menu still offers the way in; it just no
    longer opens a window. The pictures-root question is asked first, exactly as
    before, since an empty panel and an unanswered setting look the same.
    """
    if not ensure_currentpictures_root(main_window):
        return
    from gui.mainwindow import MODE_UNFILED
    main_window._set_left_mode(MODE_UNFILED)


def read_card_into_unfiled(main_window):
    """Read a card into the unfiled inbox — no trip is chosen or claimed.
    Non-modal — the copy runs on its own QThread — so the rest of SV stays
    usable while it runs."""
    if not ensure_currentpictures_root(main_window):
        return
    from gui.trip_process import ReadSDCardDialog
    if not main_window._try_start_background_op("Read SD-card or Folder"):
        return
    dlg = ReadSDCardDialog(main_window, None, unfiled_mode=True)
    main_window._read_sdcard_dlg = dlg  # keep alive while non-modal
    dlg.finished.connect(lambda *_: (main_window._end_background_op(),
                                     setattr(main_window, "_read_sdcard_dlg", None)))
    dlg.show()
