"""Put a day's WORK back from a spare card.

ONE job: read the ZIPs `engine.work_backup` wrote and say, precisely, what
putting them back would do — then do the part that was agreed. It never touches
a RAW and never deletes anything on its own.

Invariant: **nothing is written until the whole plan is known.** Every question a
restore can raise — is this the right trip, does this file already exist, is it
the same file, which pictures were culled — is answered by reading manifests and
CRCs, before a single byte lands. That is what makes it safe to run on a machine
that already holds work.

Three ideas carry the design:

* **Identity is checked first, and refuses.** The card's trip ZIP holds an
  export.ini naming the trip it came from; `inspect_zip` reads it without
  extracting. Restoring Brazil's work into Goeree is the mistake that cannot be
  undone by hand, so it is the one thing that stops the run outright.

* **Identical is not a conflict.** A file whose CRC and size already match the
  archived one needs no decision — overwriting it changes nothing. Only a file
  that genuinely DIFFERS is worth a human, and there are usually none. ZIP
  entries carry a CRC32, so this costs one cheap pass over the day folder and no
  extraction at all.

* **"Culled" is read from the manifest, never from the disk.** After the picture
  cards are re-read, a culled RAW is back on disk AND has a fresh sidecar,
  because `_write_sidecars` creates one for any RAW that lacks it. So the
  absence of a sidecar proves nothing. The manifest's recorded picture list is
  the only surviving record of what survived the culling — which is also why the
  cards may be read before or after the restore, in any order.
"""

from __future__ import annotations

import json
import logging
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

from engine import work_backup as wb

logger = logging.getLogger("speciesviewer")

# Per-file outcomes of a plan.
NEW = "new"              # not on disk — written without asking
SAME = "identical"       # same size and CRC — skipped, never queried
DIFFERS = "differs"      # real conflict; the only kind worth a question


def _crc32(path: Path, chunk=1 << 20) -> int | None:
    crc = 0
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                crc = zlib.crc32(b, crc)
        return crc & 0xFFFFFFFF
    except OSError:
        return None


def _zip_dt(info: zipfile.ZipInfo) -> datetime | None:
    try:
        return datetime(*info.date_time)
    except (ValueError, TypeError):
        return None


# ── what is on the card ───────────────────────────────────────────────────────

def find_backups(dest_dir, trip_name="") -> dict:
    """What a backup folder holds. Read-only.

    `trip_name` narrows to one trip's folder; without it the whole card is
    described, which is what an "insert the card and tell me what is on it"
    screen needs.
    """
    dest = Path(dest_dir)
    roots = []
    if trip_name and (dest / trip_name).is_dir():
        roots = [dest / trip_name]
    elif dest.is_dir():
        roots = [p for p in sorted(dest.iterdir()) if p.is_dir()]
        if any(dest.glob("*.zip")):
            roots.append(dest)          # pre-per-trip-folder layout

    out = {"root": str(dest), "trips": []}
    for r in roots:
        days, identity = [], None
        for z in sorted(r.glob("*.zip")):
            man = wb.read_manifest(z)
            if man:
                days.append({"zip": z, "manifest": man, "day": man.get("day", z.stem)})
            else:
                identity = z            # no day manifest → the trip ZIP
        if days or identity:
            out["trips"].append({
                "folder": r.name if r != dest else "",
                "path": r, "days": days, "identity": identity,
                "prefix": next((d["manifest"].get("prefix", "") for d in days
                                if d["manifest"].get("prefix")), ""),
            })
    return out


def verify_trip(identity_zip, trip_root) -> tuple[bool, str, dict]:
    """Does this card's trip ZIP belong to `trip_root`? Reads only the manifest.

    The check that matters most, and the reason it is done before anything else:
    restoring one trip's work into another mixes two sets of culling decisions
    into one folder, and no later step can separate them again.
    """
    info = {}
    if identity_zip is None:
        return False, "the card holds no trip identity ZIP", info
    try:
        from engine.trip_transfer import inspect_zip
        info = inspect_zip(str(identity_zip)) or {}
    except Exception as e:
        return False, f"could not read {Path(identity_zip).name}: {e}", info

    card_id = (info.get("trip_id") or info.get("folder") or "").strip()
    try:
        from utils.trip_config import read_trip_ini
        data = read_trip_ini(str(trip_root)) or {}
    except Exception:
        data = {}
    here_id = (data.get("trip_id") or Path(trip_root).name or "").strip()
    if not card_id:
        return False, "the card's trip ZIP does not say which trip it is", info
    if card_id.lower() != here_id.lower():
        return False, (f"this card holds “{info.get('trip_name') or card_id}” "
                       f"({card_id}), but the open trip is {here_id}"), info
    return True, "", info


