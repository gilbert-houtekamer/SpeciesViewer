"""Import Trip: bring a trip home, in three numbered steps.

ONE job: lead from "a trip on another computer" to "the trip here, with its
work and its pictures", and show at every moment how far each part is.

  1  Trip      — trip.ini, banner, species list, lifer status, translations
                 (from the trip ZIP on the SV INDEX card, or any trip ZIP)
  2  Metadata  — the days' culling, names, places, crops, blogs and JPGs
                 (the day ZIPs on the SV INDEX card) — gui.work_restore_dialog
  3  Pictures  — the RAWs, from the SD-cards and dumps (the card read, in trip mode)

Steps 2 and 3 work in either order and can be repeated: newer wins in both.
The step header always shows all three statuses, so the order is visible
without being enforced.
"""

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from engine import work_backup as wb
from engine import work_restore as wr
from gui import theme as _th
from utils.config import _config, get_trips_root

STEP_TRIP, STEP_METADATA, STEP_PICTURES = range(3)


def _active_trip():
    """(trip root, picture root) of the open trip, or ("", "")."""
    root = _config.get("trip", "tripbiotadatabase", fallback="").strip()
    if not root or not (Path(root) / "trip.ini").is_file():
        return "", ""
    from utils.trip_identity import resolve_trip_picture_root
    return root, resolve_trip_picture_root(root, kind="trip") or ""


def _count_pictures(folder: Path) -> int:
    try:
        return sum(1 for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in wb.SOURCE_EXTS)
    except OSError:
        return 0


# ── step header ──────────────────────────────────────────────────────────────

class _StepTile(QFrame):
    clicked = pyqtSignal()

    def __init__(self, number, title):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 6, 10, 6)
        col.setSpacing(0)
        n = QLabel(f"STEP {number}")
        n.setStyleSheet(f"color: {_th.INK_FAINT}; font-size: 8pt;")
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600;")
        self._status = QLabel("")
        self._status.setStyleSheet("font-size: 9pt;")
        for w in (n, t, self._status):
            col.addWidget(w)
        self.set_current(False)

    def set_status(self, text, kind):
        colour = {"done": _th.DONE_FG, "todo": _th.WARN}.get(kind, _th.INK_SOFT)
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {colour}; font-size: 9pt;")

    def set_current(self, on):
        border = _th.ACCENT if on else _th.LINE
        self.setStyleSheet(f"_StepTile {{ border: 1px solid {border}; border-radius: 6px; }}")

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


# ── step 1: the trip ─────────────────────────────────────────────────────────

class _EmbeddedImport:
    """Import Trip's own panel, shown inside step 1 instead of as a window."""

    @staticmethod
    def make(main_window):
        from gui.trip_process import ImportTripDialog

        class _Panel(ImportTripDialog):
            # An embedded dialog must never close itself (Escape, Enter).
            def reject(self):
                pass

            def accept(self):
                pass

        panel = _Panel(main_window)
        panel.setWindowFlags(Qt.WindowType.Widget)
        panel.close_btn.hide()
        return panel


