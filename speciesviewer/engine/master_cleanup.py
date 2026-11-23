"""Clean up the master — the AI… suggestion and the Purple delete, for ALL species.

ONE JOB: walk every species of the master and either
  mark   — set the AI… bins Purple (the suggestion with its default settings),
  delete — move every Purple picture without a JPG to the master trash,
  undo   — put back the colours of the last "mark" run,
reporting one line per species (a dict, for the window's log and the CSV).

INVARIANT: only CANDIDATES (engine.redundancy.is_candidate: no stars above the
day's usual, Green or no colour, no JPG) are ever set Purple, and each species'
marking is recorded (engine.purple_marks) BEFORE a colour is written; an undo
touches only pictures that are still Purple. Only pictures WITHOUT a JPG are
deleted, and only into the master trash (db.master_mutate.delete_picture):
nothing is final until Master Trash… is emptied.
"""
from __future__ import annotations

import csv
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from engine import purple_marks
from engine import redundancy as rd

logger = logging.getLogger("speciesviewer")

Report = Callable[[dict], None]
Cancelled = Callable[[], bool]

CSV_FIELDS = ["species", "pictures", "days", "per day", "candidates", "purple",
              "done", "failed", "with JPG left", "GB"]


def master_species(db) -> tuple[dict, dict]:
    """({species: [picture row]}, {date: star floor}) for the whole master —
    one query. Largest species first."""
    by_sci, stars = defaultdict(list), defaultdict(Counter)
    for rows in db.pictures_in_daterange("0001-01-01", "9999-12-31").values():
        for r in rows:
            t = rd._time(r.get("DateTimeOriginal"))
            if t:
                stars[t.date()][int(float(r.get("Rating") or 0) or 0)] += 1
            sci = (r.get("WetSoortNaam") or "").strip()
            if sci:
                by_sci[sci].append(r)
    order = sorted(by_sci, key=lambda s: (-len(by_sci[s]), s.lower()))
    return {s: by_sci[s] for s in order}, {d: rd.star_floor(c) for d, c in stars.items()}


def _has_jpg(row) -> bool:
    return any(Path(f).suffix.lower() in (".jpg", ".jpeg") for f in row.get("Files") or [])


def purple_count(by_sci: dict) -> tuple[int, int]:
    """(Purple pictures without a JPG, Purple pictures with one)."""
    free = jpg = 0
    for rows in by_sci.values():
        for r in rows:
            if (r.get("Label") or "").strip() == "Purple":
                if _has_jpg(r):
                    jpg += 1
                else:
                    free += 1
    return free, jpg


def mark_all(db, master_root, by_sci, floors, *, report: Report,
             cancelled: Cancelled = lambda: False, cache=None,
             set_label=None) -> dict:
    """Mark the AI… bins Purple in every species. Returns the totals."""
    from engine.picture_score import Cache, score_pictures
    if set_label is None:
        from db.master_mutate import set_label
    own_cache = cache is None
    cache = cache or Cache()
    run = time.strftime("%Y%m%d-%H%M%S")
    tot = Counter()
    try:
        for sci, rows in by_sci.items():
            if cancelled():
                break
            pics = rd.from_master_rows(rows, master_root)
            rd.set_floors(pics, floors)
            line = {"species": sci, "pictures": len(rows),
                    "days": len(rd.days_of(pics)),
                    "per day": rd.per_day_for(len(rd.days_of(pics))),
                    "candidates": sum(rd.is_candidate(p) for p in pics),
                    "purple": 0, "done": 0, "failed": 0, "with JPG left": "",
                    "GB": 0.0}
            if line["candidates"]:
                score_pictures(pics, cache, cancelled=cancelled)
                if cancelled():
                    break
                rd.select(pics, rd.default_settings(pics))
                bins = [p for p in pics if p.get("verdict") == rd.PURPLE
                        and rd.is_candidate(p)]
                line["purple"] = len(bins)
                if bins:
                    before = {p["upn"]: p["label"] for p in bins}
                    bid = purple_marks.start(master_root, sci, before, run=run)
                    done = set()
                    for p in bins:
                        if cancelled():
                            break
                        try:
                            set_label(db, master_root, p["upn"], "Purple")
                            done.add(p["upn"])
                            line["GB"] += p["bytes"] / 1e9
                        except Exception as e:
                            logger.warning(f"Clean up: Purple on {p['upn']}: {e}")
                            line["failed"] += 1
                    purple_marks.confirm(bid, done)
                    line["done"] = len(done)
            line["GB"] = round(line["GB"], 2)
            for k in ("pictures", "candidates", "purple", "done", "failed"):
                tot[k] += line[k]
            tot["GB"] += line["GB"]
            tot["species"] += 1
            report(line)
    finally:
        if own_cache:
            cache.close()
    return dict(tot)


