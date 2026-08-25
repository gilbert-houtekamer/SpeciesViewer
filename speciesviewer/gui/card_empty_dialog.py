"""Empty a card — check that every picture on it has been dealt with, save the
keepers to a second card, and mark the card as safe to format.

ONE job: answer "can I re-use this card?" and, when the answer is yes, make it
true. All the thinking lives in `engine.card_empty`; this is the picker, the
verdict table and the progress report.

Two cards are in play and they are not interchangeable:
  - the CARD TO EMPTY is the witness. Its own record is the only list of what
    was ever physically on it, and one card looks exactly like another.
  - the KEEPER CARD is where the pictures worth keeping go, off the laptop.

SpeciesViewer never formats the card and never deletes anything on it. The most
it writes there is a note saying the card is clear. The format is yours to do.

Nothing is remembered between openings: the card is re-checked every time, so
the screen cannot disagree with what is in the slot.
"""

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from engine import card_empty as ce
from utils.config import _config, persist_config
from utils.disk_space import human_bytes

CONFIG_SECTION = "emptycard"
CONFIG_KEY = "keeper_location"

# Columns of the per-day table. "Needs attention" is last but it is the one that
# decides the outcome, so it is also the only one that ever gets a colour.
DAY_COL, ON_CARD_COL, KEPT_COL, MARKED_COL, GONE_COL, ATTN_COL = range(6)


def keeper_location() -> str:
    return _config.get(CONFIG_SECTION, CONFIG_KEY, fallback="").strip()


def set_keeper_location(path: str):
    if not _config.has_section(CONFIG_SECTION):
        _config.add_section(CONFIG_SECTION)
    _config.set(CONFIG_SECTION, CONFIG_KEY, path.strip())
    persist_config()


class _CheckWorker(QObject):
    finished = pyqtSignal(dict, str)

    def __init__(self, card, project):
        super().__init__()
        self._card, self._project = card, project

    def run(self):
        try:
            self.finished.emit(ce.classify_card(self._card, self._project), "")
        except Exception as e:                       # a bad card must not kill the GUI
            self.finished.emit({}, str(e))


class _CopyWorker(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict, str)

    def __init__(self, report, keeper_root, trip_name, card_label, condemned):
        super().__init__()
        self._args = (report, keeper_root, trip_name, card_label, condemned)

    def run(self):
        report, keeper_root, trip_name, card_label, condemned = self._args
        try:
            out = ce.consolidate(
                report, keeper_root, trip_name, card_label,
                include_condemned=condemned,
                progress=lambda d, t, n: self.progress.emit(d, t, n))
            self.finished.emit(out, "")
        except Exception as e:
            self.finished.emit({}, str(e))


