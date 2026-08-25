"""Empty a card — check that every picture on it has been dealt with, save the
keepers to a second card, and mark the card as safe to format.

ONE job: answer "can I re-use this card?" and, when the answer is yes, make it
true. All the thinking lives in `engine.card_empty`; this is the picker, the
verdict table and the progress report.

Two cards are in play and they are not interchangeable:
  - the CARD TO EMPTY is the witness. Its own record is the only list of what
    was ever physically on it, and one card looks exactly like another.
  - the KEEPER CARD is where the pictures worth keeping go, off the laptop.

SpeciesViewer never formats the card. It will delete the pictures from it, but
only ones it has just verified on the keeper card, only as a separate second
click, and never the files it does not recognise.

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
CONFIG_KEY_CARD = "card_location"

# Columns of the per-day table. "Needs attention" is last but it is the one that
# decides the outcome, so it is also the only one that ever gets a colour.
DAY_COL, ON_CARD_COL, KEPT_COL, MARKED_COL, GONE_COL, ATTN_COL = range(6)


def _remembered(key: str) -> str:
    return _config.get(CONFIG_SECTION, key, fallback="").strip()


def _remember(key: str, path: str):
    if not _config.has_section(CONFIG_SECTION):
        _config.add_section(CONFIG_SECTION)
    _config.set(CONFIG_SECTION, key, path.strip())
    persist_config()


def keeper_location() -> str:
    return _remembered(CONFIG_KEY)


def set_keeper_location(path: str):
    _remember(CONFIG_KEY, path)


def card_location() -> str:
    """The card slot last used. A drive letter is a weak thing to remember — the
    reader may hand out a different one next time — but it is the same one all
    day on one machine, and re-typing it for every card of a trip is the kind of
    friction that stops a safety step being used at all. Re-checked on every
    open, never trusted: if it is gone, the field simply starts empty."""
    remembered = _remembered(CONFIG_KEY_CARD)
    return remembered if remembered and Path(remembered).is_dir() else ""


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
        self._cancel = False

    def cancel(self):
        """Asked for when the window is closing. The copy stops between files;
        whatever has already landed stays, and a re-run finishes the job."""
        self._cancel = True

    def run(self):
        report, keeper_root, trip_name, card_label, condemned = self._args
        try:
            out = ce.consolidate(
                report, keeper_root, trip_name, card_label,
                include_condemned=condemned,
                should_cancel=lambda: self._cancel,
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
        self._preview = None
        # Opens at about two thirds of a laptop screen. Do not ask for less than
        # the button row's own minimum (~1010 px): the layout wins that argument
        # silently, and a resize() nobody honours is worse than no resize() at
        # all, because the next person changes the number instead of the cause.
        self.resize(1040, 620)

        outer = QVBoxLayout(self)
        # ⚠ WORD WRAP, or this paragraph sets the width of the whole window. A
        # QLabel without it reports the width of its longest unbroken line as
        # its minimum — 3288 px for this text — and minimumSizeHint beats
        # resize(), so the dialog opened wider than a laptop screen no matter
        # what size was asked for. Every long label on this dialog wraps.
        intro = QLabel(
            "Before a card can be re-used, every picture on it has to have been "
            "dealt with — either kept, or deliberately thrown away. This checks the "
            "card against <b>{}</b>, copies the pictures you kept onto a second "
            "card, and then notes on the card itself that it is safe to re-use."
            "<br><br>Deleting the pictures from the card afterwards is a separate "
            "step, offered only once the copies have been verified. Files "
            "SpeciesViewer does not recognise are never touched."
            .format(self._trip_name or "this trip"))
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self._card = card_location()
        self._card_label = QLabel(self._card or "— choose the card you want to empty —")
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
        self._attn_btn = QPushButton("Deal with these here…")
        self._attn_btn.setToolTip("Open the pictures still needing a colour, so you "
                                  "can set one here instead of in PhotoLab")
        self._attn_btn.setEnabled(False)
        self._attn_btn.clicked.connect(self._open_attention)
        btns.addWidget(self._attn_btn)
        btns.addStretch(1)
        self._copy_btn = QPushButton("Save the keepers…")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy)
        btns.addWidget(self._copy_btn)
        self._empty_btn = QPushButton("Delete from the card…")
        self._empty_btn.setToolTip("Only offered once the pictures are verified "
                                   "on the keeper card")
        self._empty_btn.setEnabled(False)
        self._empty_btn.clicked.connect(self._empty_card)
        btns.addWidget(self._empty_btn)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btns.addWidget(self._close_btn)
        outer.addLayout(btns)

        if self._card:
            self._show_clear_verdict()

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
        _remember(CONFIG_KEY_CARD, path)
        self._card_label.setText(path)
        self._report = {}
        self._copy_btn.setEnabled(False)
        self._attn_btn.setEnabled(False)
        self._table.setRowCount(0)
        self._show_clear_verdict()

    def _show_clear_verdict(self):
        """The card's own answer, read before anything is re-checked — that is the
        question being asked when a card is picked up at all."""
        already = ce.clear_verdict(self._card)
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
        bits = []
        # The clear verdict FIRST and in bold: it is the answer to the question
        # the user is holding the card to ask. It used to be overwritten by the
        # "last read into" line, which is background by comparison.
        cleared = ce.clear_verdict(self._card)
        if cleared:
            bits.append(f"<b>{cleared}</b>")
        if last:
            bits.append(f"Last read into <b>{last.get('trip', '?')}</b> on "
                        f"{str(last.get('when', '?'))[:10]}.")
        emptied = ident.get("emptied") or []
        if emptied:
            bits.append(f"Last emptied on {str(emptied[-1].get('when', '?'))[:10]} "
                        f"({emptied[-1].get('deleted', '?')} picture(s) deleted).")
        self._identity.setText("<br>".join(bits))

        lines = [ln for ln in ce.format_report(report)][1:]

        # An already-emptied card is neither ready nor blocked: it is done. It
        # gets its own, quiet answer rather than a red refusal.
        if ce.nothing_to_do(report):
            # `lines` already has the "last read into" line trimmed off the
            # front — the identity label above shows that, and repeating it
            # under the table reads as a stutter.
            self._summary.setText("<br>".join(lines))
            self._copy_btn.setEnabled(False)
            self._attn_btn.setEnabled(False)
            self._empty_btn.setEnabled(False)
            return

        ok = ce.can_empty(report)
        if ok:
            warn = ce.fullness_warning(report)
            self._summary.setText(
                "✅ Every picture on this card has been dealt with — it can be "
                "emptied.<br>" + "<br>".join(lines)
                + (f"<br>⚠️ {warn}" if warn else ""))
        else:
            unreachable = report["counts"].get(ce.UNACCOUNTED, 0)
            tail = ""
            if unreachable:
                tail = (f"<br><br>{unreachable} of these are only on the card and "
                        f"cannot be opened from here — read the card in first.")
            self._summary.setText(
                "❌ This card is <b>not</b> ready to be emptied:<br>"
                + "<br>".join(f"• {b}" for b in report.get("blockers", []))
                + "<br><br>" + "<br>".join(lines) + tail)
        self._copy_btn.setEnabled(ok)
        self._attn_btn.setEnabled(bool(self._attention_paths()))
        # Deleting is offered only for a card that has ALREADY been saved — this
        # session or a previous one. It is never part of the same click as the
        # copy: the copy is reversible, this is not.
        self._empty_btn.setEnabled(bool(ce.clear_verdict(self._card)))

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

    # ── deal with the stragglers, without leaving SV ──────────────────────

    def _attention_paths(self) -> list[str]:
        """The pictures holding this card back, that we can actually show.

        A frame that never reached the laptop has nothing to open — it exists
        only on the card — so it is excluded here and called out separately. It
        is also the one case this button cannot solve: the fix for it is to read
        the card again, not to set a colour.
        """
        return [r["path"] for r in self._report.get("files", [])
                if r["verdict"] in ce.BLOCKING and r.get("path")]

    def _open_attention(self):
        """Open the culling grid over exactly those pictures, so a colour can be
        set here rather than in PhotoLab, and re-check when it closes."""
        paths = self._attention_paths()
        if not paths:
            return
        from gui.image_viewer import ImagePreviewDialog
        from gui.sidecar_provider import SidecarCollection
        from gui.preview_provider import preview_cache_dir_for

        cache_root = str(preview_cache_dir_for(Path(self._project)))
        collection = SidecarCollection(paths, cache_root=cache_root)
        # mode="all": these are RAWs, and the default JPG-only overlay would show
        # a count with an empty grid under it.
        dlg = ImagePreviewDialog(self, collection=collection, mode="all",
                                 cache_root=cache_root,
                                 source_label="Still to deal with before this card "
                                              "can be emptied")
        # This dialog runs application-modal, and a modal window blocks input to
        # every other window EXCEPT one that is itself modal and higher on the
        # stack. Without this the grid appears and cannot be scrolled or closed.
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.finished.connect(self._after_attention)
        self._preview = dlg                  # keep a reference; a local would be GC'd
        dlg.show()

    def _after_attention(self, *_):
        """Straight back to the check. Setting the last colour and then having to
        find the button again is exactly the moment to close the loop for the
        user — and the sidecar cache notices the changed .xmp on its own."""
        self._preview = None
        if self.isVisible():          # not on the way out — see closeEvent
            self._check()

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
                                   keeper_location(), out, self._report,
                                   folder=ce.card_folder_name(self._trip_name,
                                                              self._card_name()))
        if stamped:
            self._summary.setText(
                f"✅ {msg}<br><br><b>{landed} picture(s) are now on the keeper "
                f"card. This card is CLEAR — safe to format.</b><br>"
                f"Format it in the camera; SpeciesViewer will not.")
            self._identity.setText(f"<b>{ce.clear_verdict(self._card)}</b>")
            self._empty_btn.setEnabled(True)
        else:
            self._summary.setText(
                f"⚠️ {msg}<br><br>The card could not be marked as clear (it may be "
                f"write-protected). The pictures are safely copied, but check the "
                f"card again before you format it.")

    # ── emptying the card ─────────────────────────────────────────────────

    def _empty_card(self):
        """Delete the pictures we have verified copies of. Separate click,
        separate confirmation, and the plan is rebuilt from what is on the card
        RIGHT NOW rather than from the report on screen."""
        # Give the card an identity if it has none (cards stamped before ids
        # existed have none), BEFORE the plan is built — so the id is on the card
        # for the whole window in which a swap would matter.
        ce.ensure_card_id(self._card)
        stamp = (ce.read_card_index(self._card) or {}).get("cleared") or {}
        folder = stamp.get("folder") or ce.card_folder_name(self._trip_name,
                                                            self._card_name())
        keeper = stamp.get("keeper_card") or keeper_location()
        # card_folder_name is re-applied by plan_delete, so hand it the label
        # half of the folder that was actually used.
        label = folder[len(self._trip_name) + 3:] if folder.startswith(
            self._trip_name + " - ") else ""
        plan = ce.plan_delete(self._card, self._report, keeper,
                              self._trip_name, label)
        if plan.get("error"):
            QMessageBox.warning(self, "Empty a card", plan["error"])
            return
        if not plan["delete"]:
            QMessageBox.information(
                self, "Empty a card",
                "There is nothing on this card that is safe to delete — either "
                "it is already empty, or the copies could not be found where "
                "they were expected.")
            return

        from utils.disk_space import human_bytes
        lines = [f"Delete {len(plan['delete'])} file(s) from {self._card}, "
                 f"freeing {human_bytes(plan['bytes'])}?"]
        if plan["no_copy"]:
            # The one thing that must not be glossed: these exist nowhere else.
            lines.append(f"\n⚠ {len(plan['no_copy'])} of them were thrown away "
                         f"during culling and exist nowhere else. Deleting them "
                         f"is final.")
        if plan["unverified"]:
            lines.append(f"\n{len(plan['unverified'])} file(s) will be KEPT — no "
                         f"verified copy was found for them.")
        if plan["unknown"]:
            lines.append(f"\n{len(plan['unknown'])} file(s) are not ours and will "
                         f"be left alone.")
        lines.append("\nThe card keeps its own record, so it still knows which "
                     "card it is next time.")
        if QMessageBox.question(
                self, "Empty a card", "\n".join(lines),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        res = ce.delete_from_card(self._card, plan,
                                  expect_card_id=plan.get("card_id", ""))
        if res.get("error"):
            QMessageBox.warning(self, "Empty a card", res["error"])
            self._summary.setText(f"❌ {res['error']}")
            return
        self._summary.setText(
            f"🧹 Deleted {res['deleted']} file(s), {human_bytes(res['bytes'])} "
            f"freed. The card is ready to shoot on again."
            + (f" {res['failed']} could not be deleted." if res.get("failed") else ""))
        self._empty_btn.setEnabled(False)
        self._copy_btn.setEnabled(False)
        self._table.setRowCount(0)
        self._identity.setText("")

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

    def _stop_thread(self, timeout_ms=5000):
        """Quit and WAIT. The wait is the crux: without it Qt tears down a
        QThread whose run() is still going and the process dies with "QThread:
        Destroyed while thread is still running" — the same trap the card
        reader's Cancel had to solve. Bounded, so a wedged worker cannot hang
        the close."""
        if self._thread is None:
            return
        worker = getattr(self, "_worker", None)
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()
        self._thread.quit()
        self._thread.wait(timeout_ms)
        self._thread = None

    def closeEvent(self, event):
        self._stop_thread()
        super().closeEvent(event)

    def reject(self):
        self._stop_thread()
        super().reject()

    def accept(self):
        self._stop_thread()
        super().accept()
