"""Backfill place names for master pictures that have a coordinate but no place.

The published species map ("Where I have seen this species") showed a run of dots
labelled only `41.3455, -0.3424`, because those pictures carry GPS and nothing
else: Location, City, Municipality, State and Country are ALL empty. They were
never reverse-geocoded. Master-wide that is ~2,415 pictures (5.9% of everything
with a coordinate).

The user's call: fix the DATA, don't work around it in the page.

Two things make this practical rather than a 40-minute grind:

* PICTURES SHARE FIXES. The 2,415 pictures sit on only ~534 distinct coordinates
  (a burst, or a morning at one hide, is one fix). Nominatim's usage policy allows
  about one request a second, so geocoding per PICTURE would take ~40 minutes and
  hammer a free service; per COORDINATE it is ~9 minutes. Group first, then ask.

* WE ONLY TOUCH THE EMPTY ONES. A picture with any place text is left alone, so
  this can never overwrite a name you typed by hand. That is what makes it safe to
  run over the whole master.

The write is in two halves, and BOTH are needed:
  1. the picture's OWN truth (its .xmp sidecar, or the file itself when it is
     self-describing — see _write_targets), via geolocate.write_location_batch;
  2. the master index (DB + data_pictures.csv), via MasterDB.apply_location_fields
     — because the website, the maps and the viewers read the index, not the
     sidecars. Skip it and nothing visibly changes until a full master re-read.

Report-only by default: `plan()` asks Nominatim and returns what it WOULD write;
`apply()` writes only the rows the caller confirmed.
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("speciesviewer")

LOCATION_COLUMNS = ("Location", "City", "Municipality", "State", "Country")

# Nominatim asks for <= 1 request/second from a single source. We honour it.
_MIN_INTERVAL_S = 1.05


def _has_place(row) -> bool:
    return any((row[c] or "").strip() for c in LOCATION_COLUMNS)


def find_gaps(db) -> dict:
    """{GPSPosition: [picture rows]} for every master picture that has a
    coordinate and NO place text at all. Read-only."""
    con = db._connect()
    rows = con.execute(
        'SELECT "UniquePictureName","GPSPosition","Location","City",'
        '       "Municipality","State","Country","WetSoortNaam","DateTimeOriginal"'
        '  FROM pictures WHERE TRIM(COALESCE("GPSPosition","")) <> ""').fetchall()
    gaps: dict = {}
    for r in rows:
        if _has_place(r):
            continue
        gaps.setdefault(r["GPSPosition"], []).append(dict(r))
    return gaps


def plan(db, gaps: dict, *, lang: str = "en", zoom: int = 14,
         progress_cb=None, should_stop=None) -> list[dict]:
    """Reverse-geocode each DISTINCT coordinate. Returns one row per coordinate:
    {gps, lat, lon, fields, place, upns, n, error}. Writes nothing.

    `progress_cb(done, total, place)` drives the UI; `should_stop()` lets the user
    cancel a nine-minute run without losing what has already been resolved."""
    from engine.geo import parse_gps_dms
    from gui.geolocate import reverse_geocode, place_fields

    out: list[dict] = []
    total = len(gaps)
    last_call = 0.0
    for i, (gps, pics) in enumerate(sorted(gaps.items(), key=lambda kv: -len(kv[1]))):
        if should_stop and should_stop():
            break
        ll = parse_gps_dms(gps)
        row = {"gps": gps, "lat": None, "lon": None, "fields": {}, "place": "",
               "upns": [p["UniquePictureName"] for p in pics], "n": len(pics),
               "error": ""}
        if not ll:
            row["error"] = "coordinate could not be read"
            out.append(row)
            continue
        row["lat"], row["lon"] = ll
        # Rate-limit against the WALL CLOCK, not with a flat sleep: a slow lookup
        # has already paid the interval, so we don't add another second to it.
        wait = _MIN_INTERVAL_S - (time.monotonic() - last_call)
        if wait > 0:
            time.sleep(wait)
        last_call = time.monotonic()
        try:
            res = reverse_geocode(ll[0], ll[1], lang=lang, zoom=zoom)
            fields = place_fields(res) if res else {}
            row["fields"] = {k: v for k, v in (fields or {}).items()
                             if k in LOCATION_COLUMNS and (v or "").strip()}
            row["place"] = ", ".join(
                v for v in (row["fields"].get(c, "") for c in
                            ("Location", "City", "State", "Country")) if v)
            if not row["fields"]:
                row["error"] = "no place found for this coordinate"
        except Exception as e:
            row["error"] = str(e)
            logger.warning(f"geo backfill: {gps}: {e}")
        out.append(row)
        if progress_cb:
            progress_cb(i + 1, total, row["place"] or row["error"])
    return out


def _write_targets(db, upns: list[str]) -> list[str]:
    """The files to stamp for these UPNs. Mirrors the viewer's location_targets
    rule (SidecarCollection.location_targets = the group's "meta" file plus its
    JPG) — never write into a RAW.

    A SELF-DESCRIBING picture (HEIC/HEIF/PNG) holds its own annotations, so the
    FILE is the target and wins over any .xmp beside it. That sidecar is not the
    truth for such a file: PhotoLab and Lightroom read and write the place inside
    the picture, so a stray .xmp (ingest briefly wrote one for every HEIC on
    2026-09-23) would silently swallow the write.

    But "this UPN contains a .png" is NOT the same question. A master UPN gathers
    the WHOLE derivative chain, so a DxO edit can put a .png or .tif next to an
    ORF and its .xmp — there the PNG is a RENDER and the RAW's .xmp is still the
    truth. The picture is self-describing only when NOTHING in the UPN delegates,
    i.e. there is no RAW (the same test as preview_provider._heic_only_upn).
    Everything else delegates to its .xmp, and a JPG-only picture has nowhere to
    put it but itself.
    """
    from utils.raw_extensions import RAW, SELF_DESCRIBING

    con = db._connect()
    targets: list[str] = []
    for upn in upns:
        rows = [dict(r) for r in con.execute(
            'SELECT "WindowsFileName","WindowsExtension" FROM files '
            ' WHERE "UniquePictureName"=?', (upn,))]
        by_ext = lambda exts: [r["WindowsFileName"] for r in rows
                               if "." + (r["WindowsExtension"] or "").lower() in exts]
        own = by_ext(SELF_DESCRIBING)
        jpg = by_ext({".jpg", ".jpeg"})
        if own and not by_ext(RAW):
            targets.extend(own + jpg)
            continue
        targets.extend(by_ext({".xmp"}) or jpg + own)
    return [t for t in targets if t and Path(t).is_file()]


def backup_pictures_csv(master_root) -> str:
    """Dated copy of data_pictures.csv before we rewrite it. The DB journal is the
    real safety net, but a file you can open in Excel is the one you actually reach
    for when something looks wrong."""
    src = Path(master_root) / "metadata" / "active" / "data_pictures.csv"
    if not src.is_file():
        return ""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = src.with_name(f"data_pictures_{stamp}_pre-geocode.csv")
    shutil.copy2(src, dst)
    logger.info(f"geo backfill: backed up {src.name} -> {dst.name}")
    return str(dst)


def apply(db, master_root, rows: list[dict], *, progress_cb=None) -> dict:
    """Write the confirmed rows: each picture's own truth first (see
    _write_targets), then the master index (so the website and maps see it
    without a re-read). Returns a summary."""
    from gui.geolocate import write_location_batch

    rows = [r for r in rows if r.get("fields") and not r.get("error")]
    if not rows:
        return {"pictures": 0, "coordinates": 0, "files": 0, "backup": ""}

    backup = backup_pictures_csv(master_root)
    written_files = 0
    index_updates: dict = {}
    for i, r in enumerate(rows):
        files = _write_targets(db, r["upns"])
        if files:
            # ONE exiftool call per coordinate, however many pictures share it.
            write_location_batch(files, r["fields"], overwrite=True)
            written_files += len(files)
        for upn in r["upns"]:
            index_updates[upn] = dict(r["fields"])
        if progress_cb:
            progress_cb(i + 1, len(rows), r["place"])

    pictures = db.apply_location_fields(index_updates)
    return {"pictures": pictures, "coordinates": len(rows),
            "files": written_files, "backup": backup}
