"""Rotation for the big list copies that were never pruned (backlog 1.23 step 2).

ONE JOB: in the two folders where SpeciesViewer drops a plain copy of the
master's lists before changing them — `metadata/active/backup/` (one per day a
trip is committed) and `admin/status/backup/` (one before every repair) — keep
only the newest few copies of `data_files` and `data_pictures`, and say exactly
which files go.

INVARIANTS
- Only files whose name holds `data_files` or `data_pictures` AND a date are
  ever candidates. Everything else in those folders stays: the dated
  `*_species.csv` copies are the lifer baseline of old trips
  (engine/species_baseline.py), and they are small.
- Per folder and per kind, the newest KEEP_COPIES stay (by the date in the name).
- Never the daily backup folders (`<day>/`, `pre-restore-*/`): only plain files
  directly in the folder are looked at.
- EVIDENCE: a copy dated inside EVIDENCE_WINDOW (the June/July 2026 ghost-row
  loss, whose recovery was never finished) is only ever a candidate once a
  verified gzipped copy of it sits in the archive folder (archive_evidence()).
- plan() reads only; apply() deletes exactly the plan it is given. These are
  copies of copies — the daily gzipped backup (db/master_health.py, 14 days)
  holds the same lists — so, like that rotation, they are removed outright,
  not moved to a trash folder that would keep them in Dropbox.
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger("speciesviewer")

KEEP_COPIES = 3

# The copies that document the June/July 2026 ghost-row loss: from the last state
# before the migration wave (06-19) to the investigation (07-24). Inclusive dates.
EVIDENCE_WINDOW = ("20260619", "20260724")
EVIDENCE_DIR = ("admin", "archive", "ghost_rows_2026")

_KINDS = ("data_files", "data_pictures")
# 2026-06-20_… · 20261018-114442_… · data_files.20260619-231616.csv
_DATE_RE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})(?:[-_]?(\d{6}))?")


def copy_folders(master_root) -> list[Path]:
    root = Path(master_root)
    return [root / "metadata" / "active" / "backup",
            root / "admin" / "status" / "backup"]


def archive_dir(master_root) -> Path:
    return Path(master_root).joinpath(*EVIDENCE_DIR)


def _in_window(stamp: str) -> bool:
    return EVIDENCE_WINDOW[0] <= stamp[:8] <= EVIDENCE_WINDOW[1]


def _archived(master_root, path: Path) -> bool:
    return (archive_dir(master_root) / (path.name + ".gz")).is_file()


def _sha256(fh) -> str:
    h = hashlib.sha256()
    for block in iter(lambda: fh.read(1 << 20), b""):
        h.update(block)
    return h.hexdigest()


def evidence_copies(master_root) -> list[Path]:
    """The list copies dated inside EVIDENCE_WINDOW, in both folders."""
    out = []
    for folder in copy_folders(master_root):
        if not folder.is_dir():
            continue
        for entry in os.scandir(folder):
            if entry.is_file():
                kind, stamp = _kind_and_stamp(entry.name)
                if kind and _in_window(stamp):
                    out.append(Path(entry.path))
    return sorted(out, key=lambda p: p.name)


def archive_evidence(master_root, progress=print) -> tuple[int, int, list[str]]:
    """Gzip every evidence copy into the archive folder and VERIFY it (the
    unpacked archive must hash the same as the original). Already-archived ones
    are skipped. Returns (archived now, archive bytes, problems).
    Never removes anything: a failed or mismatching archive is deleted again,
    so the original stays protected."""
    adir = archive_dir(master_root)
    adir.mkdir(parents=True, exist_ok=True)
    done = size = 0
    problems = []
    for src in evidence_copies(master_root):
        gz = adir / (src.name + ".gz")
        if gz.is_file():
            continue
        tmp = adir / (src.name + ".gz.part")
        try:
            with open(src, "rb") as fi, gzip.open(tmp, "wb", compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo, 1 << 20)
            with open(src, "rb") as a, gzip.open(tmp, "rb") as b:
                if _sha256(a) != _sha256(b):
                    raise OSError("the archive does not match the original")
            tmp.replace(gz)
            done += 1
            size += gz.stat().st_size
            progress(f"archived {src.name}  ({gz.stat().st_size / 1e6:,.0f} MB)")
        except OSError as e:
            problems.append(f"{src.name}: {e}")
            try:
                tmp.unlink()
            except OSError:
                pass
    return done, size, problems


def _kind_and_stamp(name: str):
    """('data_files', '20261018114442') for a list copy, else (None, None)."""
    low = name.lower()
    kind = next((k for k in _KINDS if k in low), None)
    if kind is None or not low.endswith(".csv"):
        return None, None
    m = _DATE_RE.search(name)
    if not m:
        return None, None
    return kind, "".join(g or "000000" for g in m.groups())


def plan(master_root, keep: int = KEEP_COPIES) -> list[dict]:
    """The copies that would go: [{path, bytes, kind, folder}], oldest first."""
    out = []
    for folder in copy_folders(master_root):
        if not folder.is_dir():
            continue
        by_kind: dict[str, list] = {}
        for entry in os.scandir(folder):
            if not entry.is_file():
                continue
            kind, stamp = _kind_and_stamp(entry.name)
            if kind:
                by_kind.setdefault(kind, []).append((stamp, entry))
        protected = {e.path for items in by_kind.values() for st, e in items
                     if _in_window(st) and not _archived(master_root, Path(e.path))}
        for kind, items in by_kind.items():
            items.sort(key=lambda t: (t[0], t[1].name), reverse=True)   # newest first
            for _stamp, entry in reversed(items[keep:]):
                if entry.path in protected:
                    continue                       # evidence not yet archived
                out.append({"path": Path(entry.path), "bytes": entry.stat().st_size,
                            "kind": kind, "folder": folder})
    return out


def apply(items: list[dict]) -> tuple[int, int]:
    """Delete exactly these files. Returns (files removed, bytes freed).
    A file that cannot be removed (Dropbox holding it) is logged and left for
    the next run."""
    n = freed = 0
    for it in items:
        try:
            Path(it["path"]).unlink()
            n += 1
            freed += it["bytes"]
            logger.info(f"backup copies: removed {it['path']}")
        except FileNotFoundError:
            continue
        except OSError as e:
            logger.warning(f"backup copies: could not remove {it['path']}: {e}")
    return n, freed


def prune(master_root, keep: int = KEEP_COPIES) -> tuple[int, int]:
    """plan + apply, for the daily rotation."""
    items = plan(master_root, keep)
    if not items:
        return 0, 0
    n, freed = apply(items)
    if n:
        logger.info(f"backup copies: removed {n} old list cop{'y' if n == 1 else 'ies'}, "
                    f"{freed / 1e6:,.0f} MB (kept the newest {keep} of each).")
    return n, freed
