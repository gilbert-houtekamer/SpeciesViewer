"""Carry a trip's ADMIN between machines as a ZIP.

ONE JOB: decide what belongs in a trip transfer, and put it back on the other
machine without losing work that was done there.

INVARIANT: nothing that can be regenerated from the pictures ever travels. The
species identification lives in XMP:Subject in the sidecar next to each picture,
so metadata/review/<day>/*.csv is DERIVED — Read & Summarize rebuilds it on the
target. Picture files move by OS copy; this ZIP is kilobytes.

What travels, and why (see docs/plans/trip_export_import_plan.md):

  trip.ini                                  hard to recreate — the whole identity
  <trip_picture> / <trip_banner>            the two images named by trip.ini
  metadata/active/speciescollection.csv     easy to forget — merged, never replaced
  metadata/initialize/master_species_baseline.csv
                                            the lifer answer; NEVER regenerated on
                                            import (a partial master would mark
                                            known species as new)
  admin/TransCache.csv                      the trip's OWN translation cache —
                                            hand-verified descriptions
  export.ini                                the manifest (identity, publication,
                                            where the pictures should land)

What deliberately does NOT travel: admin/FTPcache.json (a foreign upload cache
makes the target SKIP uploads it genuinely needs — 404s that never heal),
biota/ and web/ (derived output), and metadata/review/ (see above).
"""

import csv
import io
import logging
import os
import platform
import shutil
import tempfile
import zipfile
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path

from utils.trip_config import read_trip_ini
from utils.trip_identity import resolve, resolve_trip_picture_root

logger = logging.getLogger("speciesviewer")

MANIFEST_NAME = "export.ini"
FORMAT_VERSION = "1"

TRIP_INI_REL   = "trip.ini"
COLLECTION_REL = "metadata/active/speciescollection.csv"
BASELINE_REL   = "metadata/initialize/master_species_baseline.csv"
TRANSCACHE_REL = "admin/TransCache.csv"

# TransCache.csv is written by engine/translatelib.py — that module owns the
# format; these two constants only let us merge two of them without constructing
# a CachedTranslate (which is bound to a repository directory).
_TC_DELIM = "^"
_TC_FIELDS = ["crctext", "translatedtext", "source_lang", "verified"]


def _slug(value):
    """A bare web slug from a trip_name that may carry a '/trips/' prefix."""
    return (value or "").strip().strip("/").rsplit("/", 1)[-1]


# ── publication state ────────────────────────────────────────────────────────
#
# A trip is "published" when it has a row in the master's trip_admin.csv — the
# file that drives the public trip index. It is whole-master state, so we only
# ever READ it here; see D6 in the plan.

def trip_admin_path(master_root):
    return os.path.join(master_root, "admin", "trip_admin.csv") if master_root else ""