class TripStep(QWidget):
    changed = pyqtSignal()

    def __init__(self, main_window):
        super().__init__()
        self._card_trips = []
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        row.addWidget(QLabel("SV INDEX card:"))
        loc = wb.card_root(_config.get("backup", "location", fallback="").strip())
        self._src = QLabel(loc or "— not chosen —")
        row.addWidget(self._src, 1)
        b = QPushButton("Choose…")
        b.clicked.connect(self._choose)
        row.addWidget(b)
        col.addLayout(row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Trip on the card", "Days", "Made", "On this computer"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        for i in (0, 1, 2):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setMaximumHeight(170)
        self._table.itemSelectionChanged.connect(self._picked)
        col.addWidget(self._table)
        self._card_note = QLabel("")
        self._card_note.setStyleSheet(f"color: {_th.INK_SOFT}; font-size: 9pt;")
        col.addWidget(self._card_note)

        self.panel = _EmbeddedImport.make(main_window)
        self.panel.import_btn.clicked.connect(self._after_import)
        col.addWidget(self.panel, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.next_btn = QPushButton("Next: metadata ›")
        btns.addWidget(self.next_btn)
        col.addLayout(btns)
        self.reload()

    def status(self):
        root, pic = _active_trip()
        if root:
            return f"✅ {Path(pic).name if pic else Path(root).name} is open", "done"
        return "not imported yet", "todo"

    def _choose(self):
        from gui.backup_folder import choose_card_folder
        cur = self._src.text()
        got = choose_card_folder(self, "SV INDEX card", "" if cur.startswith("—") else cur)
        if got:
            self._src.setText(got)
            self.reload()

    def reload(self):
        from engine.trip_transfer import inspect_zip
        src = self._src.text()
        self._card_trips = []
        if not src.startswith("—") and Path(src).is_dir():
            for t in wr.find_backups(src)["trips"]:
                if not t["identity"]:
                    continue
                try:
                    info = inspect_zip(str(t["identity"]))
                except Exception:
                    continue
                self._card_trips.append((t, info))
        self._card_trips.sort(key=lambda ti: ti[1].get("created", ""), reverse=True)
        trips_root = get_trips_root()
        active, _ = _active_trip()
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._card_trips))
        pick = 0
        for i, (t, info) in enumerate(self._card_trips):
            folder = info.get("folder", "")
            here = bool(trips_root) and (Path(trips_root) / folder / "trip.ini").is_file()
            is_open = bool(active) and Path(active).name == folder
            if is_open:
                pick = i
            made = info.get("created", "")
            if info.get("machine"):
                made += f" · {info['machine']}"
            self._table.setItem(i, 0, QTableWidgetItem(t["folder"] or folder))
            self._table.setItem(i, 1, QTableWidgetItem(str(len(t["days"]))))
            self._table.setItem(i, 2, QTableWidgetItem(made))
            text = ("yes — open now" if is_open else "yes" if here else "not here yet")
            item = QTableWidgetItem(text)
            if here:
                item.setForeground(QColor(_th.DONE_FG))
            self._table.setItem(i, 3, item)
        self._table.blockSignals(False)
        if self._card_trips:
            self._card_note.setText("Pick the trip to bring home. Importing again is "
                                    "safe: the species list and translations are merged.")
            self._table.selectRow(pick)
            self._picked()
        else:
            self._card_note.setText(
                "No trips found on the SV INDEX card — insert it and press Choose, or "
                "pick a trip ZIP below with Browse." if not src.startswith("—") and
                Path(src).is_dir() else
                "Choose the SV INDEX card, or pick a trip ZIP below with Browse.")

    def _picked(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._card_trips):
            return
        t, _info = self._card_trips[rows[0].row()]
        self.panel.zip_field.setText(str(t["identity"]))

    def _after_import(self):
        self.reload()
        self.changed.emit()


# ── step 3: the pictures ─────────────────────────────────────────────────────

