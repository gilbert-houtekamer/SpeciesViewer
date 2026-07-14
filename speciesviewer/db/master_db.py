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
# v3: pictures/files tables gained the Municipality / XMP:Municipality columns
#     (they're created from PICTURE_FIELDS / DATA_FILE_FIELDS).
# v4: pictures gained BestJPGPinned — the human's choice of a picture's public JPG.
SCHEMA_VERSION = 4

# Column that's typed as INTEGER (we sort on Rate; rest stay TEXT to mirror CSV).
_INT_COLS_PICTURES = {"Rate"}

# species.csv columns that need special handling in the DB mirror.
_SPEC_NUM_COLS = {
    "currentHighestRating", "iSpeciesThisRunCount", "iSpeciesCollectionCount",
    "iPictureThisRunCount", "iPictureCollectionCount",
    "iPicturesWithJPGsThisRun", "iPicturesWithJPGsCollection",
}
_SPEC_LIST_COLS = {"Leaves", "JPGmap"}  # stored as JSON, written back as ['a', 'b']


class MasterCsvGuard(Exception):
    """A master CSV rewrite was refused because it would have destroyed the file."""


def _guard_not_emptying(target: Path, n_rows: int, what: str) -> None:
    """Refuse to write a master CSV with NO rows over one that currently has some.

    This is the invariant that would have saved species.csv. The DB is a derived
    mirror, so every rewrite trusts it completely — and on 2026-07-14 a checkpoint read
    the species table during a rebuild's DDL window, saw 0 rows, and dutifully wrote a
    species.csv containing only a header. 2718 species destroyed by a write that was
    doing exactly what it was told.

    A master CSV going from many rows to zero is never a legitimate edit. It is always
    a bug upstream, and the only safe response is to refuse the write and shout, so the
    good file on disk survives to be the thing we recover from. [[feedback_qa_as_guardrails]]
    """
    if n_rows > 0 or not target.exists():
        return
    try:                                  # >1 line means it has real rows, not just a header
        with open(target, "r", encoding="utf-8") as fh:
            fh.readline()                 # header
            has_rows = bool(fh.readline().strip())
    except OSError:
        return
    if not has_rows:
        return                            # already empty; nothing to protect
    msg = (f"REFUSING to overwrite {target.name} with 0 {what} rows — the file on disk "
           f"has data and the database does not. This is a bug upstream (the DB was "
           f"read while it was being rebuilt); the CSV has been left untouched.")
    logger.error(msg)
    raise MasterCsvGuard(msg)