def published_state(master_root, web_slug):
    """'yes' | 'no' | 'unknown' — is `web_slug` listed in the master trip index?
    'unknown' when the admin CSV cannot be read, which must never be reported as
    'no': that would claim a live trip is unpublished."""
    path = trip_admin_path(master_root)
    if not path or not os.path.exists(path):
        return "unknown"
    want = _slug(web_slug).lower()
    if not want:
        return "unknown"
    try:
        with open(path, "r", newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                if _slug(row.get("trip_name", "")).lower() == want:
                    return "yes"
    except Exception as e:
        logger.warning(f"trip_transfer: could not read {path}: {e}")
        return "unknown"
    return "no"


# ── export ───────────────────────────────────────────────────────────────────

def _day_dirs(parent):
    """The YYYY-MM-DD subfolders of `parent`, sorted; [] when it doesn't exist."""
    if not parent or not os.path.isdir(parent):
        return []
    import re
    day_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    try:
        return sorted(d for d in os.listdir(parent)
                      if day_re.match(d) and os.path.isdir(os.path.join(parent, d)))
    except OSError:
        return []


def _review_days(trip_root):
    """The days this trip has actually been WORKED on — one folder per day under
    metadata/review. Zero is a perfectly normal state (a trip prepared but not
    yet shot), and the import uses it to know there is nothing to carry, so it
    can stay quiet instead of recommending a copy that has no source."""
    return _day_dirs(os.path.join(trip_root, "metadata", "review"))


def build_manifest(trip_root, master_root="", machine="", master_web_root=""):
    """The export.ini that rides along: identity, publication, and where the
    pictures should land on the target. Returns (ConfigParser, info dict)."""
    ident = resolve(trip_root, kind="trip")
    trip_data = read_trip_ini(trip_root) or {}
    picture_root_abs = resolve_trip_picture_root(trip_root, kind="trip")
    days = _day_dirs(picture_root_abs)
    review_days = _review_days(trip_root)
    published = published_state(master_root, ident.web_slug)

    cfg = ConfigParser()
    cfg["export"] = {
        "format": FORMAT_VERSION,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "machine": machine or platform.node(),
        "tool": "SpeciesViewer",
    }
    cfg["trip"] = {
        "folder": Path(os.path.normpath(trip_root)).name,
        "trip_id": ident.trip_id,
        "web_slug": ident.web_slug,
        "trip_name": trip_data.get("trip_name", ""),
        "fromdate": trip_data.get("fromdate", ""),
        "todate": trip_data.get("todate", ""),
        "title_nl": trip_data.get("title_nl", ""),
        "title_en": trip_data.get("title_en", ""),
        "name_profile": trip_data.get("name_profile", ""),
    }
    # master_web_root travels so the target knows WHERE the shared trip index
    # lives, without having to guess it from its own (possibly unset) config.
    cfg["publication"] = {
        "published": published,
        "web_slug": ident.web_slug,
        "master_web_root": master_web_root or "",
    }
    # D8: the export knows where the pictures live, so it says exactly what to
    # copy. picture_root is stored RELATIVE to each machine's CurrentPictures
    # root, so the same relative path is the answer on the target too.
    cfg["pictures"] = {
        "picture_root": trip_data.get("picture_root", ""),
        "source_folder": picture_root_abs or "",
        "day_count": str(len(days)),
        # Days already worked on at the source. 0 = a trip that has not been
        # shot yet, which is normal and means the import has nothing to ask for.
        "review_days": str(len(review_days)),
        "first_day": days[0] if days else "",
        "last_day": days[-1] if days else "",
    }
    info = {
        "trip_id": ident.trip_id,
        "web_slug": ident.web_slug,
        "published": published,
        "master_web_root": master_web_root or "",
        "picture_root": trip_data.get("picture_root", ""),
        "source_folder": picture_root_abs or "",
        "days": len(days),
        "review_days": len(review_days),
        "folder": cfg["trip"]["folder"],
    }
    return cfg, info


def copy_instruction(info, target_currentpictures_root=""):
    """The one manual step, spelled out (D8). Plain language: what to copy where."""
    rel = info.get("picture_root", "")
    src = info.get("source_folder", "")
    if not info.get("days"):
        # Nothing has been shot yet. That is a normal state for a trip prepared
        # in advance — the whole point of preparing one — so it gets a statement
        # of fact, not an instruction to go and copy something that isn't there.
        return "This trip has no day folders yet, so there is nothing to copy."
    if not src:
        return ("This trip has no picture folder on this machine yet, so there is "
                "nothing to copy.")
    if not rel:
        return (f"Copy the day folders from {src} to this trip's picture folder on "
                f"the other machine.")
    native = rel.replace("/", os.sep)
    days = info.get("days", 0)
    if target_currentpictures_root:
        return (f"Copy {src} to {os.path.join(target_currentpictures_root, native)} "
                f"on the other machine ({days} day folders).")
    # No angle-bracket placeholder here: this string is shown in RichText labels,
    # where Qt's HTML parser silently swallows anything that looks like a tag —
    # "<CurrentPictures root>" vanished and left a path starting mid-way.
    return (f"Copy {src} to the folder {native} under the other machine's "
            f"CurrentPictures root ({days} day folders).")


def export_trip(trip_root, target_dir, master_root="", machine="", master_web_root=""):
    """Write <trip folder>.zip into target_dir. Overwriting an existing ZIP is
    safe. Returns a summary dict; raises ValueError on a bad source/target."""
    if not trip_root or not os.path.isdir(trip_root):
        raise ValueError(f"Trip folder not found: {trip_root!r}")
    if not os.path.isfile(os.path.join(trip_root, "trip.ini")):
        raise ValueError(f"No trip.ini in {trip_root!r} — that is not a trip folder.")
    if not target_dir:
        raise ValueError("No output directory specified.")
    if os.path.normcase(os.path.normpath(trip_root)) == \
            os.path.normcase(os.path.normpath(target_dir)):
        raise ValueError("Source and target are the same directory.")

    cfg, info = build_manifest(trip_root, master_root=master_root, machine=machine,
                               master_web_root=master_web_root)
    trip_data = read_trip_ini(trip_root) or {}

    # The images are whatever trip.ini NAMES — never hardcode tripbanner.jpg.
    wanted = [TRIP_INI_REL, COLLECTION_REL, BASELINE_REL, TRANSCACHE_REL]
    for key in ("trip_picture", "trip_banner"):
        rel = (trip_data.get(key) or "").strip().replace("\\", "/")
        if rel and rel not in wanted:
            wanted.append(rel)

    os.makedirs(target_dir, exist_ok=True)
    zip_path = os.path.join(target_dir, f"{info['folder']}.zip")

    included, missing = [], []
    with zipfile.ZipFile(zip_path, "w") as zf:
        for rel in wanted:
            abs_path = os.path.join(trip_root, rel.replace("/", os.sep))
            if not os.path.isfile(abs_path):
                missing.append(rel)
                continue
            zf.write(abs_path, rel, compress_type=zipfile.ZIP_DEFLATED)
            included.append(rel)
        buf = io.StringIO()
        cfg.write(buf)
        zf.writestr(MANIFEST_NAME, buf.getvalue().encode("utf-8"),
                    compress_type=zipfile.ZIP_DEFLATED)
        included.append(MANIFEST_NAME)

    info["zip_path"] = zip_path
    info["included"] = included
    info["missing"] = missing
    info["size_kb"] = os.path.getsize(zip_path) / 1024
    return info


# ── inspect ──────────────────────────────────────────────────────────────────

def inspect_zip(zip_path):
    """Read the manifest + file list WITHOUT extracting anything, so the import
    dialog can show what it is about to do. Returns a dict; raises ValueError
    when this is not a trip export."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if MANIFEST_NAME not in names:
            raise ValueError(
                "This ZIP has no export.ini — it is not a trip export. "
                "(An album export is imported with Import Album.)")
        text = zf.read(MANIFEST_NAME).decode("utf-8")
    cfg = ConfigParser()
    cfg.read_string(text)
    flat = {}
    for section in cfg.sections():
        for key, value in cfg.items(section):
            flat[key] = value
    flat["_names"] = names
    flat["_has_collection"] = COLLECTION_REL in names
    flat["_has_baseline"] = BASELINE_REL in names
    flat["_has_transcache"] = TRANSCACHE_REL in names
    flat["_images"] = [n for n in names
                       if "/" not in n and n.lower().endswith((".jpg", ".jpeg", ".png"))]
    return flat


# ── import ───────────────────────────────────────────────────────────────────

def _backup(dest_root, rel):
    """Move an existing file into <trip>/.trash/ before it is replaced. Nothing
    this tool touches is ever deleted outright (D3)."""
    src = os.path.join(dest_root, rel.replace("/", os.sep))
    if not os.path.isfile(src):
        return ""
    trash = os.path.join(dest_root, ".trash")
    os.makedirs(trash, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(trash, f"{Path(rel).name}.{stamp}")
    try:
        shutil.copy2(src, target)
        return target
    except Exception as e:
        logger.warning(f"trip_transfer: could not back up {src}: {e}")
        return ""


def restore_identity(zip_path, dest_root):
    """Extract trip.ini + the trip images into dest_root, backing up what is
    already there. Runs BEFORE the repository is built: sv1MakeReposity reads
    trip.ini, exactly as Create Trip writes it before running the batch."""
    os.makedirs(dest_root, exist_ok=True)
    restored, backups = [], []
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        targets = [TRIP_INI_REL] + [
            n for n in names
            if "/" not in n and n.lower().endswith((".jpg", ".jpeg", ".png"))]
        for rel in targets:
            if rel not in names:
                continue
            b = _backup(dest_root, rel)
            if b:
                backups.append(b)
            zf.extract(rel, dest_root)
            restored.append(rel)
    return restored, backups


def _comparable(record):
    """A species record without its DERIVED parts, for "did this really change?".
    Leaves is rebuilt on every read from the children's Up pointers."""
    return {k: v for k, v in record.items() if k != "Leaves"}


def merge_species_collection(zip_path, dest_root, backup=True):
    """Merge the exported collection into the target's, ZIP WINS on conflict
    (D2) — it is the side the work was just done on. Returns a dict with the
    counts, or None when the ZIP carries no collection.

    mergeSpeciesCollections keeps what `self` already holds, so the ZIP is read
    into `self` and the local collection merged IN. Same shape as Install Small
    Master. Nothing is ever dropped: the result is the union.

    `backup=False` on a FRESH import: the only thing to back up there is the
    empty template sv1MakeReposity just wrote, and a .trash folder in a
    brand-new trip invites the question "what did I lose?" — nothing.
    """
    from engine.speciesclassification import SpeciesClassification

    with zipfile.ZipFile(zip_path, "r") as zf:
        if COLLECTION_REL not in zf.namelist():
            return None
        payload = zf.read(COLLECTION_REL)

    dest_csv = os.path.join(dest_root, COLLECTION_REL.replace("/", os.sep))
    os.makedirs(os.path.dirname(dest_csv), exist_ok=True)

    tmp = tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False)
    try:
        tmp.write(payload)
        tmp.close()
        sc_zip = SpeciesClassification(None)
        sc_zip.clearDict()
        sc_zip.readSpeciesCollection(tmp.name, donotaddLanguageIndices=True)
    finally:
        os.unlink(tmp.name)

    from_zip = len(sc_zip.sc_dict)
    added = 0
    overridden = 0
    if os.path.isfile(dest_csv):
        sc_local = SpeciesClassification(None)
        sc_local.clearDict()
        sc_local.readSpeciesCollection(dest_csv, donotaddLanguageIndices=True)
        zip_keys = set(sc_zip.sc_dict)
        added = sum(1 for k in sc_local.sc_dict if k not in zip_keys)
        # Same species, different content on the two sides: the ZIP's version is
        # kept. Counted so the report can say so out loud rather than silently
        # discarding the other machine's edit — which means the count has to be
        # TRUE. 'Leaves' is excluded: readSpeciesCollection rebuilds it from the
        # children's Up pointers, so adding one field species makes every
        # ancestor "differ" and would report a scary number for no real change.
        overridden = sum(1 for k in sc_local.sc_dict
                         if k in zip_keys
                         and _comparable(sc_local.sc_dict[k]) != _comparable(sc_zip.sc_dict[k]))
        if backup:
            _backup(dest_root, COLLECTION_REL)
        sc_zip.mergeSpeciesCollections(sc_local.scCopy())
    sc_zip.writeSpeciesCollection(dest_csv)
    return {"from_zip": from_zip, "kept_local": added,
            "overridden": overridden, "total": len(sc_zip.sc_dict)}


