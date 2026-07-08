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

from engine.csv_columns import PICTURE_FIELDS, DATA_FILE_FIELDS, SPECIES_FIELDS
from engine.handling import resolve_path

logger = logging.getLogger("speciesviewer")


def _diag(msg: str) -> None:
    """Diagnostic print straight to stderr. Bypasses the speciesviewer logger
    so we still see what happens during splash-time warming, BEFORE
    init_logger() attaches the file handler at t≈2s. Visible in the VS Code
    debug console / terminal. Only when 'Enable debug functionality' is on."""
    try:
        from utils.config import debug_enabled
        if not debug_enabled():
            return
    except Exception:
        return
    print(f"[master_db] {msg}", file=sys.stderr, flush=True)


# Bump this if the schema changes; mismatch triggers a full rebuild.
# v2: added the `species` table (mirrors species.csv) for master add/delete.
SCHEMA_VERSION = 2

# Column that's typed as INTEGER (we sort on Rate; rest stay TEXT to mirror CSV).
_INT_COLS_PICTURES = {"Rate"}

# species.csv columns that need special handling in the DB mirror.
_SPEC_NUM_COLS = {
    "currentHighestRating", "iSpeciesThisRunCount", "iSpeciesCollectionCount",
    "iPictureThisRunCount", "iPictureCollectionCount",
    "iPicturesWithJPGsThisRun", "iPicturesWithJPGsCollection",
}
_SPEC_LIST_COLS = {"Leaves", "JPGmap"}  # stored as JSON, written back as ['a', 'b']


# ── DB path resolution ─────────────────────────────────────────────────────────

def _app_data_dir() -> Path:
    """Per-OS user app-data directory (NOT in Dropbox).

    Canonical implementation lives in utils.cache_paths so the caches and the DB
    share one definition; kept here under the historical name for callers.
    """
    from utils.cache_paths import app_data_dir
    return app_data_dir()


def db_path() -> Path:
    return _app_data_dir() / "db" / "master.db"


def wal_jsonl_path() -> Path:
    """Append-only pending-CSV-writes log, sibling of master.db (Phase 2).

    NEVER in Dropbox — it is per-machine pending state tied to the local DB.
    An entry is appended (intent-first, fsync'd) before each master write hits
    SQLite; the checkpoint worker rewrites the affected master CSV from the DB
    and truncates the log once the canonical CSV has caught up."""
    return _app_data_dir() / "db" / "master.wal.jsonl"


def _opt_str(v) -> str | None:
    return None if v is None else str(v)


# ── Phase-2 config knobs (read lazily so master_db stays import-light) ──────────

def _cfg_int(key: str, default: int) -> int:
    try:
        from utils.config import _config
        return _config.getint("masterdb", key, fallback=default)
    except Exception:
        return default


def pending_warn_seconds() -> int:
    return _cfg_int("pending_warn_seconds", 30)


def _checkpoint_debounce_seconds() -> float:
    return float(_cfg_int("checkpoint_debounce_seconds", 2))


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


def _spec_col_def(name: str) -> str:
    typ = "INTEGER" if name in _SPEC_NUM_COLS else "TEXT"
    pk = " PRIMARY KEY" if name == "ScientificName" else ""
    return f"{_quoted(name)} {typ}{pk}"


def _create_species_sql() -> str:
    cols = [_spec_col_def("ScientificName")] + [
        _spec_col_def(c) for c in SPECIES_FIELDS if c != "ScientificName"
    ]
    return f"CREATE TABLE species (\n  " + ",\n  ".join(cols) + "\n)"


_PIC_COLS_SQL = ", ".join(_quoted(c) for c in PICTURE_FIELDS)
_PIC_PLACEHOLDERS = ", ".join(["?"] * len(PICTURE_FIELDS))
_FILE_COLS_SQL = ", ".join(_quoted(c) for c in DATA_FILE_FIELDS)
_FILE_PLACEHOLDERS = ", ".join(["?"] * len(DATA_FILE_FIELDS))
_SPEC_COLS_SQL = ", ".join(_quoted(c) for c in SPECIES_FIELDS)
_SPEC_PLACEHOLDERS = ", ".join(["?"] * len(SPECIES_FIELDS))