# ── planning a day ────────────────────────────────────────────────────────────

def plan_day(zip_path, day_dir) -> dict:
    """What restoring this ZIP into this folder would do. Writes nothing.

    Every archived file is classified NEW / SAME / DIFFERS by size then CRC32,
    both of which the ZIP already carries — so no entry is extracted to find out.
    """
    zip_path, day_dir = Path(zip_path), Path(day_dir)
    man = wb.read_manifest(zip_path) or {}
    files = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.filename == wb.MANIFEST_NAME:
                    continue
                target = day_dir / info.filename
                row = {"name": info.filename, "target": target,
                       "zip_bytes": info.file_size, "zip_when": _zip_dt(info),
                       "disk_bytes": None, "disk_when": None, "state": NEW}
                if target.exists():
                    try:
                        st = target.stat()
                        row["disk_bytes"] = st.st_size
                        row["disk_when"] = datetime.fromtimestamp(st.st_mtime)
                    except OSError:
                        pass
                    same = (row["disk_bytes"] == info.file_size
                            and _crc32(target) == info.CRC)
                    row["state"] = SAME if same else DIFFERS
                files.append(row)
    except (OSError, zipfile.BadZipFile) as e:
        return {"error": str(e), "files": [], "manifest": man}

    return {"error": "", "manifest": man, "files": files,
            "day": man.get("day", zip_path.stem),
            "prefix": man.get("prefix", ""),
            "counts": {NEW: sum(1 for f in files if f["state"] == NEW),
                       SAME: sum(1 for f in files if f["state"] == SAME),
                       DIFFERS: sum(1 for f in files if f["state"] == DIFFERS)}}


def restore_day(zip_path, day_dir, overwrite=None, logger_=None) -> dict:
    """Extract the plan. `overwrite` is a set of names the caller agreed to
    replace; anything DIFFERS and not named is left exactly as it is.

    Identical files are skipped rather than rewritten — not to save time but so
    that a restore which changes nothing leaves every mtime untouched, and the
    day does not then look freshly modified to the next backup.
    """
    log = logger_ or logger
    overwrite = set(overwrite or ())
    plan = plan_day(zip_path, day_dir)
    if plan["error"]:
        raise OSError(plan["error"])
    day_dir = Path(day_dir)
    day_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = kept = 0
    with zipfile.ZipFile(zip_path) as zf:
        for row in plan["files"]:
            if row["state"] == SAME:
                skipped += 1
                continue
            if row["state"] == DIFFERS and row["name"] not in overwrite:
                kept += 1
                continue
            with zf.open(row["name"]) as src, open(row["target"], "wb") as dst:
                dst.write(src.read())
            written += 1
    log.info(f"Restored {plan['day']}: {written} file(s) written, "
             f"{skipped} already identical, {kept} left as they were.")
    return {"day": plan["day"], "written": written, "identical": skipped,
            "kept": kept, "manifest": plan["manifest"]}


# ── what the culling removed ──────────────────────────────────────────────────