def restore_baseline(zip_path, dest_root):
    """Put the trip's frozen lifer baseline back — but NEVER over one that is
    already there. The baseline is written once and never touched again
    (engine/species_baseline.py); regenerating it from a partial master would
    silently turn known species into lifers. Returns 'restored'|'kept'|'absent'.
    """
    dest = os.path.join(dest_root, BASELINE_REL.replace("/", os.sep))
    if os.path.isfile(dest):
        return "kept"
    with zipfile.ZipFile(zip_path, "r") as zf:
        if BASELINE_REL not in zf.namelist():
            return "absent"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        zf.extract(BASELINE_REL, dest_root)
    return "restored"


def pull_trip_admin(base_url, master_web_root, master_root):
    """Refresh THIS machine's master/admin/trip_admin.csv from the published one.

    Import pulls; it never pushes (D6). trip_admin.csv lists every published
    trip, so a copy written from a machine that only knows about some trips
    would un-advertise the rest the next time it was uploaded. The web copy is
    the authoritative one, so overwriting the local file with it is safe in this
    direction — and the old file still goes to .trash first.

    Returns (rows_written, message). rows_written is None when nothing was
    fetched; the message always says why, because "publication unknown" must
    never be shown as "not published".
    """
    if not base_url or not master_web_root:
        return None, ("No website address configured, so the published trip list "
                      "could not be refreshed.")
    if not master_root or not os.path.isdir(master_root):
        return None, "No master database on this machine to store the trip list in."
    url = (base_url.rstrip("/") + master_web_root.rstrip("/")
           + "/includes%20web/trip_admin.csv")
    try:
        import requests
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        text = resp.text
    except Exception as e:
        return None, f"Could not read the published trip list ({e})."

    rows = [r for r in csv.DictReader(io.StringIO(text), delimiter=";")
            if (r.get("trip_name") or "").strip()]
    if not rows:
        # An empty index would un-advertise everything on the next upload — a
        # fetch that yields nothing is a failure, not an answer.
        return None, "The published trip list came back empty; keeping the local copy."

    dest = trip_admin_path(master_root)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    _backup(master_root, "admin/trip_admin.csv")
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        fh.write(text)
    return len(rows), f"Refreshed the published trip list from the website ({len(rows)} trips)."


