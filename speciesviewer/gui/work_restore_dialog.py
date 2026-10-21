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
from gui.widgets import wrapped


class RestoreWorkDialog(QDialog):
    def __init__(self, parent, trip_root, picture_root):
        super().__init__(parent)
        self.setWindowTitle("Restore work from a card")
        self._trip_root = str(trip_root or "")
        self._picture_root = Path(picture_root) if picture_root else None
        self._trip = None          # the card entry for this trip
        self._plans = {}           # day name -> plan
        self._ok = False           # identity verified
        self.resize(1180, 600)

        # The short-eared owl on the left (his Firefly set, 2026-10-21), as in
        # Create Excel — the jay of the backups has its partner here.
        from gui.wait_picture import picture_column
        outer = picture_column(self, "short_eared_owl")
        outer.addWidget(wrapped(
            "Puts back everything except the pictures themselves: the culling "
            "colours, species names, locations, crops, blog text <b>and the "
            "rendered JPGs</b>, from the <b>SV INDEX</b> card.<br>"
            "The RAW pictures come from the trip's SD-cards — read those before "
            "or after this, in any order. Nothing here writes or deletes a picture."))

        row = QHBoxLayout()
        row.addWidget(QLabel("SV INDEX card / folder:"))
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

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["", "Day", "In backup", "Already here", "Pictures", "To restore"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        for i in (0, 1, 2, 3, 4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
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
        self._read_btn = QPushButton("Read SD-card or Folder…")
        self._read_btn.setToolTip(
            "Bring the RAW pictures back from the trip's SD-cards, without "
            "leaving this screen.\nThe SV INDEX card can be out — which pictures "
            "survived the culling is already remembered.")
        self._read_btn.clicked.connect(self._read_cards)
        btns.addWidget(self._read_btn)
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
        got = QFileDialog.getExistingDirectory(self, "SV INDEX card or folder", start)
        if got:
            self._src_label.setText(got)
            self._reload()

    def _source(self):
        t = self._src_label.text().strip()
        return "" if t.startswith("—") else t

    # ── load + identity ───────────────────────────────────────────────────

    def _abandon(self, message):
        """Leave the screen saying one thing. The table is already empty here,
        and the summary must be emptied WITH it: it used to keep the previous
        card's counts, so a dialog showing no days still claimed "1 of 3 day(s)
        selected, 49 already identical"."""
        self._say(message)
        self._enable(False)
        self._update_summary()

    def _reload(self):
        self._table.setRowCount(0)
        self._plans = {}
        self._trip = None
        self._ok = False
        src = self._source()
        if not src or not Path(src).is_dir():
            self._abandon(f"❌ {src or 'No folder'} is not there. If a drive letter "
                          f"moved — inserting an SD-card can take the one the "
                          f"SV INDEX card had — press Choose and point at it again."
                          if src else "Choose the SV INDEX card, or the folder holding the backups.")
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
            # The likeliest cause by far, and the one that actually happened:
            # inserting an SD-card takes the drive letter the SV INDEX card had,
            # so the same path now points at the wrong volume.
            hint = ("  If you have just inserted an SD-card it may have taken "
                    "this drive letter — press Choose and pick the SV INDEX card "
                    "again." if names == "nothing" else "")
            self._abandon(f"❌ No backup for this trip in {src}. "
                          f"It holds: {names}.{hint}")
            return
        self._trip = mine[0]

        ok, why, info = wr.verify_trip(self._trip["identity"], self._trip_root)
        if not ok:
            # The one refusal on this screen. Everything stays disabled.
            self._abandon(f"❌ This card does not belong to the open trip — {why}. "
                          f"Nothing will be restored. Open the matching trip first.")
            return
        self._ok = True
        # Remember it, the same way the backup dialog does. Without this the
        # Read dialog could not find the card afterwards, so its "skip pictures
        # deleted during the trip" tick never appeared — restoring on a fresh
        # machine and then reading the cards silently un-culled the trip.
        # Only after the identity matched, so pointing at the wrong folder to
        # look cannot clobber the setting.
        try:
            from gui.work_backup_dialog import backup_location, set_backup_location
            if backup_location() != src:
                set_backup_location(src)
        except Exception:
            pass
        self._say(f"✅ Card matches the open trip — “{info.get('trip_name') or ''}” "
                  f"({info.get('trip_id')}), {len(self._trip['days'])} day(s) "
                  f"backed up{self._made_when(info)}.", warn=False)
        self._enable(True)
        self._fill()
        # Remember the surviving pictures NOW, while the card is readable. With
        # one card reader the SD-card read that follows will have taken this
        # card's slot, and without this the filter would have nothing to read.
        try:
            per_day = {d["day"]: (d["manifest"] or {}).get("raws") or []
                       for d in self._trip["days"]}
            wr.save_survivor_cache(self._trip_root,
                                   {k: v for k, v in per_day.items() if v})
        except Exception:
            pass

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
            self._table.setItem(i, 4, self._pictures_item(d["day"]))
            if todo:
                bits = []
                if c.get(wr.NEW):
                    bits.append(f"{c[wr.NEW]} new")
                if c.get(wr.DIFFERS):
                    bits.append(f"{c[wr.DIFFERS]} differ — you will be asked")
                txt = ", ".join(bits)
            else:
                txt = "nothing to do — everything here already matches"
            txt += self._cull_note(plan.get("manifest") or {})
            self._table.setItem(i, 5, QTableWidgetItem(txt))
        self._table.blockSignals(False)
        self._update_summary()

    def _pictures_item(self, day) -> QTableWidgetItem:
        """"21 of 21" — pictures on disk against what the backup says survived.

        The number that actually answers "is this day back?" while cards go in
        one after another. Read from the DAY FOLDER and the manifest already in
        memory, never from the SV INDEX card — by the time the SD-cards are
        being read that card is out of the machine.
        """
        man = (self._plans.get(day) or {}).get("manifest") or {}
        want = len(man.get("raws") or [])
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
            item.setToolTip(
                f"{here} picture(s) in the day folder; the backup lists {want}. "
                f"Read the trip's SD-cards to bring the rest back."
                if here < want else
                f"All {want} picture(s) the backup lists are here.")
        return item

    def _refresh_pictures(self):
        """Update ONLY the picture counts. Deliberately does not touch the card:
        after a card read the SV INDEX card is usually gone, and a full reload
        would blank the screen it is standing in."""
        self._table.blockSignals(True)
        for i in range(self._table.rowCount()):
            day = self._table.item(i, 1).text()
            self._table.setItem(i, 4, self._pictures_item(day))
        self._table.blockSignals(False)

    def _read_cards(self):
        """Open Read SD-cards from here, then update the picture counts.

        Cards go in one after another, and having to close this screen and find
        another menu between each one is the goat trail. The SV INDEX card is
        almost certainly out of the machine by now, which is exactly why the
        refresh afterwards reads the day folders rather than the card."""
        try:
            from gui.trip_process import ReadSDCardDialog
        except Exception as e:
            QMessageBox.warning(self, "Read SD-card or Folder", f"Could not open it:\n{e}")
            return
        parent = self.parent()
        spec = getattr(parent, "menu_spec", {}) or {}
        holder = None
        for group in spec.values():
            if isinstance(group, dict) and "Read SD-card or Folder…" in group:
                holder = group["Read SD-card or Folder…"]
                break
        ReadSDCardDialog(parent if parent is not None else self, holder).exec()
        self._refresh_pictures()

    @staticmethod
    def _cull_note(manifest) -> str:
        """", 181 culled (of 202 picture(s))" — what this day lost to culling.

        The manifest's picture list shrinks as pictures are thrown away and
        raws_initial is the high-water mark, so the difference IS the cull. It
        belongs on this screen because it says what the SD-card read that
        follows will leave behind — otherwise the number is invisible until the
        pictures simply fail to appear.
        """
        raws = manifest.get("raws") or []
        initial = int(manifest.get("raws_initial") or 0)
        culled = initial - len(raws)
        if culled > 0:
            return (f"  ·  covers {len(raws)} kept picture(s); {culled} of "
                    f"{initial} were culled")
        if raws:
            return f"  ·  covers {len(raws)} picture(s)"
        return ""

    def _cull_totals(self, rows):
        """(pictures the backup lists, pictures culled) over the given rows."""
        kept = culled = 0
        for i in rows:
            man = self._plans.get(self._table.item(i, 1).text(), {}).get("manifest") or {}
            raws = man.get("raws") or []
            kept += len(raws)
            culled += max(0, int(man.get("raws_initial") or 0) - len(raws))
        return kept, culled

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
        kept, culled = self._cull_totals(picked)
        tail = (f"  The backup lists {kept} picture(s); {culled} were culled and "
                f"will not be read back." if culled else
                (f"  The backup lists {kept} picture(s)." if kept else ""))
        self._summary.setText(
            f"{len(picked)} of {self._table.rowCount()} day(s) selected — "
            f"{new} file(s) to write, {diff} that differ, {same} already identical "
            f"(those are skipped, not rewritten).{tail}")

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
            # The identity ZIP has been verified but never unpacked. Merge it
            # now, with the days: the species list is the half of the trip that
            # NOTHING can rediscover — a vernacular name typed on the trip has
            # no way back once the collection holding it is gone.
            ident = wr.restore_trip_identity(self._trip["identity"], self._trip_root)
            tracks = wr.restore_tracklogs(self._trip["path"], _gpslogs_root())
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Restore work from a card",
                                f"The restore failed:\n{e}")
            self._reload()
            return
        finally:
            QApplication.restoreOverrideCursor()

        kept, culled = self._cull_totals(picked)
        self._reload()
        cull_line = (
            f"The metadata and JPGs are back for the {kept} picture(s) that "
            f"survived the culling"
            + (f"; {culled} culled picture(s) are deliberately not covered."
               if culled else ".")
            + f"\n\nNext: read the trip's SD-cards to bring back the RAW "
              f"pictures themselves. Which pictures were kept is now remembered "
              f"on this machine, so you can take the SV INDEX card out — leave "
              f"“Skip pictures deleted during the trip” ticked and the culled "
              f"ones will not return.")
        QMessageBox.information(
            self, "Restore work from a card",
            f"{totals['days']} day(s): {totals['written']} file(s) written, "
            f"{totals['identical']} already identical (left untouched), "
            f"{totals['kept']} kept as they were."
            f"\n\n{_identity_line(ident)}\n{_tracklog_line(tracks)}"
            f"\n\n{cull_line}")

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


def _gpslogs_root():
    """Where this machine keeps its GPS tracklogs, or "" when unset. Read here
    rather than passed in: the restore dialog is opened with a trip, and the
    log folder is machine-wide state that has nothing to do with which trip."""
    try:
        from utils.config import get_gpslogs_root
        return get_gpslogs_root() or ""
    except Exception:
        return ""


def _identity_line(ident):
    """One sentence on what came back out of the trip ZIP."""
    if not ident:
        return "Trip identity: nothing to merge."
    c = ident.get("collection")
    bits = []
    if c:
        bits.append(f"species list merged to {c['total']} species "
                    f"({c['kept_local']} kept from this machine"
                    + (f", {c['overridden']} taken from the card"
                       if c["overridden"] else "") + ")")
    if ident.get("baseline") == "restored":
        bits.append("lifer baseline restored")
    t = ident.get("transcache")
    if t and (t["added"] or t["updated"]):
        bits.append(f"{t['added'] + t['updated']} translation(s) merged")
    line = "Trip identity: " + ("; ".join(bits) if bits
                                else "nothing new to merge")
    if ident.get("errors"):
        line += f" — ⚠️ {'; '.join(ident['errors'])}"
    return line + "."


def _tracklog_line(tracks):
    if not tracks:
        return "GPS tracklogs: nothing to restore."
    if tracks.get("copied") or tracks.get("skipped"):
        return (f"GPS tracklogs: {tracks['copied']} copied back, "
                f"{tracks['skipped']} already there.")
    return f"GPS tracklogs: ⚠️ {tracks.get('reason', 'none found')}."


def plan_zip(trip_entry, day):
    """The ZIP for one day of this card entry."""
    return next(d["zip"] for d in trip_entry["days"] if d["day"] == day)
