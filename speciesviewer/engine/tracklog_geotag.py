"""Tracklog geotag — fill missing GPS and/or altitude for master pictures from
a GPX track or an OM-1 NMEA `.log`, matched to each photo by time.

The camera-tagged half was already solved (OM-1 tags lat/lon live via
Bluetooth); the two gaps this closes are:

  * a day the camera GPS wasn't enabled at all -> no lat/lon on the photo;
  * GPSAltitude, which the camera never writes even when GPS is enabled.

Correlation is purely a DateTimeOriginal <-> tracklog-timestamp join —
DateTimeOriginal is already in the master index, so this needs ZERO EXIF file
reads; the only file touch in the whole flow is the final write. Full design:
docs/plans/maps_plan.md, "2a" and its altitude-source addendum.

Same shape as engine/geo_backfill.py: plan() looks and proposes, writes
nothing; apply() writes only the rows the caller confirmed.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("speciesviewer")

GPS_ACTION = "gps+alt"       # photo had no GPS at all -> write lat/lon (+ altitude)
ALT_ACTION = "alt-only"      # photo already has GPS -> altitude only


def load_track(paths: list[str], *, window_s: int = 300, sd_mult: float = 2.0) -> list[dict]:
    """Parse one or more tracklog files (`.log` = OM-1 NMEA, `.gpx` = GPX),
    merge, sort by time, and clean the altitude series. Raises ValueError if
    a file's extension isn't recognised or nothing could be parsed."""
    from engine.geo import parse_gpx_track, parse_om1_nmea_log, clean_altitude_series

    points: list[dict] = []
    for p in paths:
        ext = Path(p).suffix.lower()
        if ext == ".gpx":
            points.extend(parse_gpx_track(p))
        elif ext == ".log":
            points.extend(parse_om1_nmea_log(p))
        else:
            raise ValueError(f"unrecognised tracklog file type: {Path(p).name}")
    if not points:
        raise ValueError("no usable points found in the chosen file(s)")
    points.sort(key=lambda pt: pt["t_utc"])
    return clean_altitude_series(points, window_s=window_s, sd_mult=sd_mult)


def track_date_range(points: list[dict]):
    """(from_date, to_date) as 'YYYY-MM-DD' strings covering the track, in
    the LOCAL date of each point's UTC instant (good enough for a day-range
    query — a track never spans a boundary ambiguity that matters here)."""
    if not points:
        return None, None
    lo = min(p["t_utc"] for p in points).date().isoformat()
    hi = max(p["t_utc"] for p in points).date().isoformat()
    return lo, hi


def _correlate(key: str, date_str: str, has_gps: bool, points: list[dict], *,
               camera_utc_offset, max_gap_s: float, species: str = "") -> dict | None:
    """One picture's correlation against the cleaned track — the join Phase 1
    and the entries-based path share. `date_str` is DateTimeOriginal (local
    camera time, 'YYYY:MM:DD HH:MM:SS...'); `camera_utc_offset` is a
    datetime.timezone. Returns a row, or None if there's nothing to propose
    (no usable date, nothing within max_gap_s, or the photo already has GPS
    and the track carries no altitude for it)."""
    import datetime as dt
    from engine.geo import nearest_track_point

    date_str = (date_str or "").strip()
    if not date_str:
        return None
    try:
        naive = dt.datetime.strptime(date_str[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    t_utc = naive.replace(tzinfo=camera_utc_offset).astimezone(dt.timezone.utc)
    hit = nearest_track_point(points, t_utc, max_gap_s)
    if hit is None:
        return None
    proposed_alt = hit["alt_clean"]
    action = ALT_ACTION if has_gps else GPS_ACTION
    if action == ALT_ACTION and proposed_alt is None:
        return None  # nothing to add for a photo that already has GPS
    return {
        "upn": key,
        "species": species,
        "when": date_str,
        "has_gps": has_gps,
        "proposed_lat": hit["lat"] if action == GPS_ACTION else None,
        "proposed_lon": hit["lon"] if action == GPS_ACTION else None,
        "proposed_alt": round(proposed_alt, 1) if proposed_alt is not None else None,
        "gap_s": round(hit["gap_s"]),
        "action": action,
    }


def plan(db, points: list[dict], *, camera_utc_offset, max_gap_s: float = 900) -> list[dict]:
    """For every master picture in the track's covered date range, correlate
    its DateTimeOriginal against the cleaned track and propose a row.
    Pictures with nothing within `max_gap_s` are simply not returned — the
    "silent-optional" cutoff, no garbage matches. Writes nothing.

    Returns rows: {upn, species, when, has_gps, proposed_lat, proposed_lon,
    proposed_alt, gap_s, action}."""
    from_date, to_date = track_date_range(points)
    if not from_date:
        return []
    by_day = db.pictures_in_daterange(from_date, to_date)

    out: list[dict] = []
    for day_rows in by_day.values():
        for r in day_rows:
            row = _correlate(
                r["UniquePictureName"], r.get("DateTimeOriginal"),
                bool((r.get("GPSPosition") or "").strip()), points,
                camera_utc_offset=camera_utc_offset, max_gap_s=max_gap_s,
                species=r.get("WetSoortNaam") or "")
            if row is not None:
                out.append(row)
    out.sort(key=lambda r: r["when"])
    return out


def plan_entries(entries: list[dict], points: list[dict], *, camera_utc_offset,
                 max_gap_s: float = 900) -> list[dict]:
    """Same correlation as plan(), over an already-known set of pictures
    instead of a master date-range query — for the Read-Card path, where the
    candidates are exactly the files a card read just produced (each entry:
    {"key":, "date":, "has_gps":}, built by the caller from a batched exiftool
    read, e.g. gui.geolocate.batch_datetime_gps)."""
    out: list[dict] = []
    for e in entries:
        row = _correlate(e["key"], e.get("date"), bool(e.get("has_gps")), points,
                         camera_utc_offset=camera_utc_offset, max_gap_s=max_gap_s)
        if row is not None:
            out.append(row)
    out.sort(key=lambda r: r["when"])
    return out


def _write_rows(rows: list[dict], resolve_files, *, progress_cb=None) -> dict:
    """Shared grouping + write loop: GPS rows grouped by identical rounded
    position (a burst shares one fix); altitude rows grouped by identical
    rounded value (the rolling average is piecewise-constant across a window,
    so many photos naturally share one) — the same "534 calls not 2,415"
    economy as write_location_batch. `resolve_files(upns) -> [file paths]` is
    the one thing that differs between the master and entries-based callers.

    Returns {"gps_files":, "altitude_files":, "gps_by_upn":, "alt_by_upn":}."""
    from gui.geolocate import write_gps, write_altitude

    rows = [r for r in rows if r.get("action") in (GPS_ACTION, ALT_ACTION)]
    gps_by_upn: dict = {}
    alt_by_upn: dict = {}
    gps_groups: dict = {}       # (lat, lon) rounded -> [upn]
    alt_groups: dict = {}       # alt rounded -> [upn]
    for r in rows:
        if r["action"] == GPS_ACTION and r["proposed_lat"] is not None:
            key = (round(r["proposed_lat"], 5), round(r["proposed_lon"], 5))
            gps_groups.setdefault(key, []).append(r["upn"])
            gps_by_upn[r["upn"]] = (r["proposed_lat"], r["proposed_lon"])
        if r["proposed_alt"] is not None:
            key = round(r["proposed_alt"], 1)
            alt_groups.setdefault(key, []).append(r["upn"])
            alt_by_upn[r["upn"]] = r["proposed_alt"]

    gps_files = alt_files = 0
    total_groups = len(gps_groups) + len(alt_groups)
    done = 0
    for (lat, lon), upns in gps_groups.items():
        files = resolve_files(upns)
        if files:
            gps_files += write_gps(files, lat, lon)
        done += 1
        if progress_cb:
            progress_cb(done, total_groups, f"{lat:.5f}, {lon:.5f}")
    for alt, upns in alt_groups.items():
        files = resolve_files(upns)
        if files:
            alt_files += write_altitude(files, alt)
        done += 1
        if progress_cb:
            progress_cb(done, total_groups, f"{alt:.1f} m")

    return {"gps_files": gps_files, "altitude_files": alt_files,
           "gps_by_upn": gps_by_upn, "alt_by_upn": alt_by_upn}


def apply(db, master_root, rows: list[dict], *, progress_cb=None) -> dict:
    """Write the confirmed rows: sidecars first (the truth), then the master
    index. Returns a summary dict."""
    from engine.geo_backfill import backup_pictures_csv, _write_targets

    rows = [r for r in rows if r.get("action") in (GPS_ACTION, ALT_ACTION)]
    if not rows:
        return {"pictures": 0, "gps_files": 0, "altitude_files": 0, "backup": ""}

    backup = backup_pictures_csv(master_root)
    result = _write_rows(rows, lambda upns: _write_targets(db, upns), progress_cb=progress_cb)
    gps_by_upn, alt_by_upn = result["gps_by_upn"], result["alt_by_upn"]

    # Explicit full-coverage master-index write, same reason geo_backfill.apply
    # does it: _write_targets can come back empty for an upn with no sidecar/
    # image on disk, in which case the mirror inside write_gps/write_altitude
    # (which only sees the files it actually wrote) would silently miss it.
    if gps_by_upn:
        from gui.geolocate import GPS_MANUAL
        db.apply_gps(gps_by_upn, method=GPS_MANUAL)
    if alt_by_upn:
        db.apply_altitude(alt_by_upn)

    pictures = len(set(gps_by_upn) | set(alt_by_upn))
    return {"pictures": pictures, "gps_files": result["gps_files"],
           "altitude_files": result["altitude_files"], "backup": backup}


def apply_entries(files_by_key: dict, rows: list[dict], *, progress_cb=None) -> dict:
    """Write the confirmed rows straight to their sidecars — no master
    involved (these pictures aren't in one yet). `files_by_key` maps each
    row's `upn` (really the entry's `key`) to its writable file list, e.g.
    `{stem: [xmp_path]}`. Returns a summary dict (no `"backup"` key)."""
    result = _write_rows(
        rows, lambda upns: [f for u in upns for f in files_by_key.get(u, [])],
        progress_cb=progress_cb)
    pictures = len(set(result["gps_by_upn"]) | set(result["alt_by_upn"]))
    return {"pictures": pictures, "gps_files": result["gps_files"],
           "altitude_files": result["altitude_files"]}


class MasterSource:
    """Adapter over the master-scoped plan()/apply(), for TracklogGeotagDialog."""

    confirm_note = "data_pictures.csv is backed up first."

    def __init__(self, db, master_root):
        self._db, self._master_root = db, master_root

    def plan(self, points, camera_utc_offset, max_gap_s):
        return plan(self._db, points, camera_utc_offset=camera_utc_offset, max_gap_s=max_gap_s)

    def apply(self, rows):
        return apply(self._db, self._master_root, rows)


class FilesSource:
    """Adapter over the entries-based plan_entries()/apply_entries(), for
    TracklogGeotagDialog — the Read-Card path (pictures not yet in a master).
    `entries`: [{"key":, "date":, "has_gps":}, …]. `files_by_key`: {key:
    [writable file paths]}."""

    confirm_note = ""

    def __init__(self, entries: list[dict], files_by_key: dict, label: str = ""):
        self._entries, self._files_by_key, self.label = entries, files_by_key, label

    def plan(self, points, camera_utc_offset, max_gap_s):
        return plan_entries(self._entries, points,
                            camera_utc_offset=camera_utc_offset, max_gap_s=max_gap_s)

    def apply(self, rows):
        return apply_entries(self._files_by_key, rows)
