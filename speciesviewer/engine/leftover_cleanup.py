"""Move the master's leftover work folders aside (backlog 1.23 step 3).

ONE JOB: find the folders in the master that nothing reads any more, move them
into `<master>/admin/.trash/<stamp>/` with a manifest, and — on request — put
them back or empty that trash.

What counts as left over (and only this):
  frozen copies   metadata/review/<run>/frozen/biota — the pages + JPG copies a
                  To Master / Add to Master run builds and copies into the master.
                  The freeze step builds it anew on every run.
  dry runs        admin/status/review_* and preview_* — old master reviews; the
                  NEWEST review_ folder stays (it may be the one you are applying).
  temp folders    metadata/review/sv_droppool_* (Add Pictures to Master) and
                  admin/temp.

INVARIANTS
- A run whose commit did not finish (its statemachine.ini says frozen but not
  committed) is never touched: its frozen folder is what the commit copies from.
- A folder changed in the last day is never touched (a window may be using it).
- The run folders' own lists stay: merging the same trip again reads
  review/<run>/species.csv.
- Nothing is deleted by apply(): it MOVES, on the same disk, and writes a
  manifest line per folder. restore() puts back exactly those; empty() is the
  only step that deletes, and only inside admin/.trash.
"""
from __future__ import annotations

import configparser
import csv
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("speciesviewer")

TRASH = ("admin", ".trash")
MANIFEST = "manifest.csv"
_RECENT_S = 24 * 3600


def trash_root(master_root) -> Path:
    return Path(master_root).joinpath(*TRASH)


def _folder_size(p: Path) -> tuple[int, int, float]:
    """(files, bytes, newest file mtime) of a folder tree. Only the files'
    dates count: a folder's own date moves when Dropbox or a scan touches it."""
    n = size = 0
    newest = 0.0
    for dirpath, _dirs, files in os.walk(p):
        for f in files:
            try:
                st = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            n += 1
            size += st.st_size
            newest = max(newest, st.st_mtime)
    return n, size, newest


def _commit_unfinished(run_dir: Path) -> bool:
    ini = run_dir / "statemachine.ini"
    if not ini.is_file():
        return False
    from utils.ini_io import read_ini
    cfg = configparser.ConfigParser()
    try:
        read_ini(cfg, ini)
    except Exception:
        return True                          # unreadable: treat as unfinished
    frozen = cfg.get("state", "sv6freeze", fallback="False").strip() == "True"
    committed = cfg.get("state", "sv7writecommit", fallback="False").strip() == "True"
    return frozen and not committed


def plan(master_root, now: float | None = None) -> tuple[list[dict], list[str]]:
    """([{path, kind, files, bytes}], [reasons something was kept]). Reads only."""
    root = Path(master_root)
    now = now or time.time()
    cands: list[tuple[Path, str]] = []
    kept: list[str] = []

    review = root / "metadata" / "review"
    if review.is_dir():
        for run in sorted(p for p in review.iterdir() if p.is_dir()):
            if run.name.lower().startswith("sv_droppool_"):
                cands.append((run, "temp folder"))
                continue
            if run.name.lower() == "master_rebuild":
                continue                     # Rebuild Websites' own work folder
            fb = run / "frozen" / "biota"
            if not fb.is_dir():
                continue
            if _commit_unfinished(run):
                kept.append(f"{run.name}: its commit did not finish")
                continue
            cands.append((fb, "frozen copy"))

    status = root / "admin" / "status"
    if status.is_dir():
        reviews = sorted(p for p in status.iterdir()
                         if p.is_dir() and p.name.startswith("review_"))
        if reviews:
            kept.append(f"{reviews[-1].name}: the newest review")
        for p in reviews[:-1]:
            cands.append((p, "dry run"))
        for p in sorted(status.iterdir()):
            if p.is_dir() and p.name.startswith("preview_"):
                cands.append((p, "dry run"))

    temp = root / "admin" / "temp"
    if temp.is_dir() and any(temp.iterdir()):
        cands.append((temp, "temp folder"))

    out = []
    for path, kind in cands:
        n, size, newest = _folder_size(path)
        if now - newest < _RECENT_S:
            kept.append(f"{path.relative_to(root).as_posix()}: changed in the last day")
            continue
        out.append({"path": path, "kind": kind, "files": n, "bytes": size})
    return out, kept


def apply(master_root, items: list[dict]) -> tuple[Path, int, int]:
    """Move these folders into admin/.trash/<stamp>/, keeping their path under
    the master. Returns (trash folder, folders moved, bytes)."""
    root = Path(master_root)
    dest_root = trash_root(root) / datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_root.mkdir(parents=True, exist_ok=True)
    moved = size = 0
    with open(dest_root / MANIFEST, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["original", "trashed", "kind", "files", "bytes"])
        for it in items:
            src = Path(it["path"])
            rel = src.relative_to(root)
            dst = dest_root / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)        # same disk: a rename, nothing copied
            except OSError as e:
                logger.warning(f"leftovers: could not move {rel}: {e}")
                continue
            w.writerow([rel.as_posix(), dst.relative_to(root).as_posix(),
                        it["kind"], it["files"], it["bytes"]])
            fh.flush()
            moved += 1
            size += it["bytes"]
            logger.info(f"leftovers: moved {rel} to {dst.relative_to(root)}")
    return dest_root, moved, size


def batches(master_root) -> list[Path]:
    root = trash_root(master_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / MANIFEST).is_file())


def restore(master_root, batch: Path) -> tuple[int, list[str]]:
    """Put back every folder of one batch. A place that is taken again is left
    alone (reported), never overwritten."""
    root = Path(master_root)
    done, problems = 0, []
    with open(Path(batch) / MANIFEST, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    for r in rows:
        src, dst = root / r["trashed"], root / r["original"]
        if not src.exists():
            continue
        if dst.exists():
            problems.append(f"{r['original']}: already exists again, left in the trash")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        done += 1
    return done, problems


def empty(master_root) -> int:
    """Delete admin/.trash for good. Returns the bytes freed."""
    root = trash_root(master_root)
    if not root.is_dir():
        return 0
    _n, size, _t = _folder_size(root)
    shutil.rmtree(root)
    return size
