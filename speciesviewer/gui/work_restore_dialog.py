"""Import Trip, step 2: read the metadata back from the SV INDEX card.

ONE job: show, per day, what reading the card's backup would do, then do it for
the ticked days. All the thinking is in `engine.work_restore`; this step shows
the plan and reports.

Invariants:
- The identity check runs first and, when it fails, nothing here can write.
  Restoring one trip's work into another mixes two sets of culling decisions in
  one folder, and that cannot be undone by hand.
- Newer wins, and only the CARD's newer copies are ever written over a file
  here (the older copy is kept in the trip's .trash/replaced/). A copy that is
  newer here is never overwritten: it is only counted. So there is no question
  per file; the tick on a day is the confirmation.
- A picture deleted on this computer is not brought back: its sidecars and
  JPGs would be orphans.
"""

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from engine import work_backup as wb
from engine import work_restore as wr
from gui import theme as _th
from utils.config import _config
from gui.widgets import wrapped

TITLE = "Read metadata from SV INDEX card"

# Table columns.
C_TICK, C_DAY, C_NEW, C_CARD, C_HERE, C_SAME, C_PICS, C_WHAT = range(8)


def _int_item(n) -> QTableWidgetItem:
    item = QTableWidgetItem(str(n))
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


class MetadataStep(QWidget):
    """Step 2 of the Import Trip window. `changed` fires whenever its status
    line (for the step header) may have changed."""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._trip_root = ""
        self._picture_root = None
        self._trip = None          # the card entry for this trip
        self._plans = {}           # day name -> plan
        self._ok = False           # identity verified
        self._card_missing = False
        self._last_read = ""       # "8 days read 13 Nov 10:20"

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(wrapped(
            "Culling colours, species names, locations, crops, blog text and the "
            "rendered JPGs. Pictures are never written or deleted here."))

        row = QHBoxLayout()
        row.addWidget(QLabel("SV INDEX card:"))
        self._src_label = QLabel("— not chosen —")
        row.addWidget(self._src_label)
        self._banner = QLabel("")
        self._banner.setWordWrap(True)
        row.addWidget(self._banner, 1)
        b = QPushButton("Choose…")
        b.clicked.connect(self._choose)
        row.addWidget(b)
        col.addLayout(row)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["", "Day", "New", "Newer on the card", "Newer here", "Same",
             "Pictures here", "What happens"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        for i in range(C_WHAT):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_WHAT, QHeaderView.ResizeMode.Stretch)
        for i, tip in ((C_NEW, "Not on this computer yet — added."),
                       (C_CARD, "Differs, and the card's copy is later — taken; the "
                                "copy here is kept in the trip's .trash/replaced folder."),
                       (C_HERE, "Differs, and the copy here is later — never "
                                "overwritten."),
                       (C_SAME, "Already identical — skipped.")):
            self._table.horizontalHeaderItem(i).setToolTip(tip)
        self._table.itemChanged.connect(lambda *_: self._update_summary())
        self._table.doubleClicked.connect(lambda *_: self._details())
        col.addWidget(self._table, 1)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        col.addWidget(self._summary)

        btns = QHBoxLayout()
        a = QPushButton("Select all")
        a.clicked.connect(lambda: self._set_all(True))
        btns.addWidget(a)
        n = QPushButton("Select none")
        n.clicked.connect(lambda: self._set_all(False))
        btns.addWidget(n)
        btns.addStretch(1)
        self._details_btn = QPushButton("Details of a day…")
        self._details_btn.setToolTip("The files behind the numbers of the selected "
                                     "day — read-only.")
        self._details_btn.clicked.connect(self._details)
        btns.addWidget(self._details_btn)
        self._restore_btn = QPushButton("Read ticked days")
        self._restore_btn.setDefault(True)
        self._restore_btn.clicked.connect(self._restore)
        btns.addWidget(self._restore_btn)
        self.next_btn = QPushButton("Next: pictures ›")
        btns.addWidget(self.next_btn)
        col.addLayout(btns)

    # ── the trip this step works for ──────────────────────────────────────

    def set_trip(self, trip_root, picture_root):
        self._trip_root = str(trip_root or "")
        self._picture_root = Path(picture_root) if picture_root else None
        if not self._source():
            loc = wb.card_root(_config.get("backup", "location", fallback="").strip())
            self._src_label.setText(loc or "— not chosen —")
        self.reload()

    def status(self) -> tuple[str, str]:
        """(text, kind) for the step header; kind is 'done' | 'todo' | 'faint'."""
        if not self._trip_root:
            return "open or import the trip first", "faint"
        if self._card_missing:
            return "the SV INDEX card is not inserted", "todo"
        if not self._ok:
            return "no backup of this trip found", "faint"
        days = self._trip["days"]
        todo = sum(1 for d in days if self._todo(self._plans.get(d["day"]) or {}))
        if not todo:
            return (f"✅ {self._last_read}" if self._last_read
                    else f"✅ all {len(days)} day(s) already here"), "done"
        return f"{len(days)} day(s) on the card, {todo} to read", "todo"

    # ── source ────────────────────────────────────────────────────────────

    def _choose(self):
        from gui.backup_folder import choose_card_folder
        got = choose_card_folder(self, "SV INDEX card", self._source())
        if got:
            self._src_label.setText(got)
            self.reload()

    def _source(self):
        t = self._src_label.text().strip()
        return "" if t.startswith("—") else t

    # ── load + identity ───────────────────────────────────────────────────

    def _say(self, text, warn=True):
        self._banner.setText(text)
        self._banner.setStyleSheet(
            f"color: {_th.WARN if warn else _th.INK_SOFT}; font-size: 9pt;")

    def _abandon(self, message, keep_rows=False):
        """Say one thing, and write nothing. With `keep_rows` (the card was
        taken out) the last counts stay visible, greyed, instead of vanishing."""
        self._say(message)
        self._ok = False
        self._restore_btn.setEnabled(False)
        self._details_btn.setEnabled(False)
        if keep_rows and self._table.rowCount():
            self._table.setEnabled(False)
        else:
            self._table.setRowCount(0)
            self._plans = {}
            self._trip = None
        self._update_summary()
        self.changed.emit()

    def reload(self):
        self._card_missing = False
        self._table.setEnabled(True)
        if not self._trip_root:
            self._abandon("Open or import the trip first (step 1).")
            return
        src = self._source()
        if not src or not Path(src).is_dir():
            self._card_missing = bool(src)
            self._abandon(
                f"❌ The SV INDEX card is not inserted ({src} is not there). Insert "
                f"it, or press Choose if it came back under another drive letter."
                if src else "Choose the SV INDEX card.", keep_rows=bool(src))
            return

        want = self._picture_root.name if self._picture_root else ""
        trips = wr.find_backups(src)["trips"]
        # The folder named after this trip's pictures first, else the only trip
        # on the card. The name is a convenience; the identity check decides.
        mine = [t for t in trips if want and t["folder"] == want] or (
            trips if len(trips) == 1 else [])
        if not mine:
            names = ", ".join(t["folder"] or "(root)" for t in trips) or "nothing"
            self._abandon(f"❌ No backup of this trip on {src}. It holds: {names}.")
            return
        self._trip = mine[0]

        ok, why, info = wr.verify_trip(self._trip["identity"], self._trip_root)
        if not ok:
            self._abandon(f"❌ This card does not belong to the open trip — {why}. "
                          f"Nothing will be read.")
            return
        self._ok = True
        # Remembered (as the card root) so the backup and the card read find it.
        try:
            from gui.work_backup_dialog import backup_location, set_backup_location
            root = wb.card_root(src)
            if backup_location() != root:
                set_backup_location(root)
        except Exception:
            pass
        made = (info.get("created") or "").strip()
        machine = (info.get("machine") or "").strip()
        self._say(f"✅ matches this trip · backup made {made}"
                  + (f" on {machine}" if machine else ""), warn=False)
        self._restore_btn.setEnabled(True)
        self._details_btn.setEnabled(True)
        self._fill()
        # Remember the surviving pictures and the name prefix NOW, while the
        # card is readable: the card read in step 3 needs both.
        try:
            per_day = {d["day"]: (d["manifest"] or {}).get("raws") or []
                       for d in self._trip["days"]}
            wr.save_survivor_cache(self._trip_root,
                                   {k: v for k, v in per_day.items() if v},
                                   prefix=self._trip.get("prefix", ""))
        except Exception:
            pass
        self.changed.emit()

    @staticmethod
    def _todo(plan) -> int:
        c = plan.get("counts", {})
        return c.get(wr.NEW, 0) + c.get(wr.CARD_NEWER, 0)

    def _fill(self):
        days = self._trip["days"]
        on_card = {d["day"] for d in days}
        here_only = []
        if self._picture_root and self._picture_root.is_dir():
            try:
                here_only = sorted(p.name for p in self._picture_root.iterdir()
                                   if p.is_dir() and wb._day_folder_name(p.name)
                                   and p.name not in on_card)
            except OSError:
                pass
        rows = [("card", d) for d in days] + [("here", n) for n in here_only]
        rows.sort(key=lambda r: r[1]["day"] if r[0] == "card" else r[1])

        self._plans = {}
        self._table.blockSignals(True)
        self._table.setRowCount(len(rows))
        faint = QColor(_th.INK_FAINT)
        for i, (kind, d) in enumerate(rows):
            if kind == "here":
                tick = QTableWidgetItem()
                tick.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self._table.setItem(i, C_TICK, tick)
                self._table.setItem(i, C_DAY, QTableWidgetItem(d))
                for c in (C_NEW, C_CARD, C_HERE, C_SAME):
                    self._table.setItem(i, c, QTableWidgetItem(""))
                self._table.setItem(i, C_PICS, self._pictures_item(d, {}))
                self._table.setItem(i, C_WHAT, QTableWidgetItem(
                    "not on the card — read from a card or dump only (step 3)"))
                for c in range(8):
                    self._table.item(i, c).setForeground(faint)
                continue
            day = d["day"]
            target = (self._picture_root / day) if self._picture_root else None
            plan = wr.plan_day(d["zip"], target) if target else {"counts": {}, "files": []}
            self._plans[day] = plan
            c = plan.get("counts", {})
            todo = self._todo(plan)
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Checked if todo else Qt.CheckState.Unchecked)
            self._table.setItem(i, C_TICK, tick)
            self._table.setItem(i, C_DAY, QTableWidgetItem(day))
            self._table.setItem(i, C_NEW, _int_item(c.get(wr.NEW, 0)))
            self._table.setItem(i, C_CARD, _int_item(c.get(wr.CARD_NEWER, 0)))
            self._table.setItem(i, C_HERE, _int_item(c.get(wr.HERE_NEWER, 0)))
            self._table.setItem(i, C_SAME, _int_item(c.get(wr.SAME, 0)))
            self._table.setItem(i, C_PICS, self._pictures_item(day, plan.get("manifest") or {}))
            what = QTableWidgetItem(self._what_text(plan))
            self._table.setItem(i, C_WHAT, what)
            if not todo:
                for col in range(1, 8):
                    self._table.item(i, col).setForeground(faint)
        self._table.blockSignals(False)
        self._update_summary()

    @staticmethod
    def _what_text(plan) -> str:
        if plan.get("error"):
            return f"❌ cannot read this day's backup: {plan['error']}"
        c = plan.get("counts", {})
        new, card, here = c.get(wr.NEW, 0), c.get(wr.CARD_NEWER, 0), c.get(wr.HERE_NEWER, 0)
        gone = c.get(wr.DELETED_HERE, 0)
        bits = []
        if new or card:
            bits.append(f"{new + card} file(s) from the card"
                        + (f" ({card} replacing an older copy)" if card else ""))
        if here:
            bits.append(f"{here} newer here are kept")
        if gone:
            bits.append(f"{gone} file(s) of pictures deleted here are left out")
        if not bits:
            return "nothing to do — the same as here"
        if not (new or card):
            bits.insert(0, "nothing to read")
        return " · ".join(bits)

    def _pictures_item(self, day, manifest) -> QTableWidgetItem:
        """"21 of 21" — pictures in the day folder against what the backup says
        survived the culling. Read from the day folder, never from the card."""
        want = len((manifest or {}).get("raws") or [])
        here = 0
        folder = (self._picture_root / day) if self._picture_root else None
        if folder and folder.is_dir():
            try:
                here = sum(1 for p in folder.iterdir()
                           if p.is_file() and p.suffix.lower() in wb.SOURCE_EXTS)
            except OSError:
                here = 0
        item = QTableWidgetItem(f"{here} of {want}" if want else str(here))
        if want:
            item.setToolTip(f"{here} picture(s) in the day folder; the backup lists "
                            f"{want}." + (" Read the cards in step 3 for the rest."
                                          if here < want else ""))
        return item

    # ── selection ─────────────────────────────────────────────────────────

    def _checked_days(self):
        out = []
        for i in range(self._table.rowCount()):
            item = self._table.item(i, C_TICK)
            day = self._table.item(i, C_DAY).text()
            if item and item.checkState() == Qt.CheckState.Checked and day in self._plans:
                out.append(day)
        return out

    def _set_all(self, on):
        st = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self._table.blockSignals(True)
        for i in range(self._table.rowCount()):
            if self._table.item(i, C_DAY).text() in self._plans:
                self._table.item(i, C_TICK).setCheckState(st)
        self._table.blockSignals(False)
        self._update_summary()

    def _update_summary(self):
        days = self._checked_days()
        if not self._plans:
            self._summary.setText("")
            return
        tot = {k: 0 for k in (wr.NEW, wr.CARD_NEWER, wr.HERE_NEWER, wr.SAME, wr.DELETED_HERE)}
        for d in days:
            for k in tot:
                tot[k] += self._plans[d].get("counts", {}).get(k, 0)
        self._summary.setText(
            f"{len(days)} of {len(self._plans)} day(s) ticked — "
            f"{tot[wr.NEW] + tot[wr.CARD_NEWER]} file(s) from the card"
            + (f" ({tot[wr.CARD_NEWER]} replacing an older copy)" if tot[wr.CARD_NEWER] else "")
            + f" · {tot[wr.HERE_NEWER]} newer here are kept · {tot[wr.SAME]} the same, skipped"
            + (f" · {tot[wr.DELETED_HERE]} file(s) of pictures deleted here are left out"
               if tot[wr.DELETED_HERE] else "") + ".")
        self._restore_btn.setEnabled(self._ok and bool(days))

    # ── actions ───────────────────────────────────────────────────────────

    def _details(self):
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        day = self._table.item(rows[0].row(), C_DAY).text() if rows else ""
        plan = self._plans.get(day)
        if not plan:
            QMessageBox.information(self, TITLE, "Select a day from the card first.")
            return
        DayDetailsDialog(self, day, plan).exec()

    def _restore(self):
        if not self._ok:
            return
        days = self._checked_days()
        if not days:
            return
        tot = {"written": 0, "replaced": 0, "identical": 0, "kept": 0,
               "deleted_here": 0, "days": 0}
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for day in days:
                res = wr.restore_day(_plan_zip(self._trip, day),
                                     self._picture_root / day, take_newer=True)
                for k in ("written", "replaced", "identical", "kept", "deleted_here"):
                    tot[k] += res[k]
                tot["days"] += 1
            # The species list is the half of a trip nothing can rediscover — a
            # vernacular name typed on the trip has no way back once lost.
            ident = wr.restore_trip_identity(self._trip["identity"], self._trip_root)
            tracks = wr.restore_tracklogs(self._trip["path"], _gpslogs_root())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, TITLE, f"Reading the card failed:\n{e}")
            self.reload()
            return
        finally:
            QApplication.restoreOverrideCursor()

        from datetime import datetime
        self._last_read = (f"{tot['days']} day(s) read "
                           f"{datetime.now().strftime('%d %b %H:%M')}")
        self.reload()
        lines = [f"{tot['days']} day(s): {tot['written']} file(s) written"
                 + (f" ({tot['replaced']} replacing an older copy, which is kept in "
                    f"the trip's .trash/replaced folder)" if tot["replaced"] else "")
                 + f", {tot['identical']} already identical, {tot['kept']} newer here "
                   f"and kept"
                 + (f", {tot['deleted_here']} file(s) of pictures deleted here left out"
                    if tot["deleted_here"] else "") + ".",
                 "", _identity_line(ident), _tracklog_line(tracks), "",
                 "Next: the pictures themselves, from the SD-cards and dumps (step 3). "
                 "Which pictures were kept is now remembered on this computer, so the "
                 "culled ones will not come back."]
        QMessageBox.information(self, TITLE, "\n".join(lines))