def reconcile_day(day_dir, manifest) -> dict:
    """Pictures on disk that the backup says were NOT there — i.e. culled.

    Read-only, and deliberately separate from the restore: it is meaningful only
    once the picture cards have been read, which may happen before or after.

    It compares against the manifest's recorded picture list, never against the
    presence of a sidecar, because a culled RAW coming back off a card is given
    a fresh sidecar by the read itself.
    """
    day_dir = Path(day_dir)
    recorded = {n.lower() for n in (manifest or {}).get("raws", [])}
    on_disk = []
    try:
        for p in sorted(day_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in wb.SOURCE_EXTS:
                on_disk.append(p)
    except OSError:
        pass
    extra = [p for p in on_disk if p.name.lower() not in recorded]
    missing = sorted(recorded - {p.name.lower() for p in on_disk})
    return {"day": day_dir.name, "on_disk": len(on_disk),
            "recorded": len(recorded), "culled": extra, "missing": missing}


def survivor_filter(dest_dir, trip_root=None, picture_root_name="") -> dict:
    """{day: {picture filename, …}} — what each backed-up day still held.

    Handed to `copy_card(include_names=…)`, this makes a card read re-apply the
    culling: the pictures a day's backup does not name are the ones deleted
    during the trip, so they are never copied back. Names are lower-cased and in
    TARGET form (already prefixed), which is what copy_card compares against.

    Only days the backup knows appear here — a day the cards hold and the backup
    has never seen is deliberately absent, so copy_card copies it in full. A
    filter that silently dropped an unknown day would turn "I backed up late"
    into "those pictures were never imported".

    Returns {} when there is no usable backup, which disables the filter
    entirely rather than filtering against nothing.
    """
    found = find_backups(dest_dir, picture_root_name)
    trips = found["trips"]
    mine = [t for t in trips if picture_root_name and t["folder"] == picture_root_name]         or (trips if len(trips) == 1 else [])
    if not mine:
        return {}
    entry = mine[0]
    if trip_root is not None:
        ok, _why, _info = verify_trip(entry["identity"], trip_root)
        if not ok:
            # Another trip's survivor list would delete this trip's pictures by
            # omission. Silence is the only safe answer.
            return {}
    out = {}
    for d in entry["days"]:
        raws = (d["manifest"] or {}).get("raws") or []
        if raws:
            out[d["day"]] = {n.lower() for n in raws}
    return out


# ── surviving-picture cache ───────────────────────────────────────────────────
# The card holding the manifests and the SD-card holding the pictures are often
# the SAME DRIVE LETTER, because there is one reader: swapping one in takes the
# other out. So the survivor list has to be captured while the SV INDEX card is
# present and kept on the laptop, or the card read that follows has nothing to
# filter by and quietly un-culls the trip.
#
# It lives in the trip repository, so it belongs to the trip rather than to the
# machine, and travels with it.
SURVIVOR_CACHE_REL = "admin/survivors.json"


def survivor_cache_path(trip_root) -> Path:
    return Path(trip_root) / SURVIVOR_CACHE_REL


def save_survivor_cache(trip_root, per_day: dict, logger_=None) -> int:
    """Remember which pictures each day still held, per the SV INDEX card.

    Written when the card IS readable — opening the restore screen is enough.
    Merged rather than replaced: a card read for one day must not erase what is
    known about the others.
    """
    log = logger_ or logger
    if not trip_root or not per_day:
        return 0
    path = survivor_cache_path(trip_root)
    data = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8")).get("days", {})
    except (OSError, ValueError):
        data = {}
    for day, names in per_day.items():
        data[day] = sorted(names)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"version": 1, "saved": datetime.now().isoformat(timespec="seconds"),
             "days": data}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning(f"Could not save the surviving-picture list: {e}")
        return 0
    log.info(f"Remembered which pictures survived the culling for "
             f"{len(per_day)} day(s) — the SD-cards can now be read without "
             f"the SV INDEX card present.")
    return len(per_day)


def load_survivor_cache(trip_root) -> dict:
    """{day: {name, …}} remembered earlier, or {} — lower-cased for matching."""
    try:
        raw = json.loads(survivor_cache_path(trip_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for day, names in (raw.get("days") or {}).items():
        if names:
            out[day] = {str(n).lower() for n in names}
    return out


def prefix_matches(day_dir, manifest) -> tuple[bool, str]:
    """Guard against a re-read done with the wrong filename prefix.

    Nothing on disk would look broken: the pictures are all there, correctly
    named for the prefix that was typed. But every one of them fails to match
    the backup, so the reconcile would report the entire day as culled. The
    manifest's own prefix is what catches it, and it must refuse rather than
    offer to delete a day's pictures.
    """
    want = ((manifest or {}).get("prefix") or "").strip()
    if not want:
        return True, ""
    got = wb.scan_day(day_dir)["prefix"]
    if not got:
        return True, ""
    if got.strip().lower() != want.lower():
        return False, (f"the pictures here are named “{got} - …” but this backup "
                       f"was made from “{want} - …”. Re-read the cards with the "
                       f"prefix “{want}”, or these will all look deleted.")
    return True, ""