class PicturesStep(QWidget):
    changed = pyqtSignal()

    def __init__(self, main_window):
        super().__init__()
        self._main = main_window
        self._trip_root = ""
        self._picture_root = None
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        intro = QLabel("The RAW pictures, from the trip's SD-cards and dumps. Read "
                       "them in any order; the culled ones stay out.")
        intro.setWordWrap(True)
        col.addWidget(intro)

        # Where the pictures are read from, shown and chosen HERE — the same
        # way step 2 shows its SV INDEX card — so the drive letter is never a
        # guess hidden inside the read window.
        row = QHBoxLayout()
        row.addWidget(QLabel("Read from:"))
        from utils.gui_config import get_cache_gui_section
        saved = (get_cache_gui_section("sdcard_ingest", "source") or "").strip()
        self._src = QLabel(saved or "— not chosen —")
        row.addWidget(self._src)
        self._src_note = QLabel("")
        self._src_note.setWordWrap(True)
        row.addWidget(self._src_note, 1)
        choose = QPushButton("Choose…")
        choose.setToolTip("The SD-card, or a folder the pictures were dumped into.")
        choose.clicked.connect(self._choose)
        row.addWidget(choose)
        col.addLayout(row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Day", "Pictures here", "Kept on the trip", "Read from"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        for i in (0, 1, 2):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeaderItem(2).setToolTip(
            "From the SV INDEX backup: the pictures that survived the culling. "
            "A day the backup does not know has no number, and is copied in full.")
        col.addWidget(self._table, 1)
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(f"color: {_th.INK_SOFT}; font-size: 9pt;")
        col.addWidget(self._summary)

        btns = QHBoxLayout()
        btns.addStretch(1)
        check = QPushButton("Check pictures…")
        check.setToolTip("Compare the pictures here with the backup's record — read-only.")
        check.clicked.connect(self._check)
        btns.addWidget(check)
        self._read_btn = QPushButton("Read SD-card or Folder…")
        self._read_btn.setDefault(True)
        self._read_btn.clicked.connect(self._read)
        btns.addWidget(self._read_btn)
        col.addLayout(btns)
        self._totals = (0, 0)

    def set_trip(self, trip_root, picture_root):
        self._trip_root = str(trip_root or "")
        self._picture_root = Path(picture_root) if picture_root else None
        self.reload()

    def status(self):
        if not self._trip_root:
            return "open or import the trip first", "faint"
        here, kept = self._totals
        if kept:
            return (f"{'✅ ' if here >= kept else ''}{here:,} of {kept:,} pictures here",
                    "done" if here >= kept else "todo")
        return f"{here:,} pictures here", "faint" if not here else "done"

    def _source(self) -> str:
        t = self._src.text().strip()
        return "" if t.startswith("—") else t

    def _choose(self):
        from PyQt6.QtWidgets import QFileDialog
        from utils.start_folder import start_folder
        got = QFileDialog.getExistingDirectory(
            self, "SD-card or folder with the pictures",
            start_folder(self._source(), Path.home()))
        if got:
            self._set_source(got)

    def _set_source(self, path):
        from utils.gui_config import update_cache_gui_section
        self._src.setText(path)
        try:
            update_cache_gui_section("sdcard_ingest", "source", path)
        except Exception:
            pass
        self._describe_source()

    def _describe_source(self):
        """Is it there, and what was it read into before? Read off the card
        itself (its own small history file), so it is true on any computer."""
        src = self._source()
        warn = False
        if not src:
            text = "Choose the SD-card or the folder with the pictures."
        elif not Path(src).is_dir():
            text = (f"❌ {src} is not there — insert the card, or press Choose if it "
                    f"came up under another drive letter.")
            warn = True
        else:
            try:
                from batch.sv0ReadSDCard import card_history
                hist = card_history(src)
            except Exception:
                hist = []
            if hist:
                last = hist[0]
                when = (last.get("when") or "")[:16].replace("T", " ")
                trip = last.get("trip") or last.get("trip_id") or "another trip"
                text = (f"last read into “{trip}” on {when} "
                        f"({last.get('copied', 0)} picture(s) copied)")
            else:
                text = "not read by SpeciesViewer before"
        self._src_note.setText(text)
        self._src_note.setStyleSheet(
            f"color: {_th.WARN if warn else _th.INK_SOFT}; font-size: 9pt;")
        self._read_btn.setEnabled(bool(self._trip_root and self._picture_root
                                       and src and Path(src).is_dir()))

    def _counts(self) -> dict:
        out = {}
        if self._picture_root and self._picture_root.is_dir():
            try:
                for p in self._picture_root.iterdir():
                    if p.is_dir() and wb._day_folder_name(p.name):
                        out[p.name] = _count_pictures(p)
            except OSError:
                pass
        return out

    def reload(self):
        self._describe_source()
        if not self._trip_root:
            self._table.setRowCount(0)
            self._summary.setText("Open or import the trip first (step 1).")
            self._totals = (0, 0)
            self.changed.emit()
            return
        survivors = wr.load_survivor_cache(self._trip_root)
        reads = wr.load_picture_reads(self._trip_root)
        counts = self._counts()
        days = sorted(set(counts) | set(survivors))
        self._table.setRowCount(len(days))
        here_tot = kept_tot = 0
        for i, d in enumerate(days):
            here = counts.get(d, 0)
            kept = len(survivors.get(d, ()))
            here_tot += here
            kept_tot += kept
            self._table.setItem(i, 0, QTableWidgetItem(d))
            item = QTableWidgetItem(f"{here} of {kept}" + (" ✅" if here >= kept else "")
                                    if kept else str(here))
            if kept:
                item.setForeground(QColor(_th.DONE_FG if here >= kept else _th.WARN))
            self._table.setItem(i, 1, item)
            self._table.setItem(i, 2, QTableWidgetItem(str(kept) if kept else "no backup"))
            r = reads.get(d)
            src = QTableWidgetItem(f"{r['source']} · {r['when']}" if r else "not read yet")
            if not r:
                src.setForeground(QColor(_th.INK_FAINT))
            self._table.setItem(i, 3, src)
        self._totals = (here_tot, kept_tot)
        self._summary.setText(
            "“Kept on the trip” comes from the SV INDEX backup (step 2): pictures "
            "culled on the trip are not counted and are not copied back."
            if survivors else
            "No SV INDEX backup read for this trip yet, so every picture on a card "
            "is copied. Read the metadata first (step 2) to leave the culled ones out.")
        self.changed.emit()

    def _read(self):
        from gui.trip_process import ReadSDCardDialog
        spec = getattr(self._main, "menu_spec", {}) or {}
        holder = None
        for group in spec.values():
            if isinstance(group, dict) and "Read SD-card or Folder…" in group:
                holder = group["Read SD-card or Folder…"]
                break
        before = self._counts()
        dlg = ReadSDCardDialog(self._main, holder, trip_mode=True)
        # The source chosen here, before the window opens: trip mode scans it
        # straight away.
        dlg.source_field.field.setText(self._source())
        dlg.exec()
        used = dlg.source_field.get_path()
        after = self._counts()
        grew = [d for d, n in after.items() if n > before.get(d, 0)]
        if grew:
            wr.record_picture_reads(self._trip_root, grew, used)
        if used and used != self._source():
            self._set_source(used)       # changed inside the read window
        self.reload()

    def _check(self):
        """Read-only: the pictures here against the backup's record."""
        survivors = wr.load_survivor_cache(self._trip_root)
        if not survivors:
            QMessageBox.information(self, "Check pictures",
                                    "There is no SV INDEX backup to compare with yet — "
                                    "read the metadata first (step 2).")
            return
        prefix = wr.load_backup_prefix(self._trip_root)
        lines, refused = [], []
        for day in sorted(survivors):
            man = {"raws": sorted(survivors[day]), "prefix": prefix}
            target = self._picture_root / day
            ok, why = wr.prefix_matches(target, man)
            if not ok:
                refused.append(f"{day}: {why}")
                continue
            rec = wr.reconcile_day(target, man)
            if rec["on_disk"] == 0:
                lines.append(f"{day}: no pictures here yet.")
            elif rec["culled"] or rec["missing"]:
                bit = []
                if rec["culled"]:
                    bit.append(f"{len(rec['culled'])} here that the backup does NOT "
                               f"list (culled during the trip)")
                if rec["missing"]:
                    bit.append(f"{len(rec['missing'])} still missing")
                lines.append(f"{day}: {rec['on_disk']} of {rec['recorded']} — " + "; ".join(bit))
            else:
                lines.append(f"{day}: all {rec['recorded']} pictures here, nothing extra.")
        box = QMessageBox(self)
        box.setWindowTitle("Check pictures")
        box.setIcon(QMessageBox.Icon.Warning if refused else QMessageBox.Icon.Information)
        box.setText("The pictures here compared with the backup's record:")
        box.setDetailedText("\n".join(refused + lines) or "Nothing to compare.")
        box.setInformativeText(
            ("Some days could not be compared — see the details.\n\n" if refused else "")
            + "Nothing was changed.")
        box.exec()