def _read_transcache(path_or_bytes):
    """{crc: row} from a TransCache.csv given a path or its bytes."""
    if isinstance(path_or_bytes, bytes):
        stream = io.StringIO(path_or_bytes.decode("utf-8", errors="replace"), newline="")
    else:
        if not os.path.isfile(path_or_bytes):
            return {}
        with open(path_or_bytes, "r", newline="", encoding="utf-8",
                  errors="replace") as fh:
            stream = io.StringIO(fh.read(), newline="")
    out = {}
    reader = csv.reader(stream, delimiter=_TC_DELIM)
    next(reader, None)  # header
    for values in reader:
        if not values or not values[0].strip():
            continue
        out[values[0].strip()] = {
            "translatedtext": values[1] if len(values) > 1 else "",
            "source_lang": values[2] if len(values) > 2 else "",
            "verified": values[3] if len(values) > 3 else "0",
        }
    return out


def merge_transcache(zip_path, dest_root):
    """Merge the trip's own translation cache. ZIP wins — EXCEPT that a
    hand-`verified` entry is never downgraded by an unverified one, in either
    direction. Losing a verification is losing work a person actually did.
    Returns a dict of counts, or None when the ZIP carries no cache."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        if TRANSCACHE_REL not in zf.namelist():
            return None
        incoming = _read_transcache(zf.read(TRANSCACHE_REL))

    dest = os.path.join(dest_root, TRANSCACHE_REL.replace("/", os.sep))
    local = _read_transcache(dest)

    merged = dict(local)
    added = updated = kept_verified = 0
    for crc, row in incoming.items():
        current = merged.get(crc)
        if current is None:
            merged[crc] = row
            added += 1
            continue
        if current.get("verified") == "1" and row.get("verified") != "1":
            kept_verified += 1
            continue
        if current != row:
            merged[crc] = row
            updated += 1

    if os.path.isfile(dest):
        _backup(dest_root, TRANSCACHE_REL)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_TC_FIELDS,
                                quoting=csv.QUOTE_MINIMAL, delimiter=_TC_DELIM)
        writer.writeheader()
        for crc, row in merged.items():
            writer.writerow({"crctext": crc, **row})
    return {"added": added, "updated": updated,
            "kept_verified": kept_verified, "total": len(merged)}