def delete_all_purple(db, master_root, by_sci, *, report: Report,
                      cancelled: Cancelled = lambda: False,
                      delete_picture=None) -> dict:
    """Move every Purple picture WITHOUT a JPG to the master trash."""
    if delete_picture is None:
        from db.master_mutate import delete_picture
    root = Path(master_root)
    tot = Counter()
    for sci, rows in by_sci.items():
        if cancelled():
            break
        purple = [r for r in rows if (r.get("Label") or "").strip() == "Purple"]
        if not purple:
            continue
        line = {"species": sci, "pictures": len(rows), "purple": len(purple),
                "done": 0, "failed": 0, "with JPG left": 0, "GB": 0.0}
        for r in purple:
            if cancelled():
                break
            if _has_jpg(r):
                line["with JPG left"] += 1
                continue
            folder = root / (r.get("TargetPath") or "")
            size = 0
            for f in r.get("Files") or []:
                try:
                    size += (folder / f).stat().st_size
                except OSError:
                    pass
            try:
                delete_picture(db, root, r["UniquePictureName"])
                line["done"] += 1
                line["GB"] += size / 1e9
            except Exception as e:
                logger.warning(f"Clean up: delete {r['UniquePictureName']}: {e}")
                line["failed"] += 1
        line["GB"] = round(line["GB"], 2)
        for k in ("purple", "done", "failed", "with JPG left"):
            tot[k] += line[k]
        tot["GB"] += line["GB"]
        tot["species"] += 1
        report(line)
    return dict(tot)


def undo_last_run(db, master_root, *, report: Report,
                  cancelled: Cancelled = lambda: False, set_label=None) -> dict:
    """Put back the colours of the last all-species marking, only where the
    picture is still Purple; each species' record is dropped once undone."""
    if set_label is None:
        from db.master_mutate import set_label
    _run, batches = purple_marks.last_run(master_root)
    tot = Counter()
    for b in batches:
        if cancelled():
            break
        line = {"species": b["sci"], "purple": len(b["before"]),
                "done": 0, "failed": 0, "changed since": 0}
        left = {}
        for upn, colour in b["before"].items():
            if cancelled():
                left[upn] = colour
                continue
            now = db.picture(upn)
            if now is None or (now.get("Label") or "") != "Purple":
                line["changed since"] += 1
                continue
            try:
                set_label(db, master_root, upn, colour)
                line["done"] += 1
            except Exception as e:
                logger.warning(f"Clean up: undo on {upn}: {e}")
                line["failed"] += 1
                left[upn] = colour
        purple_marks.forget(b["id"])
        if left:                         # cancelled or failed: keep those for later
            purple_marks.start(master_root, b["sci"], left, run=b.get("run", ""))
        for k in ("purple", "done", "failed", "changed since"):
            tot[k] += line[k]
        tot["species"] += 1
        report(line)
    return dict(tot)


def csv_path(kind: str) -> Path:
    from utils.cache_paths import app_data_dir
    p = app_data_dir() / "reports" / f"master_cleanup_{kind}_{datetime.now():%Y%m%d-%H%M%S}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class CsvReport:
    """Writes each species line as it comes (flushed, so a stopped run still
    has its report)."""

    def __init__(self, path: Path, fields):
        self.path = path
        self._fh = open(path, "w", newline="", encoding="utf-8-sig")
        self._w = csv.DictWriter(self._fh, fieldnames=list(fields), delimiter=";",
                                 extrasaction="ignore")
        self._w.writeheader()

    def __call__(self, line: dict) -> None:
        self._w.writerow(line)
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
