"""Master health: does the mirror still agree with its source, and a way back if not.

The master has **two kings**. `metadata/active/*.csv` is the canonical, human-readable,
Dropbox-synced truth; `master.db` is a derived SQLite mirror that every write goes
through first and that then rewrites the CSVs. Whichever one is wrong at the moment of a
write overwrites the other, and on 2026-07-14 that cost 2718 species rows: a checkpoint
read the species table during a rebuild's DDL window, saw it empty, and wrote an empty
species.csv over a perfectly good one.

`master_db._guard_not_emptying` is the floor — it refuses the catastrophic write. This
module is the rest of the answer:

  * `check()` — do the CSVs and the DB still agree, and does the master still make
    internal sense (no picture naming a file that has no row, no JPGmap entry that
    resolves to nothing). This is the pair of questions the two incidents were.
  * `daily_backup()` — one gzipped snapshot a day. All three CSVs are 154 MB raw but
    **7.3 MB gzipped, written in about a second**, so this is cheap enough to just do,
    rather than ask about.

**The check GATES the backup.** A backup taken from a corrupt master is worse than no
backup, because rotation would eventually push the last good snapshot out. If the
structural checks fail we refuse to write one and say so loudly.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger("speciesviewer")

KEEP_BACKUPS = 14                      # ~100 MB of history

# MIRRORED in master.db, so the CSV and the DB can be compared row for row.
_MIRRORED = ("data_pictures.csv", "data_files.csv", "species.csv")

# BACKED UP. speciescollection.csv is not mirrored — it is the 19,247-name recognition
# table, read by everything and written by speciesclassification.writeSpeciesCollection,
# which puts it in reach of exactly the accident that emptied species.csv. It is ~2 MB
# (0.3 MB gzipped), and it is the one file here you could not reconstruct from the
# pictures on disk. Cheap to keep, expensive to lose.
_CSVS = _MIRRORED + ("speciescollection.csv",)

# csv.field_size_limit default is too small for the Files column on some rows.
csv.field_size_limit(10_000_000)


def backup_root(master_root) -> Path:
    return Path(master_root) / "metadata" / "active" / "backup"


def _active(master_root) -> Path:
    return Path(master_root) / "metadata" / "active"


# ── the check ─────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    level: str          # "error" (structural — blocks a backup) | "warn" (drift)
    what: str
    detail: str = ""


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.errors:
            return f"{len(self.errors)} problem(s), {len(self.warnings)} warning(s)"
        if self.warnings:
            return f"consistent — {len(self.warnings)} warning(s)"
        return "consistent"


def _csv_rows(path: Path) -> int:
    """Real row count — csv.reader, not a line count: a Description can contain a
    newline, and a check that miscounts is worse than no check."""
    if not path.exists():
        return -1
    with open(path, "r", newline="", encoding="utf-8") as fh:
        r = csv.reader(fh, delimiter=";")
        next(r, None)                      # header
        return sum(1 for _ in r)


def check(db, master_root) -> Report:
    """Compare the mirror with its source, and the master with itself.

    HOLDS THE CHECKPOINT LOCK while it reads the DB — the same exclusion the checkpoint
    now takes. A rebuild publishes empty tables to every other connection while it runs
    (sqlite3 commits DDL in autocommit), so a reader that compares the DB against the
    CSV and does NOT hold this lock can see 0 rows and conclude the master is destroyed.
    That is the precise bug this module exists to catch; it would be absurd to reproduce
    it here — and this reader can REFUSE A BACKUP, so a false alarm has teeth."""
    from db.master_db import MasterDB, MasterCheckpointWorker
    t0 = time.time()
    rep = Report()
    act = _active(master_root)

    # DRAIN FIRST. Master writes are debounced (~2 s) before they reach the CSVs, so a
    # DB that is a few rows AHEAD of the CSV is the normal, healthy state of a master
    # someone is working in — not a disagreement. Comparing without draining reported a
    # 1-row difference as a structural ERROR, which would have REFUSED THE BACKUP because
    # a picture had just been edited. A false alarm with teeth is worse than no alarm.
    # "Do the two kings agree?" is only a meaningful question once the queue is empty.
    try:
        MasterCheckpointWorker.get(master_root).flush()
    except Exception as e:                      # incl. MasterCsvGuard — say so, then check
        logger.warning(f"master health: could not flush pending writes first: {e}")

    with MasterDB._checkpoint_lock:
        con = db._connect()
        rep = _check_locked(db, con, act, rep, t0)

    pending = db.pending_count()
    if pending:
        rep.findings.append(Finding(
            "warn", f"{pending} master write(s) are still pending",
            "the CSVs are behind the database; they could not be flushed"))
    return rep


def _check_locked(db, con, act, rep: "Report", t0: float) -> "Report":

    db_counts = {
        "data_pictures.csv": con.execute("SELECT COUNT(*) FROM pictures").fetchone()[0],
        "data_files.csv": con.execute("SELECT COUNT(*) FROM files").fetchone()[0],
        "species.csv": con.execute("SELECT COUNT(*) FROM species").fetchone()[0],
    }

    # 1. TWO KINGS: the CSV and the DB must hold the same number of rows.
    for name, n_db in db_counts.items():
        n_csv = _csv_rows(act / name)
        rep.counts[name] = {"csv": n_csv, "db": n_db}
        if n_csv < 0:
            rep.findings.append(Finding("error", f"{name} is missing"))
        elif n_csv != n_db:
            rep.findings.append(Finding(
                "error", f"{name}: CSV and database disagree",
                f"{n_csv} rows in the CSV, {n_db} in the database"))
        if n_db == 0 and name != "species.csv":
            rep.findings.append(Finding("error", f"{name}: the database table is EMPTY"))
    if db_counts["species.csv"] == 0 and db_counts["data_pictures.csv"] > 0:
        rep.findings.append(Finding(
            "error", "species.csv is empty but there are pictures",
            "this is the 2026-07-14 failure — do not let a backup be taken from it"))

    # 1b. speciescollection.csv has no DB mirror to be compared against, so the only
    #     question worth asking is the one that mattered: is it still there, and does it
    #     still have rows? It is written by writeSpeciesCollection, so it is reachable by
    #     the same accident — and unlike the others it cannot be rebuilt from the photos.
    n_sc = _csv_rows(act / "speciescollection.csv")
    rep.counts["speciescollection.csv"] = {"csv": n_sc, "db": None}
    if n_sc < 0:
        rep.findings.append(Finding("error", "speciescollection.csv is missing"))
    elif n_sc == 0:
        rep.findings.append(Finding(
            "error", "speciescollection.csv is EMPTY",
            "the species recognition table — nothing can be named without it"))

    # 2. A picture must not name a JPG that has no file row. This is the half-applied
    #    journal entry: the pictures section landed, the files_insert was dropped.
    dangling = con.execute(
        'SELECT COUNT(*) FROM pictures p WHERE p."BestJPG" != "" AND NOT EXISTS ('
        '  SELECT 1 FROM files f WHERE f."File:FileName" = p."BestJPG"'
        '    AND f."UniquePictureName" = p."UniquePictureName")').fetchone()[0]
    if dangling:
        # Split them: a picture that HAS a species is a real one whose file row went
        # missing (a half-applied journal entry) and Refresh will heal it. A picture with
        # NO species never belonged here at all — camels, goats, a driveway: blog extras
        # that leaked in, carrying only a -small thumbnail. Different problems, different
        # answers, so do not report them as one number.
        speciesless = con.execute(
            'SELECT COUNT(*) FROM pictures p'
            ' WHERE p."BestJPG" != "" AND TRIM(p."WetSoortNaam") = ""'
            '   AND NOT EXISTS (SELECT 1 FROM files f'
            '                    WHERE f."File:FileName" = p."BestJPG"'
            '                      AND f."UniquePictureName" = p."UniquePictureName")'
        ).fetchone()[0]
        real = dangling - speciesless
        if real:
            rep.findings.append(Finding(
                "warn", f"{real} picture(s) name a BestJPG with no file row",
                "use Refresh on the species — it re-scans the folder and heals this"))
        if speciesless:
            rep.findings.append(Finding(
                "warn", f"{speciesless} picture(s) have NO species and a missing BestJPG",
                "blog extras / landscapes that leaked into the master (a camel, a goat, "
                "a driveway); they render nowhere — see the topics/ item in the backlog"))

    # 3. Orphan file rows — a file whose picture is gone.
    orphans = con.execute(
        'SELECT COUNT(*) FROM files f WHERE NOT EXISTS ('
        '  SELECT 1 FROM pictures p'
        '   WHERE p."UniquePictureName" = f."UniquePictureName")').fetchone()[0]
    if orphans:
        rep.findings.append(Finding("warn", f"{orphans} file row(s) have no picture"))

    # 4. Every JPGmap entry must resolve to a real file row — the species page renders
    #    straight from this list, so an entry that resolves to nothing IS a broken image.
    names = {r[0] for r in con.execute('SELECT "File:FileName" FROM files')}
    bad_map = 0
    for r in con.execute('SELECT "ScientificName","JPGmap" FROM species'):
        try:
            jm = json.loads(r[1] or "[]")
        except (ValueError, TypeError):
            continue
        bad_map += sum(1 for x in jm if x and x not in names)
    if bad_map:
        rep.findings.append(Finding(
            "warn", f"{bad_map} JPGmap entr(ies) point at a file that is not tracked",
            "these would publish as broken images"))

    rep.seconds = time.time() - t0
    return rep


# ── the backup ────────────────────────────────────────────────────────────────

def list_backups(master_root) -> list[dict]:
    """Newest first: [{day, path, bytes, manifest}]."""
    root = backup_root(master_root)
    if not root.is_dir():
        return []
    out = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        gz = list(d.glob("*.csv.gz"))
        if not gz:
            continue
        man = {}
        mf = d / "manifest.json"
        if mf.exists():
            try:
                man = json.loads(mf.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        out.append({"day": d.name, "path": d,
                    "bytes": sum(p.stat().st_size for p in gz), "manifest": man})
    out.sort(key=lambda b: b["day"], reverse=True)
    return out


def make_backup(db, master_root, *, force: bool = False) -> dict:
    """Gzip the three master CSVs into backup/<YYYY-MM-DD>/.

    REFUSES when the structural checks fail: a snapshot of a broken master is not a
    backup, it is a time bomb — rotation would quietly push the last good one out."""
    rep = check(db, master_root)
    if rep.errors and not force:
        for f in rep.errors:
            logger.error(f"master backup REFUSED — {f.what}. {f.detail}")
        return {"ok": False, "report": rep,
                "error": "The master failed its consistency check; no backup was taken."}

    day = date.today().isoformat()
    dest = backup_root(master_root) / day
    dest.mkdir(parents=True, exist_ok=True)
    act = _active(master_root)
    t0 = time.time()
    written = 0
    for name in _CSVS:
        src = act / name
        if not src.exists():
            continue
        with open(src, "rb") as fi, gzip.open(dest / (name + ".gz"), "wb",
                                              compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo, 1024 * 1024)
        written += (dest / (name + ".gz")).stat().st_size
    (dest / "manifest.json").write_text(json.dumps({
        "taken": datetime.now().isoformat(timespec="seconds"),
        "counts": rep.counts,
        "check": rep.summary(),
        "warnings": [f"{f.what}. {f.detail}".strip() for f in rep.warnings],
    }, indent=2), encoding="utf-8")
    dt = time.time() - t0
    logger.info(f"master backup: {written/1e6:.1f} MB gzipped to {dest} in {dt:.1f}s "
                f"({rep.summary()}).")
    pruned = prune(master_root)
    return {"ok": True, "report": rep, "path": dest, "bytes": written,
            "seconds": dt, "pruned": pruned}


def _sweep_backup_husks(master_root) -> int:
    """Remove EMPTY directories in the backup folder.

    When a prune was blocked halfway, it deleted a backup's files and then
    failed on the directory itself — leaving an empty husk that list_backups no
    longer recognises as a backup (it requires a .csv.gz), so prune could never
    target it again and it lingered for ever. That is how one 2026-07-29 folder
    survived weeks of start-ups while producing a warning about itself.

    Only ever removes a directory with NOTHING in it, files or folders. An empty
    directory inside the master's backup folder is by definition not a backup,
    so this cannot reach anything that matters however the folder got its name.
    """
    from engine.album_delete import _rmtree_retry
    root = backup_root(master_root)
    if not root.is_dir():
        return 0
    swept = 0
    for d in root.iterdir():
        try:
            if not d.is_dir() or any(d.iterdir()):
                continue
        except OSError:
            continue
        if _rmtree_retry(d):
            logger.info(f"master backup: swept the empty leftover folder {d.name}.")
            swept += 1
    return swept


def prune(master_root, keep: int = KEEP_BACKUPS) -> int:
    """Keep the newest `keep` daily backups.

    Retries, because the master lives in Dropbox and Dropbox keeps a handle on a
    folder for a moment while it syncs the deletion — long enough for the first
    rmtree to fail with WinError 32 and short enough to be gone a fraction of a
    second later. That produced a scary-looking warning at every start-up about
    a folder that was already empty. Reuses the album delete's helper rather
    than growing a second opinion about how long Dropbox needs.

    A prune that genuinely cannot finish is still only housekeeping — the
    backups it failed to drop are old ones, so it stays a warning and the next
    run tries again.
    """
    from engine.album_delete import _rmtree_retry
    # Husks are not backups, so they are counted apart — saying "pruned 4
    # backup(s)" when four empty folders were swept would misreport what
    # happened to the history.
    swept = _sweep_backup_husks(master_root)
    backups = list_backups(master_root)
    dropped = 0
    for b in backups[keep:]:
        if _rmtree_retry(b["path"]):
            dropped += 1
        else:
            logger.warning(f"master backup prune: {Path(b['path']).name} is still "
                           f"held by another process (Dropbox syncing?) — it will "
                           f"be retried on the next start.")
    if dropped:
        logger.info(f"master backup: pruned {dropped} backup(s) older than the last {keep}.")
    return dropped + swept


def backup_due(master_root) -> bool:
    """True when today has no backup yet."""
    return not (backup_root(master_root) / date.today().isoformat()).is_dir()


def daily_backup(db, master_root) -> dict | None:
    """One snapshot a day. Cheap enough (about a second) to just do."""
    if not backup_due(master_root):
        return None
    return make_backup(db, master_root)


def restore(master_root, day: str) -> dict:
    """Put a backup's CSVs back. The CURRENT ones are snapshotted first (as
    `backup/pre-restore-<stamp>/`) — a restore is itself a destructive write, and the
    thing you are restoring over may be the only copy of something you still want.

    The caller MUST force a DB rebuild afterwards: the DB is now stale by construction."""
    src = backup_root(master_root) / day
    if not src.is_dir():
        raise FileNotFoundError(f"No backup for {day}")
    act = _active(master_root)

    stamp = datetime.now().strftime("pre-restore-%Y%m%d-%H%M%S")
    keep = backup_root(master_root) / stamp
    keep.mkdir(parents=True, exist_ok=True)
    for name in _CSVS:
        cur = act / name
        if cur.exists():
            with open(cur, "rb") as fi, gzip.open(keep / (name + ".gz"), "wb") as fo:
                shutil.copyfileobj(fi, fo, 1024 * 1024)

    restored = []
    for name in _CSVS:
        gz = src / (name + ".gz")
        if not gz.exists():
            continue
        tmp = act / (name + ".restoring")
        with gzip.open(gz, "rb") as fi, open(tmp, "wb") as fo:
            shutil.copyfileobj(fi, fo, 1024 * 1024)
        tmp.replace(act / name)            # atomic
        restored.append(name)
    logger.warning(f"master RESTORED from backup {day}: {restored}. "
                   f"The previous CSVs were kept in {keep}.")
    return {"day": day, "restored": restored, "kept": keep}