class EmptyCardDialog(QDialog):
    def __init__(self, parent, trip_name, project_root):
        super().__init__(parent)
        self.setWindowTitle("Empty a card")
        self._trip_name = str(trip_name or "")
        self._project = str(project_root or "")
        self._report: dict = {}
        self._thread = None
        self.resize(820, 560)

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "Before a card can be re-used, every picture on it has to have been "
            "dealt with — either kept, or deliberately thrown away. This checks the "
            "card against <b>{}</b>, copies the pictures you kept onto a second "
            "card, and then notes on the card itself that it is safe to format."
            "<br><br>SpeciesViewer never formats the card and never deletes "
            "anything on it.".format(self._trip_name or "this trip")))

        self._card_label = QLabel("— choose the card you want to empty —")
        outer.addLayout(self._path_row("Card to empty:", self._card_label,
                                       self._choose_card))
        self._keeper_label = QLabel(keeper_location() or "— not chosen —")
        outer.addLayout(self._path_row("Keeper card / folder:", self._keeper_label,
                                       self._choose_keeper))

        self._identity = QLabel("")
        self._identity.setWordWrap(True)
        outer.addWidget(self._identity)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Day", "On card", "Kept", "Marked for deletion", "Already gone",
             "Needs attention"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        for c in range(5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(ATTN_COL, QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self._table, 1)

        self._summary = QLabel("Choose a card, then press Check card.")
        self._summary.setWordWrap(True)
        outer.addWidget(self._summary)

        btns = QHBoxLayout()
        self._check_btn = QPushButton("Check card")
        self._check_btn.clicked.connect(self._check)
        btns.addWidget(self._check_btn)
        btns.addStretch(1)
        self._copy_btn = QPushButton("Save the keepers…")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy)
        btns.addWidget(self._copy_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btns.addWidget(self._close_btn)
        outer.addLayout(btns)

        self._card = ""

    # ── pickers ───────────────────────────────────────────────────────────

    def _path_row(self, caption, label, on_browse):
        row = QHBoxLayout()
        row.addWidget(QLabel(caption))
        label.setWordWrap(True)
        row.addWidget(label, 1)
        btn = QPushButton("Choose…")
        btn.clicked.connect(on_browse)
        row.addWidget(btn)
        return row

    def _choose_card(self):
        path = QFileDialog.getExistingDirectory(self, "The card you want to empty")
        if not path:
            return
        self._card = path
        self._card_label.setText(path)
        self._report = {}
        self._copy_btn.setEnabled(False)
        self._table.setRowCount(0)
        # Read the card's own verdict straight away: a card that was already
        # emptied should say so before anything is re-checked, because that is
        # the question being asked when a card is picked up at all.
        already = ce.clear_verdict(path)
        self._identity.setText(f"<b>{already}</b>" if already else "")
        self._summary.setText("Press Check card.")

    def _choose_keeper(self):
        path = QFileDialog.getExistingDirectory(self, "Where the keepers go")
        if not path:
            return
        set_keeper_location(path)
        self._keeper_label.setText(path)

    # ── check ─────────────────────────────────────────────────────────────

    def _check(self):
        if not self._card:
            self._summary.setText("Choose the card you want to empty first.")
            return
        if not self._project or not Path(self._project).is_dir():
            self._summary.setText(
                "This trip's pictures folder could not be found, so there is "
                "nothing to check the card against.")
            return
        self._busy(True)
        self._summary.setText("Checking the card against the trip…")
        QApplication.processEvents()
        self._thread = QThread()
        self._worker = _CheckWorker(self._card, self._project)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_checked)
        self._thread.start()

    def _on_checked(self, report, error):
        self._stop_thread()
        self._busy(False)
        if error:
            self._summary.setText(f"❌ The card could not be read: {error}")
            return
        self._report = report
        self._fill_table(report)

        ident = report.get("identity") or {}
        last = ident.get("last_read") or {}
        if last:
            self._identity.setText(
                f"This card was last read into <b>{last.get('trip', '?')}</b> on "
                f"{str(last.get('when', '?'))[:10]}.")

        lines = [ln for ln in ce.format_report(report)][1:]
        ok = ce.can_empty(report)
        if ok:
            warn = ce.fullness_warning(report)
            self._summary.setText(
                "✅ Every picture on this card has been dealt with — it can be "
                "emptied.<br>" + "<br>".join(lines)
                + (f"<br>⚠️ {warn}" if warn else ""))
        else:
            self._summary.setText(
                "❌ This card is <b>not</b> ready to be emptied:<br>"
                + "<br>".join(f"• {b}" for b in report.get("blockers", []))
                + "<br><br>" + "<br>".join(lines))
        self._copy_btn.setEnabled(ok)

    def _fill_table(self, report):
        days = report.get("days") or {}
        self._table.setRowCount(0)
        for day in sorted(days):
            counts = days[day]
            attn = sum(counts.get(v, 0) for v in ce.BLOCKING)
            r = self._table.rowCount()
            self._table.insertRow(r)
            cells = {
                DAY_COL: day,
                ON_CARD_COL: str(sum(counts.values())),
                KEPT_COL: str(counts.get(ce.KEEPER, 0)),
                MARKED_COL: str(counts.get(ce.CONDEMNED, 0)),
                GONE_COL: str(counts.get(ce.EXECUTED, 0) + counts.get(ce.PRESUMED, 0)),
                ATTN_COL: (f"{attn} still to deal with" if attn else "—"),
            }
            for col, text in cells.items():
                item = QTableWidgetItem(text)
                if col != DAY_COL and col != ATTN_COL:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                if col == ATTN_COL and attn:
                    item.setForeground(Qt.GlobalColor.red)
                self._table.setItem(r, col, item)

    # ── copy ──────────────────────────────────────────────────────────────

    def _copy(self):
        keeper = keeper_location()
        if not keeper:
            self._summary.setText("Choose where the keepers should go first.")
            return
        if not ce.can_empty(self._report):
            return                        # the button is disabled; belt and braces

        # Fullness is ADVICE, so it is asked, not enforced. The gate above is the
        # one that refuses.
        warn = ce.fullness_warning(self._report)
        if warn and QMessageBox.question(
                self, "Empty a card", f"{warn}\n\nGo ahead anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        counts = self._report.get("counts") or {}
        keepers = counts.get(ce.KEEPER, 0)
        condemned = counts.get(ce.CONDEMNED, 0)
        label = self._card_name()
        note = (f"\n\n{condemned} picture(s) you marked for deletion but have not "
                f"deleted yet will be copied too, into a 'condemned' folder — "
                f"formatting the card is what would make that final."
                if condemned else "")
        if QMessageBox.question(
                self, "Empty a card",
                f"Copy {keepers} kept picture(s) from {label} to\n{keeper}?{note}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return

        self._busy(True)
        self._summary.setText("Copying…")
        QApplication.processEvents()
        self._thread = QThread()
        self._worker = _CopyWorker(self._report, keeper, self._trip_name, label, True)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(
            lambda d, t, n: self._summary.setText(f"Copying {d}/{t} — {n}…"))
        self._worker.finished.connect(self._on_copied)
        self._thread.start()

    def _on_copied(self, out, error):
        self._stop_thread()
        self._busy(False)
        if error or out.get("error"):
            msg = error or out.get("error")
            self._summary.setText(f"❌ {msg}")
            QMessageBox.warning(self, "Empty a card",
                                f"The copy did not finish:\n{msg}\n\n"
                                f"The card has NOT been marked as safe to format.")
            return

        landed = out.get("copied", 0) + out.get("skipped", 0)
        msg = (f"{out.get('copied', 0)} picture(s) copied"
               + (f", {out['skipped']} already there" if out.get("skipped") else "")
               + f" — {human_bytes(out.get('bytes', 0))}, "
                 f"{out.get('sidecars', 0)} sidecar(s) went with them.")

        # The stamp is written LAST, from what actually landed.
        stamped = ce.stamp_cleared(self._card, self._trip_name,
                                   keeper_location(), out, self._report)
        if stamped:
            self._summary.setText(
                f"✅ {msg}<br><br><b>{landed} picture(s) are now on the keeper "
                f"card. This card is CLEAR — safe to format.</b><br>"
                f"Format it in the camera; SpeciesViewer will not.")
            self._identity.setText(f"<b>{ce.clear_verdict(self._card)}</b>")
        else:
            self._summary.setText(
                f"⚠️ {msg}<br><br>The card could not be marked as clear (it may be "
                f"write-protected). The pictures are safely copied, but check the "
                f"card again before you format it.")

    # ── plumbing ──────────────────────────────────────────────────────────

    def _card_name(self) -> str:
        """A label for this card's folder on the keeper card. The drive letter is
        no use — it is whatever Windows handed out this time — so use the volume
        label if there is one, else the date it was last read."""
        from utils.disk_space import report_volumes
        want = str(Path(self._card)).rstrip("\\/").lower()
        for row in report_volumes():
            if str(row["root"]).rstrip("\\/").lower() == want and row.get("label"):
                return f"card {row['label']}"
        last = ((self._report.get("identity") or {}).get("last_read") or {})
        when = str(last.get("when", ""))[:10]
        return f"card read {when}" if when else "card"

    def _busy(self, on: bool):
        self._check_btn.setEnabled(not on)
        self._close_btn.setEnabled(not on)
        if on:
            self._copy_btn.setEnabled(False)

    def _stop_thread(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
