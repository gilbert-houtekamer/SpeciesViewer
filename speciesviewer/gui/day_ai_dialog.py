"""Culling grid ▸ AI… — the AI's suggestion for a whole day, all species at once.

ONE JOB: measure every picture of the day once (engine.picture_score for the
pose, engine.ai_measure for the animal, its size, sharpness on it and the
names — on a worker thread, cached), then show what engine.day_ai suggests:
on the grid a bin on the pictures that could go and a name tag per pose; here
the day in numbers, the species found (click one to go to it), and the cut-offs
you can change — the view follows at once.

INVARIANTS
- A view only (step 3 of docs/plans/ai_day_plan.md): nothing is written to a
  picture, a sidecar or a list. Closing the dialog takes the marks off the grid.
- The worker reads only files and the app-data caches; the grid and the
  collection are touched on the GUI thread only.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gui.widgets import wrapped

logger = logging.getLogger("speciesviewer")


def day_pictures(grid) -> tuple[list[dict], str]:
    """(picture dicts, country ISO code) for every picture of the grid's day —
    read on the GUI thread."""
    from engine import redundancy as rd
    from engine.countries import iso_of
    coll = grid._collection
    pics = []
    for key in grid._base_keys():
        try:
            item = coll.item_for_key(key)
        except (KeyError, ValueError):
            continue
        if getattr(item, "is_video", False):
            continue
        src = None
        for get in ("raw_path", "jpg_path"):
            fn = getattr(coll, get, None)
            try:
                src = fn(key) if fn else None
            except Exception:
                src = None
            if src:
                break
        if not src or not Path(src).exists():
            continue
        src = Path(src)
        pics.append({"key": key, "upn": key, "src": src, "time": rd._time(item.date),
                     "stars": int(getattr(item, "rating", 0) or 0),
                     "label": (getattr(item, "color", "") or "").strip().capitalize()
                     .replace("Grey", "").replace("None", ""),
                     "jpg": False, "bytes": src.stat().st_size})
    from datetime import datetime
    pics.sort(key=lambda p: (p["time"] or datetime.min, p["key"]))
    # The day's country, from the first pictures that have a location.
    found = Counter()
    reader = getattr(coll, "read_location", None)
    for p in pics:
        if reader is None or sum(found.values()) >= 12:
            break
        try:
            iso = iso_of((reader(p["key"]) or {}).get("Country", ""))
        except Exception:
            iso = ""
        if iso:
            found[iso] += 1
    region = found.most_common(1)[0][0] if found else ""
    return pics, region


def master_days(master_root, pics) -> dict:
    """{lower-case scientific name: days the master has pictures of it} for the
    first names the AI gave today (own connection: call on the worker thread)."""
    if not master_root:
        return {}
    from db.master_db import MasterDB
    from engine import redundancy as rd
    names = {p["names"][0]["sci"].lower() for p in pics if p.get("names")}
    out = {}
    db = MasterDB(master_root)
    try:
        db.ensure_fresh()
        for sci in names:
            days = {t.date() for t in (rd._time(r.get("DateTimeOriginal"))
                                       for r in db.pictures_for_species_and_subspecies(sci))
                    if t}
            out[sci] = len(days)
    except Exception as e:
        logger.warning(f"Day AI: master lookup failed: {e}")
    finally:
        db.close()
    return out


class _Measure(QThread):
    progress = pyqtSignal(str, int, int)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, pics, region, master_root, parent=None):
        super().__init__(parent)
        self.pics, self.region, self.master_root = pics, region, master_root
        self.master_days: dict = {}
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            import io
            from PIL import Image, ImageOps
            from engine import af_point, ai_measure, ai_yardstick
            from engine.picture_score import Cache, image_bytes, score_pictures
            pics, n = self.pics, len(self.pics)
            cache = Cache()
            try:
                score_pictures(pics, cache, cancelled=lambda: self._stop,
                               progress=lambda i, t: self.progress.emit(
                                   "Measuring sharpness", i, t))
            finally:
                cache.close()
            if self._stop:
                return
            self.progress.emit("Reading the cameras", 0, n)
            af = af_point.read([p["src"] for p in pics])
            self.progress.emit("Getting the yardstick (your master pictures)", 0, n)
            yard = ai_yardstick.get(self.master_root)
            aic = ai_measure.AiCache()
            try:
                for i, p in enumerate(pics, 1):
                    if self._stop:
                        return
                    info = af_point.lookup_info(af, p["src"])

                    def load(src=p["src"]):
                        data = image_bytes(src)
                        return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
                    try:
                        p.update(ai_measure.measure(
                            p["src"], load, p, info.get("model", ""),
                            native=info.get("native") or 0, cache=aic,
                            region=self.region, master_root=self.master_root or None,
                            yardstick=yard))
                    except Exception as e:
                        logger.warning(f"Day AI: {p['src'].name}: {e}")
                        p.update({"box": None, "names": [], "px": 0, "sharp_pct": 0.0})
                    if i % 5 == 0 or i == n:
                        self.progress.emit("Looking for the animals", i, n)
            finally:
                aic.close()
            self.progress.emit("Looking in your master", n, n)
            self.master_days = master_days(self.master_root, pics)
            self.done.emit(pics)
        except Exception as e:
            logger.error(f"Day AI failed: {e}", exc_info=True)
            self.failed.emit(str(e))


class DayAiDialog(QDialog):
    def __init__(self, grid, master_root: str):
        super().__init__(grid)
        from engine.day_ai import DaySettings
        self.setWindowTitle("AI — this day")
        self._grid = grid
        self._pics: list[dict] = []
        self._groups: list = []
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self._stats = QLabel("")
        self._stats.setWordWrap(True)
        self._stats.setTextFormat(Qt.TextFormat.RichText)
        self._stats.setAlignment(Qt.AlignmentFlag.AlignTop)
        top.addWidget(self._stats, 1)
        form = QFormLayout()
        d = DaySettings()
        self._thr = self._spin(0, 100, d.threshold, " %",
                               "A pose gets the AI's name when its first name is at least this sure")
        self._sharp = self._spin(0, 100, d.sharp_cut, " %",
                                 "Sharp on the animal below this = out of focus: could go")
        self._per_pose = self._spin(1, 20, d.per_pose, "",
                                    "How many frames of one pose stay (your own keepers count)")
        self._small = self._spin(0, 5000, d.small_px, " px",
                                 "An animal smaller than this could go when its pose has a "
                                 "bigger one (0 = off)")
        self._small.setSingleStep(100)
        form.addRow("Name from", self._thr)
        form.addRow("Out of focus below", self._sharp)
        form.addRow("Keep per pose", self._per_pose)
        form.addRow("Too small below", self._small)
        from PyQt6.QtWidgets import QCheckBox
        self._use_master = QCheckBox("Fewer of species your master has often")
        self._use_master.setChecked(d.use_master)
        self._use_master.setToolTip(
            "Per species, keep no more today than the master's own rule: 12 of a species "
            "you never photographed, down to 4 of one you have on 32 days or more")
        self._use_master.toggled.connect(self._recompute)
        form.addRow(self._use_master)
        top.addLayout(form)
        lay.addLayout(top)

        self._bar = QProgressBar()
        self._bar_text = QLabel("")
        lay.addWidget(self._bar_text)
        lay.addWidget(self._bar)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["Species", "Poses", "Pictures", "Best",
                                               "Days in master", "Kept"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setToolTip("Click a species to select its pictures on the grid")
        self._table.cellClicked.connect(self._on_row)
        lay.addWidget(self._table, 1)

        lay.addWidget(wrapped(
            "A suggestion only — nothing is written. On the grid: a bin on the pictures "
            "that could go, and the AI's name on each pose (grey: not sure). Accepting "
            "comes in a next version."))
        box = QDialogButtonBox()
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop)
        box.addButton(self._stop_btn, QDialogButtonBox.ButtonRole.ActionRole)
        close = box.addButton(QDialogButtonBox.StandardButton.Close)
        close.clicked.connect(self.close)
        lay.addWidget(box)
        self.resize(720, 620)

        for w in (self._thr, self._sharp, self._per_pose, self._small):
            w.valueChanged.connect(self._recompute)

        pics, region = day_pictures(grid)
        self._region = region
        self._stats.setText(f"{len(pics)} pictures" + (f" · {region}" if region else ""))
        self._run = _Measure(pics, region, master_root, self)
        self._run.progress.connect(self._on_progress)
        self._run.done.connect(self._on_done)
        self._run.failed.connect(lambda m: self._bar_text.setText(f"Stopped by an error: {m}"))
        self._t0 = time.time()
        self._run.start()

    @staticmethod
    def _spin(lo, hi, val, suffix, tip):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        if suffix:
            s.setSuffix(suffix)
        s.setToolTip(tip)
        return s

    def _settings(self):
        from engine.day_ai import DaySettings
        return DaySettings(threshold=self._thr.value(), sharp_cut=self._sharp.value(),
                           per_pose=self._per_pose.value(), small_px=self._small.value(),
                           use_master=self._use_master.isChecked())

    # ── the run ─────────────────────────────────────────────────────────────

    def _on_progress(self, what, i, n):
        self._bar_text.setText(f"{what}…")
        self._bar.setRange(0, max(1, n))
        self._bar.setValue(i)

    def _on_done(self, pics):
        self._pics = pics
        self._bar.hide()
        self._stop_btn.hide()
        self._bar_text.setText(f"Measured in {time.time() - self._t0:.0f} s.")
        self._recompute()

    def _on_stop(self):
        self._run.stop()
        self._bar_text.setText("Stopping…")

    # ── the suggestion ──────────────────────────────────────────────────────

    def _recompute(self):
        if not self._pics:
            return
        from engine import day_ai
        self._groups = day_ai.decide(self._pics, self._settings(),
                                     master_days=self._run.master_days)
        st = day_ai.summary(self._pics, self._groups)
        why = st["why"]
        reasons = ", ".join(f"{n} {w}" for w, n in sorted(why.items(), key=lambda x: -x[1]))
        among = next((p.get("among") for p in self._pics if p.get("among")), "")
        self._stats.setText(
            f"<b>{st['pictures']}</b> pictures in <b>{st['poses']}</b> poses"
            f"{' · ' + self._region if self._region else ''}<br>"
            f"Could go: <b>{st['purple']}</b> ({st['bytes'] / 1e9:.1f} GB)"
            f"{' — ' + reasons if reasons else ''}<br>"
            f"Kept: <b>{st['keep']}</b> · already yours (stars, colour): {st['yours']}<br>"
            f"Poses named: {sum(e['poses'] for e in st['species'].values())} · "
            f"not sure: {st['not_sure_poses']} · no animal: {st['no_animal_poses']}"
            + (f"<br><span style='color:gray'>Names chosen from {among}</span>" if among else ""))
        rows = sorted(st["species"].items(), key=lambda kv: -kv[1]["pictures"])
        self._table.setRowCount(len(rows))
        self._row_keys = []
        days = self._run.master_days
        for r, (name, e) in enumerate(rows):
            note = "" if e["how"] == "exact" else " (not in your list)" if not e["how"] else ""
            d = days.get((e["sci"] or "").lower())
            cells = [f"{name}{note}", str(e["poses"]), str(e["pictures"]),
                     f"{e['best'] * 100:.0f} %", "" if d is None else str(d), str(e["kept"])]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c == 0:
                    it.setToolTip(e["sci"] or "")
                if c:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(r, c, it)
            self._row_keys.append(e["keys"])
        self._show_on_grid()

    def _show_on_grid(self):
        marks = {}
        for p in self._pics:
            n = p.get("name")
            if n is not None:
                tag, sure = f"{n.get('name') or n.get('sci')} {n['prob'] * 100:.0f}%", True
            elif p.get("has_animal_pose"):
                tag, sure = "? not sure", False
            else:
                tag, sure = "", True
            marks[p["key"]] = (p.get("verdict"), tag, sure)
        try:
            self._grid.show_ai_marks(marks)
        except RuntimeError:
            pass

    def _on_row(self, row, _col):
        """Select that species' pictures on the grid, and go to the first."""
        if 0 <= row < len(getattr(self, "_row_keys", [])) and self._row_keys[row]:
            try:
                self._grid.select_keys(self._row_keys[row])
            except RuntimeError:
                pass

    # ── closing ─────────────────────────────────────────────────────────────

    def _finish(self):
        if self._run.isRunning():
            self._run.stop()
            self._run.wait(30000)
        try:
            self._grid.clear_ai_marks()
        except RuntimeError:
            pass

    def closeEvent(self, e):
        self._finish()
        super().closeEvent(e)

    def reject(self):                    # Esc does not pass through closeEvent
        self._finish()
        super().reject()