def _offer_jpg(best: dict, upn: str, fname: str, size: str,
               pinned: str = "") -> None:
    """Keep the BEST jpg per picture in `best`. ONE rule, used by every derivation —
    the picture's BestJPG, the species' JPGmap, the species/ancestor best-pick — so
    they cannot drift apart about which file is a picture's public face.

    In priority order:

    1. **A HUMAN'S PIN outranks everything.** More pixels is not always better: a
       tighter crop of a warbler in reed can beat a wider frame that shows more reed,
       and which is better is a subjective call no rule can make. Before the pin
       existed, a deliberately tighter re-crop (1096x731) silently LOST to the older,
       wider file (1200x798) it was meant to replace — the code overruled the
       photographer. `BestJPGPinned` is where that judgement lives.
    2. Otherwise the biggest wins.
    3. AT EQUAL SIZE the PROCESSED render wins over the straight camera JPG. The tie
       is real — `2021 mei nl-DS5_5120.jpg` and its `..._DxO.jpg` are both 1200x800 —
       and leaving it to fall out of string ordering is a coin toss dressed up as a
       rule.

    `-small` files are the website's thumbnails and are never a page picture, pinned
    or not.
    """
    if not upn or not fname or "-small" in fname.lower():
        return
    is_pinned = 1 if (pinned and fname.lower() == pinned.lower()) else 0
    key = (is_pinned, _area(size or ""), 1 if "dxo" in fname.lower() else 0, fname)
    cur = best.get(upn)
    if cur is None or key > cur:
        best[upn] = key


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
        """Drop and repopulate every table from the master CSVs.

        HOLDS THE CHECKPOINT LOCK FOR THE WHOLE REBUILD. This is not belt-and-braces,
        it is the fix for a bug that destroyed species.csv (2026-07-14):

        `with con:` does NOT wrap the DDL below. Python's sqlite3 commits any open
        transaction before a DROP/CREATE and runs it in autocommit — so the instant
        this method drops the tables, EVERY OTHER CONNECTION SEES THEM EMPTY, and goes
        on seeing them empty for the ~13 seconds the reload takes. The checkpoint
        worker is a separate thread with its own connection: it woke up inside that
        window, read `SELECT * FROM species` (0 rows), and wrote a species.csv with a
        header and nothing else. `ensure_fresh` then rebuilt the empty table from the
        empty file — self-sustaining. 2718 species, gone.

        _build_lock only serialised rebuilds against rebuilds; _checkpoint_lock only
        serialised checkpoints against checkpoints. NOTHING serialised a checkpoint
        against a rebuild — the one pair that shares mutable state. Note the lock order
        (build → checkpoint) is safe: checkpoint() never takes _build_lock."""
        import csv

        def note(msg: str) -> None:
            logger.info(msg)
            _diag(f"rebuild: {msg}")
            if progress_cb:
                progress_cb(msg)

        con = self._connect()
        t0 = time.time()
        note("Rebuilding master DB from CSVs…")
        with MasterDB._checkpoint_lock, con:
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

    def all_species_rows(self) -> list[dict]:
        """Every SPECIES-level taxon in the master species table (the species.csv
        mirror) — the Move-to-species target universe: any species ever seen, not
        only those that currently have pictures (that's species_picker_rows()).
        Returns [{"sci","nl","en","target_path"}] sorted by scientific name."""
        con = self._connect()
        try:
            rows = con.execute(
                'SELECT "ScientificName" AS sci, "DutchName" AS nl, '
                '"EnglishName" AS en, "TargetPath" AS target_path '
                'FROM species WHERE "Level" = ? AND TRIM("ScientificName") <> "" '
                'ORDER BY sci COLLATE NOCASE', ("Level.SPECIES",)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [{"sci": r["sci"], "nl": r["nl"] or "", "en": r["en"] or "",
                 "target_path": r["target_path"] or ""} for r in rows]

    def gps_points_for_species(self, sci: str) -> list[dict]:
        """Signed-decimal GPS points for a species' master pictures, for the map
        distribution view. Returns [{"lat","lon","upn","place"}] — one per picture
        that has a parseable GPSPosition (DMS strings decoded via engine.geo).
        Pictures without GPS are simply absent (~40% of the master has none)."""
        from engine.geo import parse_gps_dms
        con = self._connect()
        rows = con.execute(
            'SELECT "UniquePictureName" AS upn, "GPSPosition" AS gps, '
            '       COALESCE(NULLIF(TRIM("Location"),""), '
            '                NULLIF(TRIM("City"),""), '
            '                NULLIF(TRIM("Country"),"")) AS place '
            'FROM pictures '
            'WHERE LOWER(TRIM("WetSoortNaam")) = ? '
            '  AND TRIM(COALESCE("GPSPosition","")) <> ""',
            ((sci or "").strip().lower(),)).fetchall()
        out = []
        for r in rows:
            ll = parse_gps_dms(r["gps"])
            if ll:
                out.append({"lat": ll[0], "lon": ll[1],
                            "upn": r["upn"], "place": r["place"] or ""})
        return out

    def species_row(self, sci: str) -> dict | None:
        """Resolve one species (case-insensitive scientific name) to
        {"sci","nl","en","target_path"} — used to look up a Move-to-species
        target's folder + canonical names."""
        con = self._connect()
        row = con.execute(
            'SELECT "ScientificName" AS sci, "DutchName" AS nl, '
            '"EnglishName" AS en, "TargetPath" AS target_path '
            'FROM species WHERE LOWER("ScientificName") = ?',
            ((sci or "").strip().lower(),)).fetchone()
        if row is None:
            return None
        return {"sci": row["sci"], "nl": row["nl"] or "", "en": row["en"] or "",
                "target_path": row["target_path"] or ""}

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

    def _species_chain(self, con: sqlite3.Connection, sci: str):
        """[species row, …ancestor rows to the root] for a scientific name, or []
        when the species isn't in the table. Shared shape with the delete/add
        count helpers."""
        sci = (sci or "").strip()
        if not sci:
            return []
        row = con.execute(
            'SELECT * FROM species WHERE LOWER("ScientificName")=?',
            (sci.lower(),)).fetchone()
        if row is None:
            return []
        chain = [row]
        up = row["Up"]
        seen = {row["ScientificName"]}
        while up and up not in seen:
            seen.add(up)
            arow = con.execute(
                'SELECT * FROM species WHERE "ScientificName"=?', (up,)).fetchone()
            if arow is None:
                break
            chain.append(arow)
            up = arow["Up"]
        return chain

    def _species_subtree(self, con: sqlite3.Connection, sciname: str) -> list[str]:
        """[sciname, …every descendant] via the Leaves adjacency list. The best-pick
        of a non-leaf node is the best of everything BELOW it, so a recompute has to
        see the whole subtree, not just the node's own pictures."""
        out: list[str] = []
        stack, seen = [sciname], {sciname}
        while stack:
            cur = stack.pop()
            out.append(cur)
            row = con.execute(
                'SELECT "Leaves" FROM species WHERE "ScientificName"=?',
                (cur,)).fetchone()
            if row is None:
                continue
            try:
                leaves = json.loads(row["Leaves"]) if row["Leaves"] else []
            except (ValueError, TypeError):
                leaves = []
            for lf in leaves:
                if lf and lf not in seen:
                    seen.add(lf)
                    stack.append(lf)
        return out

    def _recompute_best_pick(self, con: sqlite3.Connection, sciname: str, *,
                             exclude_upns=(), exclude_files=(),
                             prefer_upn: str = "") -> dict | None:
        """A species node's best-pick, recomputed across its whole subtree.

        Mirrors specieslib's bubble-up definition (specieslib.py:305-310):
        currentHighestRating is the best Rate among pictures that HAVE a JPG, and
        BestJPG/BestUPN/BestJPGPathName name that picture. Note specieslib only ever
        ratchets the value UP — `if BestJPG == "" or new > current` — so nothing in
        the engine can LOWER it when a JPG goes away. That is exactly what a delete
        does, which is why the delete path has to recompute rather than adjust.

        `prefer_upn` keeps the recompute STABLE. Top ratings tie (both 'aves' and
        'animalia' have several pictures at their maximum), and specieslib's ratchet
        breaks the tie by bubble-up ORDER, which a from-scratch max cannot reproduce.
        Without this, recomputing a node would swap its representative picture for a
        different, equally-rated one — a silent change to the site for no reason. So
        the incumbent wins any tie it is still part of; the pick moves only when it
        has to.

        Returns the four fields, or None when nothing under the node has a JPG left."""
        names = [n.lower() for n in self._species_subtree(con, sciname)]
        if not names:
            return None
        ex_upns = {(u or "").lower() for u in exclude_upns}
        ex_files = {(f or "").lower() for f in exclude_files}
        best: dict = {}      # upn -> _offer_jpg sort key (…, filename)
        meta: dict = {}      # upn -> (rate, relative species folder)
        CHUNK = 400          # an ancestor like 'aves' spans thousands of species
        for i in range(0, len(names), CHUNK):
            part = names[i:i + CHUNK]
            qs = ",".join("?" * len(part))
            rows = con.execute(
                'SELECT p."UniquePictureName" AS upn, p."Rate" AS rate,'
                '       p."TargetPath" AS rel, p."BestJPGPinned" AS pinned,'
                '       f."File:FileName" AS fname, f."ImageSize" AS size'
                '  FROM pictures p JOIN files f'
                '    ON f."UniquePictureName" = p."UniquePictureName"'
                f' WHERE LOWER(TRIM(p."WetSoortNaam")) IN ({qs})'
                '   AND LOWER(f."WindowsExtension") IN ("jpg","jpeg")', part).fetchall()
            for r in rows:
                upn = r["upn"] or ""
                if upn.lower() in ex_upns or (r["fname"] or "").lower() in ex_files:
                    continue
                _offer_jpg(best, upn, r["fname"], r["size"] or "", r["pinned"] or "")
                meta[upn] = (_coerce_rate(r["rate"]) or 0, r["rel"] or "")
        if not best:
            return None
        top = max(meta[u][0] for u in best)
        if prefer_upn in best and meta[prefer_upn][0] == top:
            winner = prefer_upn                  # incumbent still ties at the top
        else:
            winner = max((u for u in best if meta[u][0] == top), key=str)
        return {"BestJPG": best[winner][-1], "BestUPN": winner,
                "BestJPGPathName": meta[winner][1],
                "currentHighestRating": top}

    def _species_dropjpg_updates(self, con: sqlite3.Connection, sci: str, upn: str,
                                 fnames, *, lost_last_jpg: bool, upn_gone: bool,
                                 new_best_fname: str = "") -> dict:
        """Species rollups when a picture LOSES JPGs — the exact reverse of
        _species_addjpg_updates, and a thing the master never needed until now,
        because a JPG was never deleted.

        Two shapes, and they are genuinely different:

        * The picture KEEPS a real JPG (a redundant render was dropped). The best
          PICTURE of every node is unchanged and so is its rating — only the best
          FILE NAME needs re-pointing. No counter moves.
        * The picture loses its LAST JPG, or goes entirely. Now
          iPicturesWithJPGsCollection drops on the species and every ancestor, and
          any node whose best-pick named this picture has to find a new one.

        Only nodes that actually POINT at this picture are recomputed. An ancestor's
        currentHighestRating is the max over its subtree, so removing one picture can
        only disturb it when that picture WAS the max — and then the node names it."""
        updates: dict[str, dict] = {}
        fset = {(f or "").lower() for f in fnames}
        for a in self._species_chain(con, sci):
            name = a["ScientificName"]
            u: dict = {}
            if lost_last_jpg:
                u["iPicturesWithJPGsCollection"] = max(
                    _coerce_int(a["iPicturesWithJPGsCollection"]) - 1, 0)
            points_here = ((a["BestUPN"] or "") == upn
                           or (a["BestJPG"] or "").lower() in fset)
            if points_here:
                if not lost_last_jpg and new_best_fname:
                    u["BestJPG"] = new_best_fname     # same picture, same rating
                else:
                    pick = self._recompute_best_pick(
                        con, name,
                        exclude_upns={upn} if (upn_gone or lost_last_jpg) else (),
                        exclude_files=fset,
                        prefer_upn=(a["BestUPN"] or ""))
                    u.update(pick or {"BestJPG": "", "BestUPN": "",
                                      "BestJPGPathName": "",
                                      "currentHighestRating": 0})
            if u:
                updates[name] = u
        return updates

    def _species_move_updates(self, con: sqlite3.Connection,
                              source_sci: str, target_sci: str):
        """Species-table changes for moving ONE no-JPG picture from `source_sci`
        to `target_sci`. Returns (updates {ScientificName: {col: value}},
        deleted [ScientificName]) as ABSOLUTE values so they're safe to replay.

        Both taxonomy chains are diffed against the SAME pre-move snapshot and the
        per-node deltas summed, so any ancestor the two chains share nets to zero
        (the total picture count under, say, Aves doesn't change when a picture
        moves between two birds). No JPG is involved (moves of JPG-bearing
        pictures are refused), so BestJPG / …WithJPGs / currentHighestRating stay
        invariant — only iPictureCollectionCount (all nodes) and, on a species
        emptying/un-emptying, iSpeciesCollectionCount + the parent's Leaves.

        MUST be called BEFORE the picture row is reassigned (it counts pictures
        under each species to detect emptying)."""
        source_sci = (source_sci or "").strip()
        target_sci = (target_sci or "").strip()
        updates: dict[str, dict] = {}
        deleted: list[str] = []
        tgt_chain = self._species_chain(con, target_sci)
        if not tgt_chain:
            return updates, deleted   # target not tracked → nothing to roll up
        src_chain = self._species_chain(con, source_sci)

        rows_by_name = {r["ScientificName"]: r for r in src_chain + tgt_chain}

        # iPictureCollectionCount: −1 along the source chain, +1 along the target
        # chain (shared ancestors cancel).
        pic_delta: dict[str, int] = {}
        for r in src_chain:
            pic_delta[r["ScientificName"]] = pic_delta.get(r["ScientificName"], 0) - 1
        for r in tgt_chain:
            pic_delta[r["ScientificName"]] = pic_delta.get(r["ScientificName"], 0) + 1

        def _pic_count(sci: str) -> int:
            return con.execute(
                'SELECT COUNT(*) FROM pictures WHERE LOWER(TRIM("WetSoortNaam"))=?',
                (sci.lower(),)).fetchone()[0]

        source_empties = bool(src_chain) and (_pic_count(source_sci) - 1 <= 0)
        target_was_empty = (_pic_count(target_sci) == 0)

        # iSpeciesCollectionCount changes only on the ANCESTORS when a species
        # appears/disappears under them.
        spec_delta: dict[str, int] = {}
        if source_empties:
            for r in src_chain[1:]:
                spec_delta[r["ScientificName"]] = spec_delta.get(r["ScientificName"], 0) - 1
        if target_was_empty:
            for r in tgt_chain[1:]:
                spec_delta[r["ScientificName"]] = spec_delta.get(r["ScientificName"], 0) + 1

        for name, d in pic_delta.items():
            if d:
                cur = _coerce_int(rows_by_name[name]["iPictureCollectionCount"])
                updates.setdefault(name, {})["iPictureCollectionCount"] = cur + d
        for name, d in spec_delta.items():
            if d:
                cur = _coerce_int(rows_by_name[name]["iSpeciesCollectionCount"])
                updates.setdefault(name, {})["iSpeciesCollectionCount"] = cur + d

        # Parent Leaves: drop the emptied source, add the un-emptied target. Build
        # incrementally so a shared parent (moving between two species of one
        # genus) sees both edits.
        def _parent_leaves(parent_name: str) -> list:
            if parent_name in updates and "Leaves" in updates[parent_name]:
                return list(updates[parent_name]["Leaves"])
            praw = rows_by_name.get(parent_name)
            return json.loads(praw["Leaves"]) if praw and praw["Leaves"] else []

        if source_empties and len(src_chain) >= 2:
            parent = src_chain[1]["ScientificName"]
            leaves = [x for x in _parent_leaves(parent)
                      if x != src_chain[0]["ScientificName"]]
            updates.setdefault(parent, {})["Leaves"] = leaves
        if target_was_empty and len(tgt_chain) >= 2:
            parent = tgt_chain[1]["ScientificName"]
            leaves = _parent_leaves(parent)
            tname = tgt_chain[0]["ScientificName"]
            if tname not in leaves:
                leaves = leaves + [tname]
            updates.setdefault(parent, {})["Leaves"] = leaves

        # Mirror delete: an emptied source species row is removed (it re-creates on
        # a future add). Don't also emit field updates for a row we delete.
        if source_empties:
            sname = src_chain[0]["ScientificName"]
            deleted.append(sname)
            updates.pop(sname, None)

        return updates, deleted

    @staticmethod
    def _species_jpgmap(con: sqlite3.Connection, sci: str, *,
                        override: dict | None = None,
                        exclude: set | None = None,
                        exclude_files: set | None = None,
                        pin_override: dict | None = None,
                        size_override: dict | None = None) -> list:
        """DERIVE a species' JPGmap: ONE entry per picture — its LARGEST real JPG.

        NOT the picture's BestJPG, which is what I first used and which the user
        caught: 87 pictures carry a BestJPG that is not their biggest JPG, and 23 of
        those point at the 600x450 THUMBNAIL. Deriving from BestJPG faithfully
        propagated that error into JPGmap — it dropped 52 good _DxO renders (up to
        3000x2250) and left the page pointing at a thumbnail. The files table is the
        ground truth about what a picture actually has; BestJPG is a cached opinion
        about it, and a stale one. Derive from the truth.

        `-small` files are excluded: they are the website's thumbnails, never a page
        picture.

        The species page renders from this list, and until now it was only ever
        APPENDED to, by exactly one code path (the full pipeline read,
        projectlib.py:872). So a JPG added any other way — "Add new files for this
        picture", a fresh DxO render — became the picture's BestJPG and then never
        appeared on the page, because nothing put it in this list. Four such JPGs were
        invisible in the master. An append that fires from only one path is a fact
        waiting to go stale; deriving it cannot.

        ONE ENTRY PER PICTURE, deliberately. The old list held every JPG a picture
        had, so a picture with both an 800px original and a 2000px re-render offered
        the page two entries for the SAME photo — which is how you end up publishing
        the same bird twice, and how the pictures-with-JPGs counters get inflated. The
        page wants the best render of each picture, which is exactly BestJPG.

        Costs one query — no folder scan: the DB already knows every picture of the
        species and which of its JPGs is best.

        `override` (upn -> (filename, ImageSize)) / `exclude` (whole pictures) /
        `exclude_files` (individual filenames) let a caller derive the list for the
        state it is ABOUT to write: the journal entry is built before the DB is
        touched (crash safety), so a plain read cannot see a file still being
        inserted, or a picture — or a JPG — on its way out.

        `exclude_files` matters for a JPG delete specifically: dropping the picture
        would be wrong (it may keep other JPGs, and one of them becomes the page's
        entry), so the file has to leave the derivation on its own."""
        rows = con.execute(
            'SELECT f."UniquePictureName" AS upn, f."File:FileName" AS fname,'
            '       f."ImageSize" AS size, p."BestJPGPinned" AS pinned'
            '  FROM files f JOIN pictures p'
            '    ON p."UniquePictureName" = f."UniquePictureName"'
            ' WHERE LOWER(TRIM(p."WetSoortNaam"))=?'
            '   AND LOWER(f."WindowsExtension") IN ("jpg","jpeg")',
            ((sci or "").strip().lower(),)).fetchall()

        exf = {(f or "").lower() for f in (exclude_files or ())}
        pino = pin_override or {}
        # A JPG re-exported over its own name has a NEW pixel size that the row does
        # not carry yet (the journal entry is built before the write lands). Correcting
        # the SIZE and re-deriving beats handing in a pre-picked winner: the rule stays
        # the only thing that decides, so it still honours the pin and still compares
        # against the picture's other renders.
        szo = {(k or "").lower(): v for k, v in (size_override or {}).items()}
        best: dict = {}          # upn -> sort key + filename
        for r in rows:
            fname_r = r["fname"] or ""
            if fname_r.lower() in exf:
                continue
            upn_r = r["upn"]
            pin = pino[upn_r] if upn_r in pino else (r["pinned"] or "")
            size = szo.get(fname_r.lower(), r["size"])
            _offer_jpg(best, upn_r, fname_r, size, pin)
        for upn, (fname, size) in (override or {}).items():
            _offer_jpg(best, upn, fname, size)
        for upn in (exclude or ()):
            best.pop(upn, None)
        return sorted({v[-1] for v in best.values() if v[-1]})

    def _jpgmap_update(self, con: sqlite3.Connection, sci: str, **kw) -> dict:
        """{sci: {"JPGmap": [...]}} for the journal, or {} when there is nothing to
        say. Folded into whichever write is already happening, so the CSV follows."""
        sci = (sci or "").strip()
        if not sci:
            return {}
        return {sci: {"JPGmap": self._species_jpgmap(con, sci, **kw)}}

    def redistil_best_jpg(self, upn: str) -> tuple[str, str] | None:
        """Re-point a picture's BestJPG / JPGSize at its LARGEST real JPG.

        BestJPG is a cached opinion, and for 87 pictures it had gone stale — 23 of
        them naming the 600x450 THUMBNAIL as the picture's best image. Anything that
        trusts it (the website best-pick, the Species Viewer's rating sort, the
        "upgrade the best JPG" worklist) was being lied to. Returns (fname, size) when
        it changed, else None."""
        con = self._connect()
        prow = con.execute(
            'SELECT "BestJPG" b, "JPGSize" j, "BestJPGPinned" p FROM pictures'
            ' WHERE "UniquePictureName"=?', (upn,)).fetchone()
        pinned = (prow["p"] or "") if prow else ""
        rows = con.execute(
            'SELECT "File:FileName" AS fname, "ImageSize" AS size FROM files'
            ' WHERE "UniquePictureName"=? AND LOWER("WindowsExtension") IN ("jpg","jpeg")',
            (upn,)).fetchall()
        # The SAME rule the JPGmap derivation uses, so the two can never disagree
        # about which file is a picture's best — including which one a human pinned.
        pick: dict = {}
        sizes: dict = {}
        for r in rows:
            _offer_jpg(pick, upn, r["fname"], r["size"] or "", pinned)
            sizes[r["fname"]] = r["size"] or ""
        if upn not in pick:
            return None
        fname = pick[upn][-1]
        size = sizes.get(fname, "")
        if prow is None or ((prow["b"] or "") == fname and (prow["j"] or "") == size):
            return None
        fields = {"BestJPG": fname, "JPGSize": size}
        entry = {"op": "redistil_bestjpg", "ts": time.time(), "upn": upn,
                 "pictures": {upn: fields}}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        with con:
            self._set_picture_fields(con, upn, fields)
        MasterCheckpointWorker.get(self.master_root).notify()
        return fname, size

    def rebuild_species_jpgmap(self, sci: str) -> list:
        """Re-derive one species' JPGmap and persist it. Standalone entry point —
        used to heal the species whose list drifted before this was derived."""
        con = self._connect()
        jpgmap = self._species_jpgmap(con, sci)
        entry = {"op": "jpgmap", "ts": time.time(), "upn": "",
                 "species": {sci: {"JPGmap": jpgmap}}}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        with con:
            self._set_species_fields(con, sci, {"JPGmap": jpgmap})
        MasterCheckpointWorker.get(self.master_root).notify()
        return jpgmap

    def apply_jpg_replaced(self, upn: str, fname: str, image_size: str) -> bool:
        """A tracked JPG was replaced on disk, under its own name, with a DIFFERENT
        number of pixels.

        This used to assume the replacement was always BIGGER — the crop upgrade's
        case, "same picture, better render" — and concluded there was no JPGmap to edit
        and no best-pick to re-distil. That assumption is dead: PhotoLab re-exports a
        TIGHTER crop over the same name, and a JPG that shrinks may no longer be its
        picture's best one. So the size change is re-derived through the same single
        rule as everything else (`_offer_jpg`, pin included): the picture's
        BestJPG/JPGSize, the species' JPGmap, and the BestJPG filename of any
        species/ancestor whose best-pick IS this picture. The rating never moves —
        it is the same photograph.

        Journalled like every other master write, so the CSVs follow."""
        con = self._connect()
        prow = con.execute(
            'SELECT * FROM pictures WHERE "UniquePictureName"=?', (upn,)).fetchone()
        if prow is None:
            return False
        sci = (prow["WetSoortNaam"] or "").strip()
        pinned = (prow["BestJPGPinned"] or "")

        # Re-distil from what the files table will hold once this write lands (the
        # journal entry is built before the row is updated, so a plain read still sees
        # the OLD size — the same reason the delete path needs its overrides).
        rows = con.execute(
            'SELECT "File:FileName" AS f, "ImageSize" AS s FROM files'
            ' WHERE "UniquePictureName"=? AND LOWER("WindowsExtension") IN ("jpg","jpeg")',
            (upn,)).fetchall()
        pick, sizes = {}, {}
        for r in rows:
            size = image_size if r["f"] == fname else (r["s"] or "")
            sizes[r["f"]] = size
            _offer_jpg(pick, upn, r["f"], size, pinned)
        new_best = pick[upn][-1] if upn in pick else (prow["BestJPG"] or "")

        pic_fields = {"BestJPG": new_best, "JPGSize": sizes.get(new_best, "")}
        spec_updates = self._jpgmap_update(
            con, sci, size_override={fname: image_size})
        for a in self._species_chain(con, sci):
            if (a["BestUPN"] or "") == upn and (a["BestJPG"] or "") != new_best:
                spec_updates.setdefault(a["ScientificName"], {})["BestJPG"] = new_best

        entry = {"op": "jpg_replaced", "ts": time.time(), "upn": upn,
                 "files": {fname: {"ImageSize": image_size}},
                 "pictures": {upn: pic_fields},
                 "species": spec_updates}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        with con:
            self._set_file_fields(con, fname, {"ImageSize": image_size})
            self._set_picture_fields(con, upn, pic_fields)
            for s, fields in spec_updates.items():
                self._set_species_fields(con, s, fields)
        MasterCheckpointWorker.get(self.master_root).notify()
        return True

    LOCATION_COLUMNS = ("Location", "City", "Municipality", "State", "Country")

    def apply_location_fields(self, updates: dict) -> int:
        """Set the place-name columns on MANY pictures at once: {upn: {field: val}}.

        Used by the reverse-geocode backfill, which fills in the pictures that have
        a coordinate but no place text at all. The sidecar is still the truth — the
        caller writes the XMP first — but the website, the maps and the viewers all
        read the master index, so without this the names would not appear anywhere
        until a full master re-read.

        ONE journal entry for the whole batch, deliberately: 2,415 separate entries
        would rewrite data_pictures.csv 2,415 times. Only the five location columns
        are writable here, so a caller cannot smuggle in a species or a rating."""
        clean = {}
        for upn, fields in (updates or {}).items():
            keep = {k: v for k, v in (fields or {}).items()
                    if k in self.LOCATION_COLUMNS}
            if keep:
                clean[upn] = keep
        if not clean:
            return 0
        con = self._connect()
        entry = {"op": "set_location", "ts": time.time(), "pictures": clean}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        with con:
            for upn, fields in clean.items():
                self._set_picture_fields(con, upn, fields)
        MasterCheckpointWorker.get(self.master_root).notify()
        logger.info(f"master: set place names on {len(clean)} picture(s).")
        return len(clean)

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
        had_jpg = self._upn_has_jpg(con, upn)     # snapshot BEFORE the rows go
        spec_updates, spec_deleted = self._species_delete_updates(con, sci)
        # The deleted picture's BestJPG must leave the species' JPGmap, or the page
        # keeps trying to render a file that is gone. Derived for the post-delete
        # state (exclude), and skipped when the species row itself is being removed.
        if sci and sci not in spec_deleted:
            # _species_delete_updates deliberately moves only iPictureCollectionCount,
            # because it was written when "a JPG is never deleted" made every other
            # rollup invariant. A JPG-bearing picture CAN now be deleted (a banner
            # export whose only file is the JPG), and then the JPG counters and the
            # best-pick move too. Different columns, so the two merge cleanly.
            if had_jpg:
                for s, fields in self._species_dropjpg_updates(
                        con, sci, upn, fnames,
                        lost_last_jpg=True, upn_gone=True).items():
                    spec_updates.setdefault(s, {}).update(fields)
            for s, fields in self._jpgmap_update(
                    con, sci, exclude={upn}).items():
                spec_updates.setdefault(s, {}).update(fields)
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

    def jpgs_for_upn(self, upn: str) -> list[dict]:
        """The picture's real JPGs (thumbnails excluded), biggest first, each flagged
        `best` (what publishes today) and `pinned` (what a human chose). What the
        'use this JPG' picker shows."""
        con = self._connect()
        prow = con.execute(
            'SELECT "BestJPG" b, "BestJPGPinned" p FROM pictures'
            ' WHERE "UniquePictureName"=?', (upn,)).fetchone()
        if prow is None:
            return []
        best, pinned = (prow["b"] or ""), (prow["p"] or "")
        out = []
        for r in con.execute(
                'SELECT "File:FileName" AS fname, "ImageSize" AS size FROM files'
                ' WHERE "UniquePictureName"=?'
                '   AND LOWER("WindowsExtension") IN ("jpg","jpeg")', (upn,)):
            fn = r["fname"] or ""
            if "-small" in fn.lower():
                continue
            out.append({"fname": fn, "size": r["size"] or "",
                        "area": _area(r["size"] or ""),
                        "best": fn == best, "pinned": fn.lower() == pinned.lower()})
        out.sort(key=lambda d: -d["area"])
        return out

    def apply_pin_jpg(self, upn: str, fname: str) -> bool:
        """Pin (or, with fname="", un-pin) the JPG a human chose as this picture's
        public face.

        The pin is the ONLY thing a human writes here. BestJPG stays derived — from
        the pin when set, from area when not — so there is still exactly one rule
        (`_offer_jpg`) and no second opinion to go stale. Everything the choice can
        move is re-derived in the same durable write: the picture's BestJPG/JPGSize,
        the species' JPGmap (the page renders from it), and the BestJPG filename of
        any species/ancestor whose best-pick IS this picture — same picture, same
        rating, different file, so the rating never moves."""
        con = self._connect()
        prow = con.execute(
            'SELECT * FROM pictures WHERE "UniquePictureName"=?', (upn,)).fetchone()
        if prow is None:
            return False
        sci = (prow["WetSoortNaam"] or "").strip()

        rows = con.execute(
            'SELECT "File:FileName" AS fname, "ImageSize" AS size FROM files'
            ' WHERE "UniquePictureName"=? AND LOWER("WindowsExtension") IN ("jpg","jpeg")',
            (upn,)).fetchall()
        names = {(r["fname"] or "").lower(): r for r in rows}
        if fname and fname.lower() not in names:
            logger.warning(f"apply_pin_jpg: {fname!r} is not a file of {upn!r}.")
            return False

        pick: dict = {}
        sizes: dict = {}
        for r in rows:
            _offer_jpg(pick, upn, r["fname"], r["size"] or "", fname)
            sizes[r["fname"]] = r["size"] or ""
        new_best = pick[upn][-1] if upn in pick else ""

        pic_updates = {"BestJPGPinned": fname, "BestJPG": new_best,
                       "JPGSize": sizes.get(new_best, "")}
        # pin_override, because the journal entry is built BEFORE the pin is written —
        # a plain read here would derive the page from the pin we are replacing.
        spec_updates = self._jpgmap_update(con, sci, pin_override={upn: fname})
        for a in self._species_chain(con, sci):
            if (a["BestUPN"] or "") == upn and (a["BestJPG"] or "") != new_best:
                spec_updates.setdefault(a["ScientificName"], {})["BestJPG"] = new_best

        entry = {"op": "pin_jpg", "ts": time.time(), "upn": upn,
                 "pictures": {upn: pic_updates}, "species": spec_updates}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        with con:
            self._set_picture_fields(con, upn, pic_updates)
            for s, fields in spec_updates.items():
                self._set_species_fields(con, s, fields)
        MasterCheckpointWorker.get(self.master_root).notify()
        return True

    def apply_delete_jpg(self, upn: str, fnames: list[str]) -> bool:
        """Delete one or more JPGs from a picture that SURVIVES the delete.

        NOT apply_delete_file, which was written for the no-JPG case and touches
        neither the picture's BestJPG, nor the species' JPGmap, nor the species
        counters — none of which a RAW delete can move, and every one of which a JPG
        delete does. Doing only some of them is how a species page ends up rendering
        a file that is no longer on disk.

        So all four move together, in one journal entry:
          * the picture's Files, BestJPG and JPGSize (re-distilled from what remains);
          * the species' JPGmap (re-derived, the deleted files excluded by name);
          * iPicturesWithJPGsCollection, when this was the picture's last JPG;
          * the best-pick of any species/ancestor that named the departing file."""
        con = self._connect()
        prow = con.execute(
            'SELECT * FROM pictures WHERE "UniquePictureName"=?', (upn,)).fetchone()
        if prow is None:
            return False
        sci = (prow["WetSoortNaam"] or "").strip()
        gone = {(f or "").lower() for f in fnames}

        cur = json.loads(prow["Files"]) if prow["Files"] else []
        new_files = [f for f in cur if (f or "").lower() not in gone]

        # Deleting the very file a human pinned RELEASES the pin — otherwise the
        # picture keeps naming a file that is no longer there, and every derivation
        # would quietly fall back to area anyway while the CSV still claimed a choice.
        pinned = (prow["BestJPGPinned"] or "")
        if pinned.lower() in gone:
            pinned = ""

        rows = con.execute(
            'SELECT "File:FileName" AS fname, "ImageSize" AS size FROM files'
            ' WHERE "UniquePictureName"=? AND LOWER("WindowsExtension") IN ("jpg","jpeg")',
            (upn,)).fetchall()
        keep: dict = {}
        sizes: dict = {}
        any_jpg_left = False
        for r in rows:
            if (r["fname"] or "").lower() in gone:
                continue
            any_jpg_left = True                       # counts -small too, so that
            sizes[r["fname"]] = r["size"] or ""       # iPicturesWithJPGs* stays the
            _offer_jpg(keep, upn, r["fname"], r["size"] or "", pinned)  # mirror add
        new_best = keep[upn][-1] if upn in keep else ""
        lost_last = not any_jpg_left

        pic_updates = {"Files": new_files, "BestJPG": new_best,
                       "JPGSize": sizes.get(new_best, ""),
                       "BestJPGPinned": pinned}
        spec_updates = self._species_dropjpg_updates(
            con, sci, upn, fnames, lost_last_jpg=lost_last, upn_gone=False,
            new_best_fname=new_best)
        for s, fields in self._jpgmap_update(
                con, sci, exclude_files=gone,
                pin_override={upn: pinned}).items():   # the pin may have been released
            spec_updates.setdefault(s, {}).update(fields)

        entry = {"op": "delete_jpg", "ts": time.time(), "upn": upn,
                 "files_delete": list(fnames),
                 "pictures": {upn: pic_updates},
                 "species": spec_updates}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        with con:
            self._delete_files(con, list(fnames))
            self._set_picture_fields(con, upn, pic_updates)
            for s, fields in spec_updates.items():
                self._set_species_fields(con, s, fields)
        MasterCheckpointWorker.get(self.master_root).notify()
        return True

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
        pinned = (prow["BestJPGPinned"] or "")
        if is_jpg and not pinned:
            # A pinned picture keeps the JPG the human chose — a newly ingested render
            # being BIGGER is exactly the case the pin exists to overrule.
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
        if is_jpg:
            # THE PAGE RENDERS FROM JPGmap, and nothing but the full pipeline read
            # ever appended to it — so a JPG added here became the picture's BestJPG
            # and was then never shown. Re-derive the species' list from what the DB
            # will hold once this write lands (hence the override: the journal entry
            # is built before the row is updated). This is the whole "upgrade the best
            # JPG" loop, which was quietly half-broken.
            sci = prow["WetSoortNaam"] or ""
            for s, fields in self._jpgmap_update(
                    con, sci,
                    override={upn: (fname, file_row.get("ImageSize", ""))}).items():
                spec_updates.setdefault(s, {}).update(fields)
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

    def apply_move_species(self, upn: str, *, picture_fields: dict,
                           file_updates: dict | None = None) -> None:
        """Durably reassign a master picture to another species. This is a plain
        generic field update — the picture's species names + TargetPath, and each
        file's path fields — so the generic _replay_sections handles crash
        recovery (no new replay op). Species-count rollups are DEFERRED to a
        Recount, exactly like apply_add_file / apply_restore: the two taxonomy
        chains and species row create/remove are the hard part and Recount
        reconciles them exactly. jsonl intent first (fsync), then SQLite, then
        nudge the checkpoint worker."""
        file_updates = file_updates or {}
        con = self._connect()
        # Roll the species counts BEFORE the picture row is reassigned (the count
        # helper detects a species emptying/un-emptying by counting its pictures).
        prow = con.execute(
            'SELECT "WetSoortNaam" FROM pictures WHERE "UniquePictureName"=?',
            (upn,)).fetchone()
        source_sci = (prow["WetSoortNaam"] if prow else "") or ""
        target_sci = picture_fields.get("WetSoortNaam", "") or ""
        spec_updates, spec_deleted = self._species_move_updates(
            con, source_sci, target_sci)
        entry = {"op": "move_species", "ts": time.time(), "upn": upn,
                 "pictures": {upn: picture_fields},
                 "files": file_updates,
                 "species": spec_updates,
                 "species_delete": spec_deleted}
        with self._append_lock:
            entry["seq"] = self._next_seq()
            self._append_jsonl(entry)
        try:
            with con:
                self._set_picture_fields(con, upn, picture_fields)
                for fn, fields in file_updates.items():
                    self._set_file_fields(con, fn, fields)
                for sci, fields in spec_updates.items():
                    self._set_species_fields(con, sci, fields)
                self._delete_species(con, spec_deleted)
        except Exception:
            # The SQLite write was rolled back (with con) but the jsonl intent is
            # still on disk — and every checkpoint replays the whole log in one
            # transaction, so a single bad entry would block ALL future
            # checkpoints. Drop it before re-raising so a failed move stays
            # local and recoverable.
            self._drop_jsonl_seq(entry["seq"])
            raise
        MasterCheckpointWorker.get(self.master_root).notify()

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
        # Same guard as species. Pictures/files survived 2026-07-14 only because their
        # inserts happened to have committed before the checkpoint read them — they were
        # one thread-scheduling accident away from the identical fate.
        _guard_not_emptying(target, len(p.UniquePictureName_dict), "picture")
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
        _guard_not_emptying(target, len(f.files_dict), "file")
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
        rows = con.execute("SELECT * FROM species ORDER BY rowid").fetchall()
        _guard_not_emptying(target, len(rows), "species")
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=SPECIES_FIELDS, quoting=csv.QUOTE_MINIMAL, delimiter=";")
            writer.writeheader()
            for r in rows:
                writer.writerow(self._species_row_to_dict(r))
        os.replace(tmp, target)

    @staticmethod
    def _entry_is_stale(e: dict, pics_mtime: float, files_mtime: float,
                        spec_mtime: float) -> bool:
        """True when ANY section of this entry targets a CSV that has been rewritten
        past it. The entry is one logical write, so one stale section voids all of it —
        applying the rest is what leaves a picture naming a file that does not exist."""
        ts = e.get("ts", 0.0)
        if (e.get("pictures") or e.get("pictures_insert") or e.get("pictures_delete")) \
                and ts <= pics_mtime:
            return True
        if (e.get("files") or e.get("files_insert") or e.get("files_delete")) \
                and ts <= files_mtime:
            return True
        if (e.get("species") or e.get("species_delete")) and ts <= spec_mtime:
            return True
        return False

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

    def _drop_jsonl_seq(self, seq: int) -> None:
        """Remove one entry (by seq) from the pending-writes log — used to undo a
        jsonl intent whose SQLite write failed, so a bad entry can't poison every
        future checkpoint."""
        keep = [e for e in self._read_jsonl() if e.get("seq") != seq]
        tmp = self._jsonl_path.with_suffix(self._jsonl_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for e in keep:
                fh.write(json.dumps(e) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._jsonl_path)

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
                        # add/delete op (generic, keyed sections). ALL-OR-NOTHING:
                        # an entry is ONE logical write, and the per-section stale
                        # gate used to drop its sections independently. On 2026-07-14
                        # that dropped the files_insert of an ingested DxO render while
                        # applying the picture update that named it — leaving BestJPG
                        # pointing at a file with no row anywhere. If any part of an
                        # entry is stale, the whole write is void.
                        if self._entry_is_stale(e, pics_mtime, files_mtime, spec_mtime):
                            logger.warning(
                                f"master checkpoint: dropping the WHOLE stale entry "
                                f"{e.get('op')} for {e.get('upn')} (a CSV was rewritten "
                                f"past it) — a partial replay would corrupt the master.")
                            continue
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
