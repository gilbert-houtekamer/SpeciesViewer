"""SQLite-backed read cache for the master collection (Plan B).

The two big master CSVs (`data_pictures.csv` ~85k rows, `data_files.csv`
~178k rows) are still the canonical store in Dropbox so they stay
human-readable, syncable across machines, and unaffected by SQLite's
internal-consistency invariants. This module mirrors them into a local
per-machine SQLite file (NEVER in Dropbox — Dropbox + SQLite mid-write =
corruption risk) and offers indexed lookups against it.

Architecture:
  - DB lives outside Dropbox under per-OS app-data dirs:
      Windows  %LOCALAPPDATA%/SpeciesViewer/db/master.db
      macOS    ~/Library/Application Support/SpeciesViewer/db/master.db
      Linux    $XDG_DATA_HOME or ~/.local/share/SpeciesViewer/db/master.db
  - `ensure_fresh()` checks the master root + each CSV's mtime against the
    `meta` table; if anything differs the DB is rebuilt from CSV. The
    rebuild is the same ~2s pass `FilePicture.readMaster()` used to do at
    every launch — now you pay it only when the CSVs actually change.
  - Query helpers return dicts shaped exactly like `Pictures.read_pictures`
    and `Files.read_file` produce (Files column parsed to list[str]) so the
    rating provider can keep its existing row-walking code unchanged.

Phase 1 is READ-only; writes still flow through the existing CSV path.
Phase 2 will add DB-first writes + a queued CSV rewriter (see memory
project-master-viewer).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from engine.csv_columns import PICTURE_FIELDS, DATA_FILE_FIELDS

logger = logging.getLogger("speciesviewer")


def _diag(msg: str) -> None:
    """Diagnostic print straight to stderr. Bypasses the speciesviewer logger
    so we still see what happens during splash-time warming, BEFORE
    init_logger() attaches the file handler at t≈2s. Visible in the VS Code
    debug console / terminal."""
    print(f"[master_db] {msg}", file=sys.stderr, flush=True)


# Bump this if the schema changes; mismatch triggers a full rebuild.
SCHEMA_VERSION = 1

# Column that's typed as INTEGER (we sort on Rate; rest stay TEXT to mirror CSV).
_INT_COLS_PICTURES = {"Rate"}


# ── DB path resolution ─────────────────────────────────────────────────────────

def _app_data_dir() -> Path:
    """Per-OS user app-data directory (NOT in Dropbox)."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SpeciesViewer"
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "SpeciesViewer"


def db_path() -> Path:
    return _app_data_dir() / "db" / "master.db"


# ── DDL helpers ────────────────────────────────────────────────────────────────

def _quoted(name: str) -> str:
    """SQLite identifier quoting — CSV columns contain ':' so we always quote."""
    return '"' + name.replace('"', '""') + '"'


def _col_def(name: str, *, pk: bool = False) -> str:
    typ = "INTEGER" if name in _INT_COLS_PICTURES else "TEXT"
    suffix = " PRIMARY KEY" if pk else ""
    return f"{_quoted(name)} {typ}{suffix}"


def _create_pictures_sql() -> str:
    cols = [_col_def("UniquePictureName", pk=True)] + [
        _col_def(c) for c in PICTURE_FIELDS if c != "UniquePictureName"
    ]
    return f"CREATE TABLE pictures (\n  " + ",\n  ".join(cols) + "\n)"


def _create_files_sql() -> str:
    cols = [_col_def("File:FileName", pk=True)] + [
        _col_def(c) for c in DATA_FILE_FIELDS if c != "File:FileName"
    ]
    return f"CREATE TABLE files (\n  " + ",\n  ".join(cols) + "\n)"


_PIC_COLS_SQL = ", ".join(_quoted(c) for c in PICTURE_FIELDS)
_PIC_PLACEHOLDERS = ", ".join(["?"] * len(PICTURE_FIELDS))
_FILE_COLS_SQL = ", ".join(_quoted(c) for c in DATA_FILE_FIELDS)
_FILE_PLACEHOLDERS = ", ".join(["?"] * len(DATA_FILE_FIELDS))


# ── Files-column codec (matches Pictures.read_pictures' repr-list parsing) ────

def _parse_files_csv(raw: str) -> list[str]:
    """The CSV stores Files as a Python-repr-style list, e.g.
    `['foo.nef', 'foo.jpg']`. Same parse as `Pictures.read_pictures`."""
    if not raw or raw == "[]":
        return []
    try:
        inner = raw[1:-1].replace("'", "")  # strip [] and quote chars
        parts = [v.lstrip() for v in inner.split(",")]
        out: list[str] = []
        for p in parts:
            if p and p not in out:
                out.append(p)
        return out
    except Exception:
        return []


def _coerce_rate(raw) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# ── MasterDB ──────────────────────────────────────────────────────────────────

