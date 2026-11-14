"""SetDateDialog — choose the capture date/time for pictures that have none.

ONE JOB: ask for ONE start time, each part (day, month, year, hour, minute,
second) editable on its own, and return it. Spreading the time over several
pictures (one second apart, never on a taken second) and the write itself live
in gui.viewer_clipboard.set_dates / engine.capture_date_write.
"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QDate, QDateTime, QTime
from PyQt6.QtWidgets import (QDateEdit, QDialog, QDialogButtonBox, QHBoxLayout,
                             QLabel, QSpinBox, QVBoxLayout, QTimeEdit)

from gui.widgets import wrapped


class SetDateDialog(QDialog):
    def __init__(self, parent, n: int, start: datetime, source: str, n_redo: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Set date/time")
        lay = QVBoxLayout(self)
        pics = f"{n} picture{'s' if n != 1 else ''}"
        if n_redo == n:
            what = (f"{pics} with a date set by hand earlier — an estimate; "
                    f"the new date replaces it.")
        elif n_redo:
            what = (f"{pics}: {n - n_redo} without a capture date, {n_redo} with a "
                    f"date set by hand earlier (replaced).")
        else:
            what = f"{pics} without a capture date."
        lay.addWidget(wrapped(
            what + " Choose the date and time"
            + (" of the first one; each next picture gets the next free second, "
               "so no two pictures share a time." if n > 1 else ".")
            + " A date the camera recorded is never changed."))
        lay.addWidget(wrapped(f"Starting from: {source}."))

        # Separate boxes, so every part can be changed on its own — by typing or
        # with the arrow keys (no mouse needed).
        row = QHBoxLayout()
        self._date = QDateEdit(QDate(start.year, start.month, start.day))
        self._date.setDisplayFormat("yyyy-MM-dd")
        self._date.setCalendarPopup(True)
        self._time = QTimeEdit(QTime(start.hour, start.minute, 0))
        self._time.setDisplayFormat("HH:mm")
        self._sec = QSpinBox()
        self._sec.setRange(0, 59)
        self._sec.setValue(start.second)
        self._sec.setPrefix(":")
        for label, w in (("Date", self._date), ("Time", self._time), ("Seconds", self._sec)):
            row.addWidget(QLabel(label))
            row.addWidget(w)
        row.addStretch()
        lay.addLayout(row)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._date.setFocus()

    def chosen(self) -> datetime:
        dt = QDateTime(self._date.date(), self._time.time()).toPyDateTime()
        return dt.replace(second=self._sec.value(), microsecond=0)
