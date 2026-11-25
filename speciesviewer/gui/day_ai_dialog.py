"""Culling grid ▸ AI… — the AI's suggestion for a whole day, all species at once.

ONE JOB: measure every picture of the day once (engine.picture_score for the
pose, engine.ai_measure for the animal, its size, sharpness on it and the
names — on a worker thread, cached), then show what engine.day_ai suggests:
on the grid a bin on the pictures that could go and a name tag per pose; here
the day in numbers, the species found (click one to go to it), and the cut-offs
you can change — the view follows at once.

INVARIANTS
- Nothing is written until you press Accept… and say what (step 4 of
  docs/plans/ai_day_plan.md): names (only from your own species list, at or
  above the threshold, only on pictures without a species), Purple on what
  could go, Green on what stays (off unless ticked). Only candidates change
  colour. The Accept is recorded first (engine.day_ai_marks) and checked per
  picture after; "Undo the last accept" puts back only what is still the
  AI's. Closing the dialog takes the marks off the grid.
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

from gui import theme
from gui.widgets import wrapped

logger = logging.getLogger("speciesviewer")


class _BusyDialog(QDialog):
    """A dialog that cannot be closed while it writes (busy)."""
    busy = False

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, e):
        if self.busy:
            e.ignore()
        else:
            super().closeEvent(e)


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

    def __init__(self, pics, region, master_root, parent=None, aggressive=False):
        super().__init__(parent)
        self.pics, self.region, self.master_root = pics, region, master_root
        self.aggressive = aggressive      # "Group similar pictures aggressively"
        self.master_days: dict = {}
        self.timing: list = []           # [(step, seconds)]: where the time went
        from engine.day_ai import Stopwatch
        self.clock = Stopwatch()         # … and within the animals and the names
        self.counts: dict = {}
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            import io
            from PIL import Image, ImageOps
            from engine import af_point, ai_measure, ai_yardstick, day_ai
            from engine.picture_score import Cache, image_bytes, score_pictures
            import time
            pics, n = self.pics, len(self.pics)
            t = [time.time()]

            def took(step):
                now = time.time()
                self.timing.append((step, now - t[0]))
                t[0] = now
            cache = Cache()
            try:
                score_pictures(pics, cache, cancelled=lambda: self._stop,
                               progress=lambda i, t: self.progress.emit(
                                   "Measuring sharpness", i, t))
            finally:
                cache.close()
            if self._stop:
                return
            took("sharpness")
            self.progress.emit("Reading the cameras", 0, n)
            af = af_point.read([p["src"] for p in pics])
            took("cameras")
            self.progress.emit("Getting the yardstick (your master pictures)", 0, n)
            yard = ai_yardstick.get(self.master_root)
            took("yardstick")
            aic = ai_measure.AiCache()

            def load(src):
                data = image_bytes(src)
                return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
            try:
                def measure_one(p, im, box):
                    info = af_point.lookup_info(af, p["src"])
                    try:
                        p.update(ai_measure.measure(
                            p["src"], im if im is not None else (lambda s=p["src"]: load(s)),
                            p, info.get("model", ""),
                            native=info.get("native") or 0, cache=aic,
                            region=self.region, master_root=self.master_root or None,
                            yardstick=yard, name=False, box=box))
                    except Exception as e:
                        logger.warning(f"Day AI: {p['src'].name}: {e}")
                        p.update({"box": None, "names": [], "px": 0, "sharp_pct": 0.0})
                from engine import ai_models
                clock = self.clock
                with clock.part("starting the detector"):
                    ai_models.detector()
                count = day_ai.find_animals(
                    pics, measure_one, load, aic,
                    progress=lambda i, t: self.progress.emit("Looking for the animals", i, t),
                    stopped=lambda: self._stop, join=self.aggressive, clock=clock)
                if self._stop:
                    return
                self.counts = count
                logger.info(f"Day AI: animals detected {count['detected']}, followed "
                            f"{count['followed']}, from earlier {count['cached']}")
                took("animals")
                with clock.part("starting the names (BioCLIP, eBird's list)"):
                    ai_measure.prepare_names(self.region)
                # One name per pose: only the frame whose name the pose gets.
                # One name per series: poses that show the same animal (it was
                # followed from one into the next) share their best frame's name.
                series = (day_ai.name_series(pics) if self.aggressive else
                          [[p] for p in day_ai.pose_leaders(pics)])
                firsts = [x[0] for x in series]
                ahead = day_ai.Prefetch([p["src"] for p in firsts
                                         if aic.vector(p["src"], p["box"]) is None], load)
                try:
                    for i, x in enumerate(series, 1):
                        if self._stop:
                            return
                        p = x[0]

                        def picture(src=p["src"]):
                            with clock.part("waiting for a decoded picture (names)"):
                                return ahead.get(src)
                        part = ("first name" if i == 1 else
                                "a name" if aic.vector(p["src"], p["box"]) is None else
                                "a name, picture seen before")
                        try:
                            with clock.part(part):
                                p["names"], p["among"] = ai_measure.name_of(
                                    p["src"], picture, p["box"],
                                    cache=aic, region=self.region,
                                    master_root=self.master_root or None)
                        except Exception as e:
                            logger.warning(f"Day AI: naming {p['src'].name}: {e}")
                        for q in x[1:]:
                            q["names"], q["among"] = p.get("names", []), p.get("among", "")
                        if i % 5 == 0 or i == len(series):
                            self.progress.emit("Naming each series", i, len(series))
                finally:
                    ahead.close()
                logger.info(f"Day AI: {len(series)} series named "
                            f"({sum(len(x) for x in series)} poses with an animal)")
                if ai_measure.still_groups(self.region):
                    self._name_plants(pics, aic, load, clock)
            finally:
                aic.close()
            took("names")
            self.progress.emit("Looking in your master", n, n)
            self.master_days = master_days(self.master_root, pics)
            took("your master")
            logger.info("Day AI timing: " + ", ".join(f"{s} {v:.1f} s" for s, v in self.timing))
            logger.info("Day AI detail: " + self.clock.line())
            self.done.emit(pics)
        except Exception as e:
            logger.error(f"Day AI failed: {e}", exc_info=True)
            self.failed.emit(str(e))


    def _name_plants(self, pics, aic, load, clock):
        """Poses without an animal: the whole sharpest frame, named among the
        country's plants (only when BioCLIP agrees: ai_measure.plant_names)."""
        from engine import ai_measure, day_ai
        still = day_ai.still_leaders(pics)
        ahead = day_ai.Prefetch([p["src"] for p in still
                                 if aic.vector(p["src"], None) is None], load)
        named = 0
        try:
            for i, p in enumerate(still, 1):
                if self._stop:
                    return

                def picture(src=p["src"]):
                    with clock.part("waiting for a decoded picture (plants)"):
                        return ahead.get(src)
                try:
                    with clock.part("a plant name"):
                        p["names"], p["among"] = ai_measure.plant_name_of(
                            p["src"], picture, cache=aic, region=self.region,
                            master_root=self.master_root or None)
                    named += bool(p["names"])
                except Exception as e:
                    logger.warning(f"Day AI: plant name {p['src'].name}: {e}")
                if i % 5 == 0 or i == len(still):
                    self.progress.emit("Naming the plants", i, len(still))
        finally:
            ahead.close()
        logger.info(f"Day AI: {len(still)} poses without an animal, {named} named as a plant")


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
        from utils.config import _config
        self._aggressive = QCheckBox("Group similar pictures aggressively")
        self._aggressive.setChecked(
            _config.get("ai", "group_aggressively", fallback="no") == "yes")
        self._aggressive.setToolTip(
            "Faster on a slow computer: when the AI can follow the animal from one pose "
            "into the next, those poses share one name, and fewer pictures need the full "
            "look.\nOff: every pose is looked at and named on its own.\nTakes effect "
            "the next time you open AI… on a day.")
        self._aggressive.toggled.connect(self._remember_aggressive)
        form.addRow(self._aggressive)
        top.addLayout(form)
        lay.addLayout(top)

        self._bar = QProgressBar()
        self._bar_text = QLabel("")
        lay.addWidget(self._bar_text)
        # Where the time went, in detail (selectable, to copy into a message).
        self._detail = wrapped("", self)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail.setStyleSheet(f"color: {theme.INK_FAINT};")
        self._detail.hide()
        lay.addWidget(self._detail)
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
            "On the grid: a bin on the pictures that could go, and the AI's name on each "
            "pose (grey: not sure). Nothing is written until you press Accept…"))
        self._note = QLabel("")
        self._note.setWordWrap(True)
        lay.addWidget(self._note)
        box = QDialogButtonBox()
        self._accept_btn = QPushButton("Accept…")
        self._accept_btn.setToolTip("Write the names and colours you choose to the pictures")
        self._accept_btn.setEnabled(False)
        self._accept_btn.clicked.connect(self._on_accept)
        box.addButton(self._accept_btn, QDialogButtonBox.ButtonRole.ActionRole)
        self._undo_btn = QPushButton("Undo the last accept")
        self._undo_btn.clicked.connect(self._on_undo)
        box.addButton(self._undo_btn, QDialogButtonBox.ButtonRole.ActionRole)
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
        self._folder = str(pics[0]["src"].parent) if pics else ""
        self._refresh_undo()
        self._stats.setText(f"{len(pics)} pictures" + (f" · {region}" if region else ""))
        self._run = _Measure(pics, region, master_root, self,
                             aggressive=self._aggressive.isChecked())
        self._run.progress.connect(self._on_progress)
        self._run.done.connect(self._on_done)
        self._run.failed.connect(lambda m: self._bar_text.setText(f"Stopped by an error: {m}"))
        self._t0 = time.time()
        self._run.start()

    def _remember_aggressive(self, on: bool):
        from gui.ai_models_dialog import remember_aggressive
        remember_aggressive(on)
        self._bar_text.setText("Grouping changed — it takes effect the next time you "
                               "open AI… on a day.")

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
        parts = [f"{s} {v:.0f} s" for s, v in getattr(self._run, "timing", []) if v >= 0.5]
        self._bar_text.setText(f"Measured in {time.time() - self._t0:.0f} s"
                               + (f" — {', '.join(parts)}." if parts else "."))
        c = getattr(self._run, "counts", {})
        detail = getattr(self._run, "clock", None)
        if detail is not None and detail.secs:
            self._detail.setText(
                (f"Animals: detector on {c.get('detected', 0)}, followed "
                 f"{c.get('followed', 0)}, from earlier {c.get('cached', 0)}. " if c else "")
                + "In detail: " + detail.line() + ".")
            self._detail.show()
        self._accept_btn.setEnabled(True)
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
        among = next((p.get("among") for p in self._pics
                      if p.get("among") and not p.get("plant_pose")), "")
        plants = next((p.get("among") for p in self._pics
                       if p.get("among") and p.get("plant_pose")), "")
        if plants:
            among = f"{among}; plants: {plants}" if among else plants
        self._stats.setText(
            f"<b>{st['pictures']}</b> pictures in <b>{st['poses']}</b> poses"
            f"{' · ' + self._region if self._region else ''}<br>"
            f"Could go: <b>{st['purple']}</b> ({st['bytes'] / 1e9:.1f} GB)"
            f"{' — ' + reasons if reasons else ''}<br>"
            f"Kept: <b>{st['keep']}</b> · already yours (stars, colour): {st['yours']}"
            + (f", of which marked Blue or Purple: {st['marked']} "
               f"({st['marked_bytes'] / 1e9:.1f} GB)" if st['marked'] else "") + "<br>"
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
        marks, names = {}, {}
        for p in self._pics:
            n = p.get("name")
            if n is not None:
                names[p["key"]] = dict(n)
                tag, sure = f"{n.get('name') or n.get('sci')} {n['prob'] * 100:.0f}%", True
            elif p.get("has_animal_pose") or p.get("plant_pose"):
                tag, sure = "? not sure", False
            else:
                tag, sure = "", True
            marks[p["key"]] = (p.get("verdict"), tag, sure)
        try:
            self._grid.show_ai_marks(marks, names)
        except RuntimeError:
            pass

    def _on_row(self, row, _col):
        """Select that species' pictures on the grid, and go to the first."""
        if 0 <= row < len(getattr(self, "_row_keys", [])) and self._row_keys[row]:
            try:
                self._grid.select_keys(self._row_keys[row])
            except RuntimeError:
                pass

    # ── accept + undo ───────────────────────────────────────────────────────

    def _plan(self):
        """What Accept would write: ({key: [before, name]}, {key: [before, "Purple"]},
        {key: [before, "Green"]}) — names only from your list and only where no
        species is set; colours only on candidates."""
        names, purple, green = {}, {}, {}
        can_name = self._grid.ai_can_write_species()
        for p in self._pics:
            k = p["key"]
            n = p.get("name")
            if (can_name and n is not None and n.get("how") in ("exact", "renamed")
                    and n.get("name") and not self._grid.ai_species_of(k)):
                names[k] = ["", n["name"]]
            before = self._grid.ai_colour_of(k)
            if p.get("verdict") == "purple" and before != "Purple":
                purple[k] = [before, "Purple"]
            elif p.get("verdict") == "keep" and before != "Green":
                green[k] = [before, "Green"]
        return names, purple, green

    def _on_accept(self):
        from PyQt6.QtWidgets import QCheckBox, QMessageBox
        from engine import day_ai_marks
        names, purple, green = self._plan()
        dlg = _BusyDialog(self)
        dlg.setWindowTitle("Accept the AI's suggestion")
        lay = QVBoxLayout(dlg)
        n_poses = len({p["pose"] for p in self._pics if p["key"] in names})
        cb_names = QCheckBox(f"Names: {len(names)} pictures ({n_poses} poses) without a "
                             f"species get the AI's name")
        cb_names.setChecked(bool(names))
        cb_names.setEnabled(bool(names))
        cb_purple = QCheckBox(f"Purple: {len(purple)} pictures that could go")
        cb_purple.setChecked(bool(purple))
        cb_purple.setEnabled(bool(purple))
        cb_green = QCheckBox(f"Green: {len(green)} pictures that stay")
        cb_green.setEnabled(bool(green))
        for w in (cb_names, cb_purple, cb_green):
            lay.addWidget(w)
        extra = []
        if not self._grid.ai_can_write_species():
            extra.append("Names are given on a culling day only; this day has its own "
                         "species step.")
        extra.append("Only names from your own species list, at or above the threshold, "
                     "and only on pictures that have no species yet. Nothing is deleted: "
                     "Purple pictures go when you delete Purple. \"Undo the last accept\" "
                     "puts everything back.")
        lay.addWidget(wrapped(" ".join(extra)))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        write_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        write_btn.setText("Write")
        close_btn = bb.button(QDialogButtonBox.StandardButton.Cancel)
        bar = QProgressBar()
        bar.hide()
        status = QLabel("")
        status.setWordWrap(True)
        lay.addWidget(bar)
        lay.addWidget(status)
        lay.addWidget(bb)
        bb.rejected.connect(dlg.reject)

        def write():
            n_names = names if cb_names.isChecked() else {}
            colours = dict(purple if cb_purple.isChecked() else {})
            if cb_green.isChecked():
                colours.update(green)
            if not n_names and not colours:
                dlg.reject()
                return
            for w in (cb_names, cb_purple, cb_green, write_btn, close_btn):
                w.setEnabled(False)
            dlg.busy = True
            bar.show()
            eid = day_ai_marks.start(self._folder, colours, n_names)     # BEFORE writing
            c_done, c_failed, s_done, s_failed = self._write_steps(
                {k: v[1] for k, v in colours.items()},
                {k: v[1] for k, v in n_names.items()}, bar, status)
            day_ai_marks.confirm(eid, c_done, s_done)
            dlg.busy = False
            text = f"Written: {len(s_done)} names, {len(c_done)} colours."
            if c_failed or s_failed:
                text += f" {len(c_failed | s_failed)} could not be changed (see the log)."
            status.setText(text)
            write_btn.hide()
            close_btn.setText("Close")
            close_btn.setEnabled(True)
            self._after_write(text, c_failed | s_failed, warn=False)

        bb.accepted.connect(write)
        dlg.exec()

    def _on_undo(self):
        from PyQt6.QtWidgets import QMessageBox
        from engine import day_ai_marks
        last = day_ai_marks.last(self._folder)
        if not last:
            return
        colours, species, changed = day_ai_marks.undo_plan(
            last, self._grid.ai_colour_of, self._grid.ai_species_of)
        if QMessageBox.question(
                self, "Undo the last accept",
                f"Put back {len(species)} species and {len(colours)} colours as they were "
                f"before {last['when']}?"
                + (f"\n\n{changed} you changed since are left as they are." if changed else ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return
        self.setEnabled(False)
        try:
            c_done, c_failed, s_done, s_failed = self._write_steps(
                colours, species, self._bar, self._bar_text)
        finally:
            self.setEnabled(True)
            self._bar.hide()
        day_ai_marks.forget(last["id"])
        self._after_write(f"Put back: {len(s_done)} species, {len(c_done)} colours.",
                          c_failed | s_failed)

    def _write_steps(self, colours: dict, species: dict, bar, status, step: int = 25):
        """Write {key: colour} and {key: species} a few pictures at a time, showing
        how far it is — the window stays alive. → (colours done, failed,
        species done, failed)."""
        from PyQt6.QtWidgets import QApplication
        total = len(colours) + len(species)
        bar.setRange(0, max(1, total))
        bar.setValue(0)
        bar.show()
        sets = [set(), set(), set(), set()]
        done = 0
        for what, todo, fn, at in (("colours", colours, self._grid.ai_write_colours, 0),
                                   ("names", species, self._grid.ai_write_species, 2)):
            keys = list(todo)
            for i in range(0, len(keys), step):
                part = {k: todo[k] for k in keys[i:i + step]}
                status.setText(f"Writing {what}: {done + len(part)} of {total}…")
                QApplication.processEvents()
                ok, bad = fn(part)
                sets[at] |= ok
                sets[at + 1] |= bad
                done += len(part)
                bar.setValue(done)
        QApplication.processEvents()
        return tuple(sets)

    def _after_write(self, text: str, failed, warn: bool = True):
        from PyQt6.QtWidgets import QMessageBox
        for p in self._pics:                      # the new colours count from now on
            p["label"] = self._grid.ai_colour_of(p["key"])
        self._recompute()
        self._refresh_undo()
        if failed and warn:
            QMessageBox.warning(self, "Not every picture took it",
                                f"{text}\n\n{len(failed)} could not be changed (see the log).")
        self._note.setText(text)

    def _refresh_undo(self):
        from engine import day_ai_marks
        last = day_ai_marks.last(self._folder) if self._folder else None
        self._undo_btn.setEnabled(last is not None)
        self._undo_btn.setText(f"Undo the last accept ({last['when']})" if last
                               else "Undo the last accept")

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