class MasterDB:
    """Indexed read cache for the master collection. Rebuilds from the master
    CSVs when their mtime no longer matches what's recorded in the DB's `meta`
    table — at first run, after a master pipeline, or when SCHEMA_VERSION
    changes."""

    # Class-level lock: serialise rebuilds across threads (splash-time warm-up
    # vs Rating Editor lazy-open). Each thread still creates its own MasterDB
    # instance with its own sqlite3 connection — the lock just stops two
    # threads from both seeing "stale" and rebuilding in parallel.
    _build_lock = threading.Lock()

    def __init__(self, master_root: str | Path):
        self.master_root = str(master_root)
        self._db_path = db_path()
        self._con: sqlite3.Connection | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        if self._con is None:
            _diag(f"_connect: mkdir {self._db_path.parent}")
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            _diag(f"_connect: sqlite3.connect({self._db_path})")
            con = sqlite3.connect(self._db_path)
            con.row_factory = sqlite3.Row
            # WAL gives us fast crash-safe writes (Phase 2). Harmless for reads.
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA synchronous = NORMAL")
            self._con = con
            _diag(f"_connect: ok (WAL mode)")
        return self._con

    def close(self) -> None:
        if self._con is not None:
            try:
                self._con.close()
            finally:
                self._con = None

    # ── CSV paths derived from master root ────────────────────────────────
    def _pictures_csv(self) -> Path:
        return Path(self.master_root) / "metadata" / "active" / "data_pictures.csv"

    def _files_csv(self) -> Path:
        return Path(self.master_root) / "metadata" / "active" / "data_files.csv"

    # ── staleness ─────────────────────────────────────────────────────────
    def _meta(self, con: sqlite3.Connection) -> dict[str, str]:
        try:
            rows = con.execute("SELECT key, value FROM meta").fetchall()
        except sqlite3.OperationalError:
            return {}
        return {r["key"]: r["value"] for r in rows}

    def _csv_mtime(self, p: Path) -> str:
        return f"{p.stat().st_mtime:.6f}" if p.exists() else ""

    def is_stale(self) -> bool:
        pics_csv, files_csv = self._pictures_csv(), self._files_csv()
        if not pics_csv.exists() or not files_csv.exists():
            return False  # nothing to (re)build from
        con = self._connect()
        meta = self._meta(con)
        if meta.get("schema_version") != str(SCHEMA_VERSION):
            return True
        if meta.get("master_root") != self.master_root:
            return True
        if meta.get("pictures_csv_mtime") != self._csv_mtime(pics_csv):
            return True
        if meta.get("files_csv_mtime") != self._csv_mtime(files_csv):
            return True
        return False

    def ensure_fresh(self, *, progress_cb: Callable[[str], None] | None = None) -> bool:
        """Build the DB if missing, rebuild if stale, otherwise just open it.
        Returns True iff a rebuild was performed.

        Thread-safe against concurrent rebuilds — the class-level _build_lock
        serialises competing rebuilds (splash-time warm-up racing the
        Rating Editor's lazy open). The double-check inside the lock means
        the loser of the race just returns False instead of rebuilding twice.
        """
        _diag(f"ensure_fresh: master_root={self.master_root!r}")
        pics_csv, files_csv = self._pictures_csv(), self._files_csv()
        _diag(f"ensure_fresh: pics_csv exists={pics_csv.exists()} ({pics_csv})")
        _diag(f"ensure_fresh: files_csv exists={files_csv.exists()} ({files_csv})")
        if not pics_csv.exists() or not files_csv.exists():
            raise FileNotFoundError(
                f"Master CSVs not found under {self.master_root}/metadata/active/")
        stale = self.is_stale()
        _diag(f"ensure_fresh: is_stale={stale}")
        if not stale:
            return False
        with MasterDB._build_lock:
            # Another thread may have rebuilt while we were waiting.
            if not self.is_stale():
                _diag("ensure_fresh: another thread rebuilt while we waited")
                return False
            _diag("ensure_fresh: starting rebuild")
            self.rebuild(progress_cb=progress_cb)
            _diag("ensure_fresh: rebuild complete")
            return True

    # ── rebuild ───────────────────────────────────────────────────────────
    def rebuild(self, *, progress_cb: Callable[[str], None] | None = None) -> None:
        """Drop and repopulate every table from the master CSVs. Wrapped in a
        single transaction — a crash mid-rebuild leaves the previous DB
        intact."""
        import csv

        def note(msg: str) -> None:
            logger.info(msg)
            _diag(f"rebuild: {msg}")
            if progress_cb:
                progress_cb(msg)

        con = self._connect()
        t0 = time.time()
        note("Rebuilding master DB from CSVs…")
        with con:  # implicit transaction
            con.execute("DROP TABLE IF EXISTS pictures")
            con.execute("DROP TABLE IF EXISTS files")
            con.execute("DROP TABLE IF EXISTS meta")
            con.execute(_create_pictures_sql())
            con.execute(_create_files_sql())
            con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

            # Pictures
            pics_csv = self._pictures_csv()
            note(f"  loading {pics_csv.name}…")
            picture_rows = []
            with open(pics_csv, "r", newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter=";")
                for row in reader:
                    values = []
                    for col in PICTURE_FIELDS:
                        raw = row.get(col, "")
                        if col == "Files":
                            # store as JSON for clean round-trip
                            values.append(json.dumps(_parse_files_csv(raw)))
                        elif col == "Rate":
                            values.append(_coerce_rate(raw))
                        else:
                            values.append(raw)
                    picture_rows.append(values)
            con.executemany(
                f"INSERT OR REPLACE INTO pictures ({_PIC_COLS_SQL}) "
                f"VALUES ({_PIC_PLACEHOLDERS})", picture_rows)

            # Files
            files_csv = self._files_csv()
            note(f"  loading {files_csv.name}…")
            file_rows = []
            with open(files_csv, "r", newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh, delimiter=";")
                for row in reader:
                    file_rows.append([row.get(c, "") for c in DATA_FILE_FIELDS])
            con.executemany(
                f"INSERT OR REPLACE INTO files ({_FILE_COLS_SQL}) "
                f"VALUES ({_FILE_PLACEHOLDERS})", file_rows)

            # Indexes (created after bulk insert = much faster than per-row)
            note("  indexing…")
            con.execute('CREATE INDEX idx_pic_sci ON pictures("WetSoortNaam")')
            con.execute('CREATE INDEX idx_pic_rate ON pictures("Rate" DESC)')
            con.execute('CREATE INDEX idx_files_upn ON files("UniquePictureName")')

            con.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                [("schema_version", str(SCHEMA_VERSION)),
                 ("master_root", self.master_root),
                 ("pictures_csv_mtime", self._csv_mtime(pics_csv)),
                 ("files_csv_mtime", self._csv_mtime(files_csv)),
                 ("built_at", f"{time.time():.0f}")])

        dt = time.time() - t0
        n_pics = con.execute("SELECT COUNT(*) FROM pictures").fetchone()[0]
        n_files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        note(f"Master DB built: {n_pics} pictures, {n_files} files in {dt:.1f}s "
             f"({self._db_path})")

    # ── queries (return dicts shaped like the CSV readers do) ─────────────
    def picture_count(self) -> int:
        con = self._connect()
        try:
            return con.execute("SELECT COUNT(*) FROM pictures").fetchone()[0]
        except sqlite3.OperationalError:
            return 0

    def _picture_row_to_dict(self, row: sqlite3.Row) -> dict:
        d = {k: row[k] for k in row.keys()}
        # restore the parsed Files list shape the rating provider expects
        raw = d.get("Files")
        if isinstance(raw, str) and raw:
            try:
                d["Files"] = json.loads(raw)
            except Exception:
                d["Files"] = []
        else:
            d["Files"] = []
        # Rate comes back as int (or None); the rating provider tolerates
        # str/int via _safe_int, but mirror the CSV "" for empty so downstream
        # comparisons stay stable.
        if d.get("Rate") is None:
            d["Rate"] = ""
        else:
            d["Rate"] = str(d["Rate"])
        return d

    def pictures_for_species(self, wet_soort_naam: str) -> list[dict]:
        """All master pictures with this scientific name (case-insensitive)."""
        if not wet_soort_naam:
            return []
        con = self._connect()
        rows = con.execute(
            'SELECT * FROM pictures WHERE LOWER(TRIM("WetSoortNaam")) = ?',
            (wet_soort_naam.strip().lower(),)).fetchall()
        return [self._picture_row_to_dict(r) for r in rows]

    def picture(self, upn: str) -> dict | None:
        con = self._connect()
        row = con.execute(
            'SELECT * FROM pictures WHERE "UniquePictureName" = ?',
            (upn,)).fetchone()
        return self._picture_row_to_dict(row) if row else None

    def files_for_upns(self, upns: Iterable[str]) -> dict[str, dict]:
        """{File:FileName: row_dict} for every file row whose UPN is in `upns`."""
        upns = list(upns)
        if not upns:
            return {}
        con = self._connect()
        out: dict[str, dict] = {}
        # SQLite has a 999-default param limit; chunk to be safe.
        CHUNK = 500
        for i in range(0, len(upns), CHUNK):
            chunk = upns[i:i + CHUNK]
            placeholders = ", ".join(["?"] * len(chunk))
            rows = con.execute(
                f'SELECT * FROM files WHERE "UniquePictureName" IN ({placeholders})',
                chunk).fetchall()
            for r in rows:
                d = {k: r[k] for k in r.keys()}
                out[d["File:FileName"]] = d
        return out