class DayDetailsDialog(QDialog):
    """The files behind one day's numbers — read-only, so you can look before
    you tick, instead of answering a question per file."""

    _ORDER = {wr.CARD_NEWER: 0, wr.NEW: 1, wr.HERE_NEWER: 2, wr.DELETED_HERE: 3, wr.SAME: 4}
    _TEXT = {wr.CARD_NEWER: "newer on the card — taken", wr.NEW: "new — added",
             wr.HERE_NEWER: "newer here — kept", wr.DELETED_HERE: "picture deleted here — left out",
             wr.SAME: "the same — skipped"}

    def __init__(self, parent, day, plan):
        super().__init__(parent)
        self.setWindowTitle(f"{day} — what reading the card would do")
        self.resize(900, 560)
        col = QVBoxLayout(self)
        files = sorted(plan.get("files", []),
                       key=lambda r: (self._ORDER.get(r["state"], 9), r["name"].lower()))
        t = QTableWidget(len(files), 4)
        t.setHorizontalHeaderLabels(["File", "What happens", "On the card", "Here"])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        fmt = "%d %b %Y %H:%M:%S"
        for i, r in enumerate(files):
            t.setItem(i, 0, QTableWidgetItem(r["name"]))
            t.setItem(i, 1, QTableWidgetItem(self._TEXT.get(r["state"], r["state"])))
            t.setItem(i, 2, QTableWidgetItem(r["zip_when"].strftime(fmt) if r.get("zip_when") else ""))
            t.setItem(i, 3, QTableWidgetItem(r["disk_when"].strftime(fmt) if r.get("disk_when") else ""))
        col.addWidget(t, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        col.addLayout(row)


def _gpslogs_root():
    """Where this computer keeps its GPS tracklogs, or "" when unset."""
    try:
        from utils.config import get_gpslogs_root
        return get_gpslogs_root() or ""
    except Exception:
        return ""


def _identity_line(ident):
    """One sentence on what came back out of the trip ZIP."""
    if not ident:
        return "Trip: nothing to merge."
    c = ident.get("collection")
    bits = []
    if c:
        bits.append(f"species list merged to {c['total']} species "
                    f"({c['kept_local']} kept from this computer"
                    + (f", {c['overridden']} taken from the card"
                       if c["overridden"] else "") + ")")
    if ident.get("baseline") == "restored":
        bits.append("lifer status restored")
    t = ident.get("transcache")
    if t and (t["added"] or t["updated"]):
        bits.append(f"{t['added'] + t['updated']} translation(s) merged")
    line = "Trip: " + ("; ".join(bits) if bits else "nothing new to merge")
    if ident.get("errors"):
        line += f" — ⚠️ {'; '.join(ident['errors'])}"
    return line + "."


def _tracklog_line(tracks):
    if not tracks:
        return "GPS tracklogs: nothing to read."
    if tracks.get("copied") or tracks.get("skipped"):
        return (f"GPS tracklogs: {tracks['copied']} copied, "
                f"{tracks['skipped']} already here.")
    return f"GPS tracklogs: ⚠️ {tracks.get('reason', 'none found')}."


def _plan_zip(trip_entry, day):
    """The ZIP for one day of this card entry."""
    return next(d["zip"] for d in trip_entry["days"] if d["day"] == day)