# ── the window ───────────────────────────────────────────────────────────────

class TripImportWindow(QDialog):
    def __init__(self, main_window, step=STEP_TRIP):
        super().__init__(main_window)
        self._main = main_window
        self.setWindowTitle("Import Trip")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.resize(1240, 760)

        from gui.wait_picture import picture_column
        from gui.work_restore_dialog import MetadataStep
        col = picture_column(self, "short_eared_owl")

        tiles = QHBoxLayout()
        self._tiles = [_StepTile(1, "Trip"),
                       _StepTile(2, "Metadata — SV INDEX card"),
                       _StepTile(3, "Pictures — cards and dumps")]
        for i, t in enumerate(self._tiles):
            t.clicked.connect(lambda i=i: self.go(i))
            tiles.addWidget(t, 1)
        col.addLayout(tiles)

        self._stack = QStackedWidget()
        self.trip_step = TripStep(main_window)
        self.meta_step = MetadataStep(self)
        self.pics_step = PicturesStep(main_window)
        for w in (self.trip_step, self.meta_step, self.pics_step):
            self._stack.addWidget(w)
            w.changed.connect(self._refresh_header)
        col.addWidget(self._stack, 1)

        self.trip_step.next_btn.clicked.connect(lambda: self.go(STEP_METADATA))
        self.meta_step.next_btn.clicked.connect(lambda: self.go(STEP_PICTURES))
        self.trip_step.changed.connect(self._trip_changed)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        col.addLayout(bottom)

        self._trip_changed()
        self.go(step)

    def _trip_changed(self):
        root, pic = _active_trip()
        self.meta_step.set_trip(root, pic)
        self.pics_step.set_trip(root, pic)
        name = Path(pic).name if pic else ""
        self.setWindowTitle(f"Import Trip — {name}" if name else "Import Trip")
        self._refresh_header()

    def go(self, step):
        self._stack.setCurrentIndex(step)
        for i, t in enumerate(self._tiles):
            t.set_current(i == step)
        # Each visit shows the disk as it is now: a card read in step 3 changes
        # step 2's numbers, and the other way round.
        if step == STEP_METADATA:
            self.meta_step.reload()
        elif step == STEP_PICTURES:
            self.pics_step.reload()
        self._refresh_header()

    def _refresh_header(self):
        for tile, step in zip(self._tiles, (self.trip_step, self.meta_step, self.pics_step)):
            tile.set_status(*step.status())
