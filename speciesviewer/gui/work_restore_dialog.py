"""Put a trip's work back from a spare card.

ONE job: show what a restore would do, then do the agreed part. All the thinking
is in `engine.work_restore`; this screen asks the questions and reports.

Invariant: the identity check runs before anything else and, when it fails,
nothing on this screen can write. Restoring one trip's work into another mixes
two sets of culling decisions into one folder and cannot be undone by hand, so
it is refused rather than warned about.

Identical files are never queried. A file whose size and CRC already match the
archived one has no decision in it — overwriting it changes nothing — so only
genuine differences raise the Windows-style prompt, and those are usually none.
On a card restored onto the folder it came from, the expected outcome is a
single line saying everything already matches.
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from engine import work_backup as wb
from engine import work_restore as wr
from gui import theme as _th
from utils.config import _config
from utils.disk_space import human_bytes


class RestoreWorkDialog(QDialog):
    def __init__(self, parent, trip_root, picture_root):
        super().__init__(parent)
        self.setWindowTitle("Restore work from a card")
        self._trip_root = str(trip_root or "")
        self._picture_root = Path(picture_root) if picture_root else None
        self._trip = None          # the card entry for this trip
        self._plans = {}           # day name -> plan
        self._ok = False           # identity verified
        self.resize(940, 560)

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "Puts back the culling, species, locations, crops and blog text a "
            "backup card holds. Pictures are never written or deleted — read the "
            "SD-cards for those, before or after, in any order."))

        row = QHBoxLayout()
        row.addWidget(QLabel("Backup card / folder:"))
        self._src_label = QLabel(self._default_source() or "— not chosen —")
        self._src_label.setWordWrap(True)
        row.addWidget(self._src_label, 1)
        b = QPushButton("Choose…")
        b.clicked.connect(self._choose)
        row.addWidget(b)
        outer.addLayout(row)

        self._banner = QLabel("")
        self._banner.setWordWrap(True)
        outer.addWidget(self._banner)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["", "Day", "In backup", "Already here", "To restore"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        for i in (0, 1, 2, 3):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.itemChanged.connect(lambda *_: self._update_summary())
        outer.addWidget(self._table, 1)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        outer.addWidget(self._summary)

        btns = QHBoxLayout()
        a = QPushButton("Select all")
        a.clicked.connect(lambda: self._set_all(True))
        btns.addWidget(a)
        n = QPushButton("Select none")
        n.clicked.connect(lambda: self._set_all(False))
        btns.addWidget(n)
        btns.addStretch(1)
        self._check_btn = QPushButton("Check pictures…")
        self._check_btn.setToolTip(
            "Compare the pictures on disk against what the backup recorded — "
            "read-only. Meaningful once the SD-cards have been read.")
        self._check_btn.clicked.connect(self._check_pictures)
        btns.addWidget(self._check_btn)
        self._restore_btn = QPushButton("Restore selected")
        self._restore_btn.clicked.connect(self._restore)
        btns.addWidget(self._restore_btn)
        c = QPushButton("Close")
        c.clicked.connect(self.accept)
        btns.addWidget(c)
        outer.addLayout(btns)

        self._reload()

    # ── source ────────────────────────────────────────────────────────────

    def _default_source(self):
        return _config.get("backup", "location", fallback="").strip()

    def _choose(self):
        start = self._default_source() or str(Path.home())
        got = QFileDialog.getExistingDirectory(self, "Backup card or folder", start)
        if got:
            self._src_label.setText(got)
            self._reload()

    def _source(self):
        t = self._src_label.text().strip()
        return "" if t.startswith("—") else t

    # ── load + identity ───────────────────────────────────────────────────

    def _reload(self):
        self._table.setRowCount(0)
        self._plans = {}
        self._trip = None
        self._ok = False
        src = self._source()
        if not src or not Path(src).is_dir():
            self._say("Choose the folder or card holding the backups.", warn=False)
            self._enable(False)
            return

        want = self._picture_root.name if self._picture_root else ""
        trips = wr.find_backups(src)["trips"]
        # Prefer the folder named after this trip's pictures, but fall back to
        # the only trip on the card. The folder name is a convenience, not the
        # authority — on a rebuilt machine the local picture folder may not
        # exist yet, and the identity ZIP below is what actually decides.
        mine = [t for t in trips if want and t["folder"] == want] or (
            trips if len(trips) == 1 else [])
        if not mine:
            names = ", ".join(t["folder"] or "(root)" for t in trips) or "nothing"
            self._say(f"❌ No single backup to restore from on this card. "
                      f"It holds: {names}.")
            self._enable(False)
            return
        self._trip = mine[0]

        ok, why, info = wr.verify_trip(self._trip["identity"], self._trip_root)
        if not ok:
            # The one refusal on this screen. Everything stays disabled.
            self._say(f"❌ This card does not belong to the open trip — {why}. "
                      f"Nothing will be restored. Open the matching trip first.")
            self._enable(False)
            return
        self._ok = True
        self._say(f"✅ Card matches the open trip — “{info.get('trip_name') or ''}” "
                  f"({info.get('trip_id')}), {len(self._trip['days'])} day(s) "
                  f"backed up{self._made_when(info)}.", warn=False)
        self._enable(True)
        self._fill()

    @staticmethod
    def _made_when(info):
        w = (info.get("created") or "").strip()
        return f", made {w}" if w else ""

    def _say(self, text, warn=True):
        self._banner.setText(text)
        self._banner.setStyleSheet(
            f"color: {_th.WARN if warn else _th.INK_SOFT}; font-size: 9pt;")

    def _enable(self, ok):
        self._restore_btn.setEnabled(ok)
        self._check_btn.setEnabled(ok)

    def _fill(self):
        days = self._trip["days"]
        self._table.blockSignals(True)
        self._table.setRowCount(len(days))
        for i, d in enumerate(days):
            target = (self._picture_root / d["day"]) if self._picture_root else None
            plan = wr.plan_day(d["zip"], target) if target else {"counts": {}, "files": []}
            self._plans[d["day"]] = plan
            c = plan.get("counts", {})
            todo = c.get(wr.NEW, 0) + c.get(wr.DIFFERS, 0)

            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Checked if todo else Qt.CheckState.Unchecked)
            self._table.setItem(i, 0, tick)
            self._table.setItem(i, 1, QTableWidgetItem(d["day"]))
            self._table.setItem(i, 2, QTableWidgetItem(str(len(plan.get("files", [])))))
            self._table.setItem(i, 3, QTableWidgetItem(str(c.get(wr.SAME, 0))))
            if todo:
                bits = []
                if c.get(wr.NEW):
                    bits.append(f"{c[wr.NEW]} new")
                if c.get(wr.DIFFERS):
                    bits.append(f"{c[wr.DIFFERS]} differ — you will be asked")
                txt = ", ".join(bits)
            else:
                txt = "nothing to do — everything here already matches"
            self._table.setItem(i, 4, QTableWidgetItem(txt))
        self._table.blockSignals(False)
        self._update_summary()

    # ── selection ─────────────────────────────────────────────────────────

    def _checked(self):
        return [i for i in range(self._table.rowCount())
                if self._table.item(i, 0).checkState() == Qt.CheckState.Checked]

    def _set_all(self, on):
        st = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self._table.blockSignals(True)
        for i in range(self._table.rowCount()):
            self._table.item(i, 0).setCheckState(st)
        self._table.blockSignals(False)
        self._update_summary()

    def _update_summary(self):
        picked = self._checked()
        new = diff = same = 0
        for i in picked:
            c = self._plans[self._table.item(i, 1).text()].get("counts", {})
            new += c.get(wr.NEW, 0); diff += c.get(wr.DIFFERS, 0); same += c.get(wr.SAME, 0)
        self._summary.setText(
            f"{len(picked)} of {self._table.rowCount()} day(s) selected — "
            f"{new} file(s) to write, {diff} that differ, {same} already identical "
            f"(those are skipped, not rewritten).")

    # ── actions ───────────────────────────────────────────────────────────

    def _ask_overwrite(self, row, day):
        """The Windows-style question, asked ONLY for a file that really differs."""
        box = QMessageBox(self)
        box.setWindowTitle("Restore work from a card")
        box.setIcon(QMessageBox.Icon.Question)
        dz = row["zip_when"].strftime("%d %b %Y %H:%M") if row["zip_when"] else "unknown"
        dd = row["disk_when"].strftime("%d %b %Y %H:%M") if row["disk_when"] else "unknown"
        newer = "newer" if (row["zip_when"] and row["disk_when"]
                            and row["zip_when"] > row["disk_when"]) else "older"
        box.setText(f"Overwrite “{row['name']}” in {day}?")
        box.setInformativeText(
            f"Here now:   {human_bytes(row['disk_bytes'] or 0)}, {dd}\n"
            f"In backup:  {human_bytes(row['zip_bytes'])}, {dz}  ({newer})")
        cb = QCheckBox("Apply to all remaining files")
        box.setCheckBox(cb)
        box.setStandardButtons(QMessageBox.StandardButton.Yes |
                               QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        yes = box.exec() == QMessageBox.StandardButton.Yes
        return yes, cb.isChecked()

    def _restore(self):
        if not self._ok:
            return
        picked = self._checked()
        if not picked:
            return
        apply_all = None          # None = keep asking; True/False = decided
        totals = {"written": 0, "identical": 0, "kept": 0, "days": 0}
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for i in picked:
                day = self._table.item(i, 1).text()
                plan = self._plans[day]
                target = self._picture_root / day
                overwrite = set()
                for row in plan["files"]:
                    if row["state"] != wr.DIFFERS:
                        continue
                    if apply_all is None:
                        QApplication.restoreOverrideCursor()
                        yes, forever = self._ask_overwrite(row, day)
                        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                        if forever:
                            apply_all = yes
                        if yes:
                            overwrite.add(row["name"])
                    elif apply_all:
                        overwrite.add(row["name"])
                res = wr.restore_day(plan_zip(self._trip, day), target,
                                     overwrite=overwrite)
                for k in ("written", "identical", "kept"):
                    totals[k] += res[k]
                totals["days"] += 1
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Restore work from a card",
                                f"The restore failed:\n{e}")
            self._reload()
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._reload()
        QMessageBox.information(
            self, "Restore work from a card",
            f"{totals['days']} day(s): {totals['written']} file(s) written, "
            f"{totals['identical']} already identical (left untouched), "
            f"{totals['kept']} kept as they were.\n\n"
            f"Pictures are untouched — read the SD-cards, then use "
            f"“Check pictures…” to see what the culling had removed.")

    def _check_pictures(self):
        """Read-only comparison of the pictures on disk against the backup."""
        lines, refused = [], []
        for i in range(self._table.rowCount()):
            day = self._table.item(i, 1).text()
            plan = self._plans[day]
            man = plan.get("manifest") or {}
            target = self._picture_root / day
            ok, why = wr.prefix_matches(target, man)
            if not ok:
                refused.append(f"{day}: {why}")
                continue
            rec = wr.reconcile_day(target, man)
            if rec["on_disk"] == 0:
                lines.append(f"{day}: no pictures here yet — read the SD-cards first.")
            elif rec["culled"] or rec["missing"]:
                bit = []
                if rec["culled"]:
                    bit.append(f"{len(rec['culled'])} on disk that the backup does "
                               f"NOT list (culled during the trip)")
                if rec["missing"]:
                    bit.append(f"{len(rec['missing'])} still missing")
                lines.append(f"{day}: {rec['on_disk']} of {rec['recorded']} — " +
                             "; ".join(bit))
            else:
                lines.append(f"{day}: all {rec['recorded']} pictures present, "
                             f"nothing extra.")
        text = "\n".join(refused + lines) or "Nothing to compare."
        box = QMessageBox(self)
        box.setWindowTitle("Check pictures")
        box.setIcon(QMessageBox.Icon.Warning if refused else QMessageBox.Icon.Information)
        box.setText("Pictures on disk compared with the backup's record:")
        box.setDetailedText(text)
        box.setInformativeText(
            ("Some days could not be compared — see the details.\n\n" if refused else "")
            + "Nothing was changed. Removing the culled pictures is not done here.")
        box.exec()


def plan_zip(trip_entry, day):
    """The ZIP for one day of this card entry."""
    return next(d["zip"] for d in trip_entry["days"] if d["day"] == day)