def _coerce_int(raw) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _area(image_size: str) -> int:
    """Pixel area from an 'ImageSize' string like '6036 4020' or '799x533'.
    Used to decide whether an added crop JPG is the larger/better one. Returns 0
    when it can't be parsed (so anything real beats an empty value)."""
    import re as _re
    nums = _re.findall(r"\d+", image_size or "")
    if len(nums) >= 2:
        return int(nums[0]) * int(nums[1])
    return 0


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
    # Serialises checkpoint() + jsonl truncate across the GUI flush and the
    # background worker (each has its own sqlite connection).
    _checkpoint_lock = threading.Lock()

    def __init__(self, master_root: str | Path):
        self.master_root = str(master_root)
        self._db_path = db_path()
        self._jsonl_path = wal_jsonl_path()
        self._con: sqlite3.Connection | None = None
        self._seq: int | None = None          # next-seq cache (append side)
        self._append_lock = threading.Lock()  # guards _next_seq + append

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

    def _species_csv(self) -> Path:
        return Path(self.master_root) / "metadata" / "active" / "species.csv"

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
        # species.csv is optional (older masters may predate it); only treat a
        # drift as stale when the file exists.
        spec_csv = self._species_csv()
        if spec_csv.exists() and meta.get("species_csv_mtime") != self._csv_mtime(spec_csv):
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
            con.execute("DROP TABLE IF EXISTS species")
            con.execute("DROP TABLE IF EXISTS meta")
            con.execute(_create_pictures_sql())
            con.execute(_create_files_sql())
            con.execute(_create_species_sql())
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
                    # Resolve root-relative paths written by the SmallMaster copy tool
                    for col in ("WindowsFileName", "File:Directory"):
                        if col in row:
                            row[col] = resolve_path(row[col], self.master_root)
                    file_rows.append([row.get(c, "") for c in DATA_FILE_FIELDS])
            con.executemany(
                f"INSERT OR REPLACE INTO files ({_FILE_COLS_SQL}) "
                f"VALUES ({_FILE_PLACEHOLDERS})", file_rows)

            # Species (species.csv — optional on very old masters)
            spec_csv = self._species_csv()
            spec_mtime = ""
            if spec_csv.exists():
                note(f"  loading {spec_csv.name}…")
                spec_rows = []
                with open(spec_csv, "r", newline="", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh, delimiter=";")
                    for row in reader:
                        values = []
                        for col in SPECIES_FIELDS:
                            raw = row.get(col, "")
                            if col in _SPEC_LIST_COLS:
                                values.append(json.dumps(_parse_files_csv(raw)))
                            elif col in _SPEC_NUM_COLS:
                                values.append(_coerce_int(raw))
                            else:
                                values.append(raw)
                        spec_rows.append(values)
                con.executemany(
                    f"INSERT OR REPLACE INTO species ({_SPEC_COLS_SQL}) "
                    f"VALUES ({_SPEC_PLACEHOLDERS})", spec_rows)
                spec_mtime = self._csv_mtime(spec_csv)

            # Indexes (created after bulk insert = much faster than per-row)
            note("  indexing…")
            con.execute('CREATE INDEX idx_pic_sci ON pictures("WetSoortNaam")')
            con.execute('CREATE INDEX idx_pic_rate ON pictures("Rate" DESC)')
            con.execute('CREATE INDEX idx_files_upn ON files("UniquePictureName")')
            con.execute('CREATE INDEX idx_spec_up ON species("Up")')

            con.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                [("schema_version", str(SCHEMA_VERSION)),
                 ("master_root", self.master_root),
                 ("pictures_csv_mtime", self._csv_mtime(pics_csv)),
                 ("files_csv_mtime", self._csv_mtime(files_csv)),
                 ("species_csv_mtime", spec_mtime),
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

    def species_picker_rows(self) -> list[dict]:
        """Distinct species that actually have master pictures, for the Master
        Species Viewer's selector. Derived from the pictures table (always
        present, so every listed species has viewable content) rather than the
        optional species.csv mirror — and it is the master's own list, not a
        trip's SpeciesCollection. Returns
        [{"sci":.., "nl":.., "en":.., "n":..}] sorted by scientific name; nl/en
        are a representative (MAX) value per species so a blank on one picture
        doesn't win over a filled one."""
        con = self._connect()
        try:
            rows = con.execute(
                'SELECT TRIM("WetSoortNaam") AS sci, '
                '       MAX("NLSoortNaam")  AS nl, '
                '       MAX("EngSoortNaam") AS en, '
                '       COUNT(*)            AS n '
                'FROM pictures '
                'WHERE TRIM("WetSoortNaam") <> "" '
                'GROUP BY LOWER(TRIM("WetSoortNaam")) '
                'ORDER BY sci COLLATE NOCASE').fetchall()
        except sqlite3.OperationalError:
            return []
        return [{"sci": r["sci"], "nl": r["nl"] or "", "en": r["en"] or "",
                 "n": r["n"]} for r in rows]

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

    # ── Phase 2: DB-first writes + WAL-jsonl queue ────────────────────────────
    #
    # Write path (called on the GUI thread by the Rating Editor):
    #   apply_rating() appends a jsonl entry (intent-first, fsync), applies the
    #   field UPDATEs to SQLite, then nudges the background checkpoint worker.
    # The worker (own connection, see MasterCheckpointWorker) debounces, then
    # checkpoint()s: rewrite the affected master CSV(s) from the DB and truncate
    # the log. Crash recovery (recover_if_needed) replays the log on launch.

    def _read_jsonl(self) -> list[dict]:
        p = self._jsonl_path
        if not p.exists():
            return []
        out: list[dict] = []
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    logger.warning("master checkpoint: skipping corrupt jsonl line")
        return out

    def _next_seq(self) -> int:
        if self._seq is None:
            self._seq = max((e.get("seq", 0) for e in self._read_jsonl()), default=0)
        self._seq += 1
        return self._seq

    def _append_jsonl(self, entry: dict) -> None:
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._jsonl_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    @staticmethod
    def _replay_picture(con: sqlite3.Connection, upn: str, fields: dict) -> None:
        sets, vals = [], []
        for col in ("Rating", "Label", "Rate"):
            if fields.get(col) is None:
                continue
            sets.append(f"{_quoted(col)}=?")
            vals.append(_coerce_rate(fields[col]) if col == "Rate" else fields[col])
        if not sets:
            return
        vals.append(upn)
        con.execute(
            f'UPDATE pictures SET {", ".join(sets)} WHERE "UniquePictureName"=?', vals)

    @staticmethod
    def _replay_file(con: sqlite3.Connection, fname: str, fields: dict) -> None:
        sets, vals = [], []
        for col in ("XMP:Rating", "XMP:Label"):
            if fields.get(col) is None:
                continue
            sets.append(f"{_quoted(col)}=?")
            vals.append(fields[col])
        if not sets:
            return
        vals.append(fname)
        con.execute(
            f'UPDATE files SET {", ".join(sets)} WHERE "File:FileName"=?', vals)

    def apply_rating(self, upn: str, *, rating=None, label=None, rate=None,
                     file_updates: dict | None = None) -> None:
        """Durably record a master re-rating. Order matters (WAL discipline):
        jsonl intent first (fsync), then the SQLite UPDATE, then nudge the
        checkpoint worker that rewrites the canonical CSV. file_updates maps
        File:FileName -> {"XMP:Rating": str|None, "XMP:Label": str|None}."""
        file_updates = file_updates or {}
        entry = {
            "ts": time.time(),
            "upn": upn,
            "pictures": {"Rating": _opt_str(rating), "Label": _opt_str(label),
                         "Rate": _opt_str(rate)},
            "files": {fn: {"XMP:Rating": _opt_str(v.get("XMP:Rating")),
                           "XMP:Label": _opt_str(v.get("XMP:Label"))}
                      for fn, v in file_updates.items()},
        }
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        con = self._connect()
        with con:
            self._replay_picture(con, upn, entry["pictures"])
            for fn, fields in entry["files"].items():
                self._replay_file(con, fn, fields)
        MasterCheckpointWorker.get(self.master_root).notify()

    # ── generic row mutators (used by add/delete ops) ─────────────────────────
    @staticmethod
    def _set_picture_fields(con: sqlite3.Connection, upn: str, fields: dict) -> None:
        sets, vals = [], []
        for col, v in fields.items():
            sets.append(f"{_quoted(col)}=?")
            if col == "Files":
                vals.append(v if isinstance(v, str) else json.dumps(v or []))
            elif col == "Rate":
                vals.append(_coerce_rate(v))
            else:
                vals.append(v)
        if not sets:
            return
        vals.append(upn)
        con.execute(
            f'UPDATE pictures SET {", ".join(sets)} WHERE "UniquePictureName"=?', vals)

    @staticmethod
    def _set_file_fields(con: sqlite3.Connection, fname: str, fields: dict) -> None:
        sets, vals = [], []
        for col, v in fields.items():
            sets.append(f"{_quoted(col)}=?")
            vals.append(v)
        if not sets:
            return
        vals.append(fname)
        con.execute(
            f'UPDATE files SET {", ".join(sets)} WHERE "File:FileName"=?', vals)

    @staticmethod
    def _insert_file_row(con: sqlite3.Connection, row: dict) -> None:
        vals = [row.get(c, "") for c in DATA_FILE_FIELDS]
        con.execute(
            f'INSERT OR REPLACE INTO files ({_FILE_COLS_SQL}) '
            f'VALUES ({_FILE_PLACEHOLDERS})', vals)

    @staticmethod
    def _insert_picture_row(con: sqlite3.Connection, row: dict) -> None:
        vals = []
        for c in PICTURE_FIELDS:
            v = row.get(c, "")
            if c == "Files":
                vals.append(v if isinstance(v, str) else json.dumps(v or []))
            elif c == "Rate":
                vals.append(_coerce_rate(v))
            else:
                vals.append(v)
        con.execute(
            f'INSERT OR REPLACE INTO pictures ({_PIC_COLS_SQL}) '
            f'VALUES ({_PIC_PLACEHOLDERS})', vals)

    @staticmethod
    def _delete_pictures(con: sqlite3.Connection, upns) -> None:
        for u in upns:
            con.execute('DELETE FROM pictures WHERE "UniquePictureName"=?', (u,))

    @staticmethod
    def _delete_files(con: sqlite3.Connection, fnames) -> None:
        for f in fnames:
            con.execute('DELETE FROM files WHERE "File:FileName"=?', (f,))

    @staticmethod
    def _set_species_fields(con: sqlite3.Connection, sci: str, fields: dict) -> None:
        sets, vals = [], []
        for col, v in fields.items():
            sets.append(f"{_quoted(col)}=?")
            if col in _SPEC_LIST_COLS:
                vals.append(v if isinstance(v, str) else json.dumps(v or []))
            elif col in _SPEC_NUM_COLS:
                vals.append(_coerce_int(v))
            else:
                vals.append(v)
        if not sets:
            return
        vals.append(sci)
        con.execute(
            f'UPDATE species SET {", ".join(sets)} WHERE "ScientificName"=?', vals)

    @staticmethod
    def _delete_species(con: sqlite3.Connection, scinames) -> None:
        for s in scinames:
            con.execute('DELETE FROM species WHERE "ScientificName"=?', (s,))

    def _upn_has_jpg(self, con: sqlite3.Connection, upn: str) -> bool:
        row = con.execute(
            'SELECT 1 FROM files WHERE "UniquePictureName"=? AND '
            'LOWER("WindowsExtension") IN ("jpg","jpeg") LIMIT 1', (upn,)).fetchone()
        return row is not None

    # ── species count maintenance for a no-JPG delete ─────────────────────────
    def _species_delete_updates(self, con: sqlite3.Connection, sci: str):
        """Compute the species-table changes for removing ONE no-JPG picture of
        `sci`, from the pre-delete snapshot. Returns (updates, deleted) where
        updates is {ScientificName: {col: value}} and deleted is [ScientificName].

        Because no JPG is ever deleted, BestJPG / BestUPN / currentHighestRating /
        ...WithJPGs... are invariant; only iPictureCollectionCount changes on the
        species + ancestors, plus iSpeciesCollectionCount when the species empties
        (and the species row + its parent's Leaves entry are removed)."""
        updates: dict[str, dict] = {}
        deleted: list[str] = []
        sci = (sci or "").strip()
        if not sci:
            return updates, deleted
        row = con.execute(
            'SELECT * FROM species WHERE LOWER("ScientificName")=?',
            (sci.lower(),)).fetchone()
        if row is None:
            return updates, deleted   # species not tracked → just drop the rows
        sciname = row["ScientificName"]
        n_before = con.execute(
            'SELECT COUNT(*) FROM pictures WHERE LOWER(TRIM("WetSoortNaam"))=?',
            (sciname.lower(),)).fetchone()[0]
        n_left = max(n_before - 1, 0)
        emptied = (n_left == 0)

        # ancestor chain (pre-delete snapshot)
        chain = []
        up = row["Up"]
        seen = set()
        while up and up not in seen:
            seen.add(up)
            arow = con.execute(
                'SELECT * FROM species WHERE "ScientificName"=?', (up,)).fetchone()
            if arow is None:
                break
            chain.append(arow)
            up = arow["Up"]

        if emptied:
            deleted.append(sciname)
            parent = row["Up"]
            prow = next((a for a in chain if a["ScientificName"] == parent), None)
            if prow is not None:
                leaves = json.loads(prow["Leaves"]) if prow["Leaves"] else []
                updates.setdefault(parent, {})["Leaves"] = [
                    x for x in leaves if x != sciname]
        else:
            updates.setdefault(sciname, {})["iPictureCollectionCount"] = n_left

        for a in chain:
            u = updates.setdefault(a["ScientificName"], {})
            u["iPictureCollectionCount"] = _coerce_int(a["iPictureCollectionCount"]) - 1
            if emptied:
                u["iSpeciesCollectionCount"] = _coerce_int(a["iSpeciesCollectionCount"]) - 1
        return updates, deleted

    def _species_addjpg_updates(self, con: sqlite3.Connection, sci: str,
                                upn: str, fname: str, rel: str, pic_rate: int):
        """Species-table changes when a picture that had NO JPG gains its first
        one (add of a crop). Symmetric to _species_delete_updates: increment
        iPicturesWithJPGsCollection on the species + ancestors, and bump the
        best-pick (BestJPG/BestUPN/BestJPGPathName/currentHighestRating) on any
        node the new picture's rating now leads. Returns {ScientificName: {...}}."""
        updates: dict[str, dict] = {}
        sci = (sci or "").strip()
        if not sci:
            return updates
        row = con.execute(
            'SELECT * FROM species WHERE LOWER("ScientificName")=?',
            (sci.lower(),)).fetchone()
        if row is None:
            return updates
        chain = [row]
        up = row["Up"]
        seen = set()
        while up and up not in seen:
            seen.add(up)
            arow = con.execute(
                'SELECT * FROM species WHERE "ScientificName"=?', (up,)).fetchone()
            if arow is None:
                break
            chain.append(arow)
            up = arow["Up"]
        for a in chain:
            u = updates.setdefault(a["ScientificName"], {})
            u["iPicturesWithJPGsCollection"] = \
                _coerce_int(a["iPicturesWithJPGsCollection"]) + 1
            cur_best = _coerce_int(a["currentHighestRating"])
            if pic_rate > cur_best or not (a["BestJPG"] or ""):
                u["BestJPG"] = fname
                u["BestUPN"] = upn
                u["BestJPGPathName"] = rel
                u["currentHighestRating"] = pic_rate
        return updates

    # ── master add/delete operations (durable; mirror apply_rating's discipline)
    def apply_delete_upn(self, upn: str) -> list[str] | None:
        """Delete a whole picture (UPN) + its file rows from the master, and
        decrement species counts. Caller MUST have verified the UPN has no JPG
        and trashed/handles the physical files. Returns the deleted file names."""
        con = self._connect()
        prow = con.execute(
            'SELECT * FROM pictures WHERE "UniquePictureName"=?', (upn,)).fetchone()
        if prow is None:
            return None
        sci = prow["WetSoortNaam"] or ""
        fnames = [r["File:FileName"] for r in con.execute(
            'SELECT "File:FileName" FROM files WHERE "UniquePictureName"=?', (upn,))]
        spec_updates, spec_deleted = self._species_delete_updates(con, sci)
        entry = {"op": "delete_upn", "ts": time.time(), "upn": upn,
                 "pictures_delete": [upn], "files_delete": fnames,
                 "species": spec_updates, "species_delete": spec_deleted}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        with con:
            self._delete_pictures(con, [upn])
            self._delete_files(con, fnames)
            for s, fields in spec_updates.items():
                self._set_species_fields(con, s, fields)
            self._delete_species(con, spec_deleted)
        MasterCheckpointWorker.get(self.master_root).notify()
        return fnames

    def apply_delete_file(self, upn: str, fname: str) -> bool:
        """Delete ONE non-JPG file from a UPN that survives (keeps other files).
        Caller verifies non-JPG and that it isn't the last file."""
        con = self._connect()
        prow = con.execute(
            'SELECT * FROM pictures WHERE "UniquePictureName"=?', (upn,)).fetchone()
        if prow is None:
            return False
        cur = json.loads(prow["Files"]) if prow["Files"] else []
        new_files = [f for f in cur if f != fname]
        entry = {"op": "delete_file", "ts": time.time(), "upn": upn,
                 "files_delete": [fname],
                 "pictures": {upn: {"Files": new_files}}}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        with con:
            self._delete_files(con, [fname])
            self._set_picture_fields(con, upn, {"Files": new_files})
        MasterCheckpointWorker.get(self.master_root).notify()
        return True

    def apply_add_file(self, upn: str, file_row: dict) -> bool:
        """Attach one new file (a crop JPG) to an existing UPN. Picture-level
        distillation (Files, JPGSize/BestJPG when the new JPG is larger) is done
        here; species-level rollups (WithJPGs counts, best-pick bubbling) are the
        genuinely hard part and are left to the Recount backstop — callers should
        flag master rollups dirty after an add."""
        con = self._connect()
        prow = con.execute(
            'SELECT * FROM pictures WHERE "UniquePictureName"=?', (upn,)).fetchone()
        if prow is None:
            return False
        fname = file_row.get("File:FileName", "")
        if not fname:
            return False
        file_row = {**file_row, "UniquePictureName": upn}
        cur = json.loads(prow["Files"]) if prow["Files"] else []
        new_files = cur + [fname] if fname not in cur else cur
        pic_updates: dict = {"Files": new_files}
        is_jpg = file_row.get("WindowsExtension", "").lower() in ("jpg", "jpeg")
        had_jpg = self._upn_has_jpg(con, upn)   # pre-insert snapshot
        if is_jpg:
            new_size = file_row.get("ImageSize", "")
            if _area(new_size) >= _area(prow["JPGSize"] or ""):
                pic_updates["JPGSize"] = new_size
                pic_updates["BestJPG"] = fname
        spec_updates = {}
        if is_jpg and not had_jpg:
            rel = (prow["TargetPath"] or prow["BestJPGPathName"] or "").strip()
            spec_updates = self._species_addjpg_updates(
                con, prow["WetSoortNaam"] or "", upn, fname, rel,
                _coerce_rate(prow["Rate"]) or 0)
        entry = {"op": "add_file", "ts": time.time(), "upn": upn,
                 "files_insert": {fname: file_row},
                 "pictures": {upn: pic_updates},
                 "species": spec_updates}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        with con:
            self._insert_file_row(con, file_row)
            self._set_picture_fields(con, upn, pic_updates)
            for s, fields in spec_updates.items():
                self._set_species_fields(con, s, fields)
        MasterCheckpointWorker.get(self.master_root).notify()
        return True

    def apply_restore(self, picture: dict, files: list[dict]) -> None:
        """Undo support: re-insert a previously-deleted picture row + its file
        rows (shaped like the query API returns). Species counts are NOT restored
        here — a Recount reconciles them (the common single-delete case stays
        exact because the inverse count delta is tiny)."""
        upn = picture.get("UniquePictureName", "")
        entry = {"op": "restore", "ts": time.time(), "upn": upn,
                 "pictures_insert": {upn: picture},
                 "files_insert": {fr.get("File:FileName", ""): fr for fr in files}}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        con = self._connect()
        with con:
            self._insert_picture_row(con, picture)
            for fr in files:
                self._insert_file_row(con, fr)
        MasterCheckpointWorker.get(self.master_root).notify()

    # ── checkpoint (CSV catch-up) ─────────────────────────────────────────────
    def _rewrite_pictures_csv(self, con: sqlite3.Connection, target: Path) -> None:
        from engine.picturecsv import Pictures
        p = Pictures(None)
        for r in con.execute("SELECT * FROM pictures ORDER BY rowid"):
            d = self._picture_row_to_dict(r)
            p.UniquePictureName_dict[d["UniquePictureName"]] = d
        tmp = target.with_suffix(target.suffix + ".tmp")
        p.picturefile = str(tmp)
        p.write_project_summary()
        os.replace(tmp, target)

    def _rewrite_files_csv(self, con: sqlite3.Connection, target: Path) -> None:
        from engine.filecsv import Files
        f = Files(None)
        for r in con.execute("SELECT * FROM files ORDER BY rowid"):
            d = {k: r[k] for k in r.keys()}
            f.files_dict[d["File:FileName"]] = d
        tmp = target.with_suffix(target.suffix + ".tmp")
        f.filename = str(tmp)
        f.write_project_files()
        os.replace(tmp, target)

    def _species_row_to_dict(self, row: sqlite3.Row) -> dict:
        """Shape a species DB row for the CSV writer: Leaves/JPGmap JSON → list
        (DictWriter renders ['a', 'b']); Level stays the 'Level.SPECIES' string;
        counts stay ints. Reproduces the pipeline's writeSpeciesInfo format."""
        d = {}
        for col in SPECIES_FIELDS:
            v = row[col]
            if col in _SPEC_LIST_COLS:
                try:
                    d[col] = json.loads(v) if v else []
                except Exception:
                    d[col] = []
            else:
                d[col] = v
        return d

    def _rewrite_species_csv(self, con: sqlite3.Connection, target: Path) -> None:
        import csv
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=SPECIES_FIELDS, quoting=csv.QUOTE_MINIMAL, delimiter=";")
            writer.writeheader()
            for r in con.execute("SELECT * FROM species ORDER BY rowid"):
                writer.writerow(self._species_row_to_dict(r))
        os.replace(tmp, target)

    def _replay_sections(self, con: sqlite3.Connection, e: dict,
                         pics_mtime: float, files_mtime: float,
                         spec_mtime: float) -> tuple[bool, bool, bool]:
        """Apply a generic add/delete entry's sections, each gated by its target
        CSV's mtime (Phase 2 stale-drop). Returns (dirty_pics, dirty_files,
        dirty_species)."""
        ts = e.get("ts", 0.0)
        dp = df = ds = False

        pics = e.get("pictures") or {}
        pins = e.get("pictures_insert") or {}
        pdel = e.get("pictures_delete") or []
        if pics or pins or pdel:
            if ts > pics_mtime:
                for upn, row in pins.items():
                    self._insert_picture_row(con, row)
                for upn, fields in pics.items():
                    self._set_picture_fields(con, upn, fields)
                self._delete_pictures(con, pdel)
                dp = True
            else:
                logger.warning(f"master checkpoint: dropping stale pictures op "
                               f"{e.get('op')} for {e.get('upn')}")

        fset = e.get("files") or {}
        fins = e.get("files_insert") or {}
        fdel = e.get("files_delete") or []
        if fset or fins or fdel:
            if ts > files_mtime:
                for fn, fields in fset.items():
                    self._set_file_fields(con, fn, fields)
                for fn, row in fins.items():
                    self._insert_file_row(con, row)
                self._delete_files(con, fdel)
                df = True
            else:
                logger.warning(f"master checkpoint: dropping stale files op "
                               f"{e.get('op')} for {e.get('upn')}")

        sset = e.get("species") or {}
        sdel = e.get("species_delete") or []
        if sset or sdel:
            if ts > spec_mtime:
                for sci, fields in sset.items():
                    self._set_species_fields(con, sci, fields)
                self._delete_species(con, sdel)
                ds = True
            else:
                logger.warning(f"master checkpoint: dropping stale species op "
                               f"{e.get('op')} for {e.get('upn')}")
        return dp, df, ds

    def _truncate_jsonl(self, processed_max_seq: int) -> None:
        """Drop entries up to processed_max_seq, preserving any appended during
        the checkpoint (seq strictly greater)."""
        keep = [e for e in self._read_jsonl()
                if e.get("seq", 0) > processed_max_seq]
        tmp = self._jsonl_path.with_suffix(self._jsonl_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for e in keep:
                fh.write(json.dumps(e) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._jsonl_path)

    def checkpoint(self) -> int:
        """Replay pending entries into the DB (those newer than the affected CSV
        mtime; older ones are stale conflicts → dropped) and atomically rewrite
        the dirty master CSV(s) from the DB. Returns the number of entries
        processed. Thread-safe via the class-level checkpoint lock."""
        with MasterDB._checkpoint_lock:
            entries = self._read_jsonl()
            if not entries:
                return 0
            con = self._connect()
            pics_csv, files_csv = self._pictures_csv(), self._files_csv()
            spec_csv = self._species_csv()
            pics_mtime = pics_csv.stat().st_mtime if pics_csv.exists() else 0.0
            files_mtime = files_csv.stat().st_mtime if files_csv.exists() else 0.0
            spec_mtime = spec_csv.stat().st_mtime if spec_csv.exists() else 0.0
            max_seq = max(e.get("seq", 0) for e in entries)
            dirty_pics = dirty_files = dirty_species = False
            with con:
                for e in entries:
                    ts = e.get("ts", 0.0)
                    if e.get("op", "rate") == "rate":
                        # Phase-2 re-rating entry (flat pictures + files{fname})
                        pic = e.get("pictures")
                        if pic and any(v is not None for v in pic.values()):
                            if ts > pics_mtime:
                                self._replay_picture(con, e["upn"], pic)
                                dirty_pics = True
                            else:
                                logger.warning(
                                    f"master checkpoint: dropping stale pictures entry "
                                    f"for {e.get('upn')} (older than {pics_csv.name})")
                        fls = e.get("files") or {}
                        if fls:
                            if ts > files_mtime:
                                for fname, fields in fls.items():
                                    self._replay_file(con, fname, fields)
                                dirty_files = True
                            else:
                                logger.warning(
                                    f"master checkpoint: dropping stale files entry "
                                    f"for {e.get('upn')} (older than {files_csv.name})")
                    else:
                        # add/delete op (generic, keyed sections)
                        dp, df, ds = self._replay_sections(
                            con, e, pics_mtime, files_mtime, spec_mtime)
                        dirty_pics |= dp
                        dirty_files |= df
                        dirty_species |= ds
            if dirty_pics:
                self._rewrite_pictures_csv(con, pics_csv)
            if dirty_files:
                self._rewrite_files_csv(con, files_csv)
            if dirty_species and spec_csv.exists():
                self._rewrite_species_csv(con, spec_csv)
            # Refresh stored mtimes for whatever we rewrote so the next
            # is_stale() check doesn't see our own write as an external change.
            updates = []
            if dirty_pics:
                updates.append(("pictures_csv_mtime", self._csv_mtime(pics_csv)))
            if dirty_files:
                updates.append(("files_csv_mtime", self._csv_mtime(files_csv)))
            if dirty_species and spec_csv.exists():
                updates.append(("species_csv_mtime", self._csv_mtime(spec_csv)))
            if updates:
                with con:
                    con.executemany(
                        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                        updates)
            self._truncate_jsonl(max_seq)
            n = len(entries)
            logger.info(f"master checkpoint: {n} pending write(s) flushed to CSV.")
            return n

    def recover_if_needed(self) -> int:
        """On launch: if the pending-writes log is non-empty, flush it. Safe to
        call after ensure_fresh() on the warm thread."""
        if self.pending_count() > 0:
            logger.info("master: pending CSV writes found at launch — checkpointing.")
            return self.checkpoint()
        return 0

    # ── stats (for the Database settings dialog) ──────────────────────────────
    def pending_count(self) -> int:
        return len(self._read_jsonl())

    def pending_oldest_ts(self) -> float | None:
        tss = [e.get("ts") for e in self._read_jsonl() if e.get("ts")]
        return min(tss) if tss else None

    def meta_value(self, key: str) -> str | None:
        try:
            con = self._connect()
            row = con.execute(
                "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None
        except sqlite3.OperationalError:
            return None

    def file_count(self) -> int:
        con = self._connect()
        try:
            return con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


# ── background checkpoint worker ────────────────────────────────────────────────

class MasterCheckpointWorker:
    """One daemon thread per master root that debounces master writes into a
    single CSV rewrite. It owns its own MasterDB (sqlite connections are
    thread-affine), so the GUI thread never blocks on the ~85k/178k-row CSV
    rewrite. notify() coalesces rapid star-clicks; flush() runs a synchronous
    checkpoint (Database dialog's "Flush now" / shutdown)."""

    _instances: dict[str, "MasterCheckpointWorker"] = {}
    _registry_lock = threading.Lock()

    @classmethod
    def get(cls, master_root) -> "MasterCheckpointWorker":
        key = str(master_root)
        with cls._registry_lock:
            w = cls._instances.get(key)
            if w is None:
                w = cls(master_root)
                cls._instances[key] = w
            return w

    def __init__(self, master_root):
        self.master_root = str(master_root)
        self._wake = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="MasterCheckpoint", daemon=True)
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        db = MasterDB(self.master_root)   # own connection, created in-thread
        while True:
            self._wake.wait()
            # Debounce: keep waiting until writes go quiet for the interval.
            debounce = _checkpoint_debounce_seconds()
            while True:
                self._wake.clear()
                if self._wake.wait(timeout=debounce):
                    continue
                break
            try:
                db.checkpoint()
            except Exception as e:
                logger.error(f"master checkpoint worker failed: {e}", exc_info=True)

    def flush(self) -> int:
        """Synchronous checkpoint on the caller's thread (fresh connection)."""
        return MasterDB(self.master_root).checkpoint()
