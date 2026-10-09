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


def _altitude_already_close(cur_alt, proposed_alt, *, rel_tol: float = 0.05,
                            abs_tol: float = 0.5) -> bool:
    """True if `cur_alt` (already on the photo) and `proposed_alt` (from the
    tracklog) are close enough that re-writing would be a no-op in substance —
    re-running the same geotag against a photo that already has an altitude
    shouldn't re-propose it just because the rolling average recomputed a
    float a few centimetres off. abs_tol covers low altitudes where a percent
    tolerance would be unreasonably tight (e.g. 2.0 m vs 2.1 m near sea level)."""
    import math
    if cur_alt is None or proposed_alt is None:
        return False
    return math.isclose(cur_alt, proposed_alt, rel_tol=rel_tol, abs_tol=abs_tol)


def _correlate(key: str, date_str: str, has_gps: bool, points: list[dict], *,
               camera_utc_offset, max_gap_s: float, species: str = "",
               cur_alt: float | None = None) -> tuple[dict | None, str | None]:
    """One picture's correlation against the cleaned track — the join Phase 1
    and the entries-based path share. `date_str` is DateTimeOriginal (local
    camera time, 'YYYY:MM:DD HH:MM:SS...'); `camera_utc_offset` is a
    datetime.timezone. `cur_alt` is the photo's current GPSAltitude (if any),
    so a re-run against an already-geotagged photo doesn't re-propose a value
    that's already close enough.

    Returns (row, reason): row is None when there's nothing to propose, and
    `reason` then distinguishes why — for the dialog's empty-result message,
    so it doesn't attribute a specific cause it can't actually confirm:
      "outside_range" — the photo's time falls entirely before/after the
                        track's own covered span (a day the track doesn't
                        cover at all — not a near-miss, not a bug).
      "gap"           — within the track's span, but no point fell inside
                        max_gap_s (a genuine tracklog gap), the date was
                        missing/unparseable, or the track carries no clean
                        altitude at this point.
      "no_change"     — the photo already has GPS/altitude close enough to
                        what the track would propose."""
    import datetime as dt
    from engine.geo import nearest_track_point

    date_str = (date_str or "").strip()
    if not date_str:
        return None, "gap"
    try:
        naive = dt.datetime.strptime(date_str[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, "gap"
    t_utc = naive.replace(tzinfo=camera_utc_offset).astimezone(dt.timezone.utc)
    hit = nearest_track_point(points, t_utc, max_gap_s)
    if hit is None:
        if points and (t_utc < points[0]["t_utc"] or t_utc > points[-1]["t_utc"]):
            return None, "outside_range"
        return None, "gap"
    proposed_alt = hit["alt_clean"]
    action = ALT_ACTION if has_gps else GPS_ACTION
    if action == ALT_ACTION and proposed_alt is None:
        return None, "gap"  # nothing to add for a photo that already has GPS
    if action == ALT_ACTION and _altitude_already_close(cur_alt, proposed_alt):
        return None, "no_change"  # already has (close enough to) this altitude
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
    }, None


def plan(db, points: list[dict], *, camera_utc_offset, max_gap_s: float = 900
         ) -> tuple[list[dict], dict]:
    """For every master picture in the track's covered date range, correlate
    its DateTimeOriginal against the cleaned track and propose a row.
    Pictures with nothing within `max_gap_s` are simply not returned — the
    "silent-optional" cutoff, no garbage matches. Writes nothing.

    Returns (rows, stats): rows are {upn, species, when, has_gps, proposed_lat,
    proposed_lon, proposed_alt, gap_s, action}; stats = {"considered": N,
    "outside_range": N, "gap": N, "no_change": N} lets the caller explain an
    empty result accurately (see _correlate's `reason`) instead of assuming
    one specific cause."""
    from_date, to_date = track_date_range(points)
    if not from_date:
        return [], {"considered": 0}
    by_day = db.pictures_in_daterange(from_date, to_date)

    out: list[dict] = []
    stats: dict = {"considered": 0}
    for day_rows in by_day.values():
        for r in day_rows:
            stats["considered"] += 1
            try:
                cur_alt = float(r.get("GPSAltitude")) if str(r.get("GPSAltitude") or "").strip() else None
            except ValueError:
                cur_alt = None
            row, reason = _correlate(
                r["UniquePictureName"], r.get("DateTimeOriginal"),
                bool((r.get("GPSPosition") or "").strip()), points,
                camera_utc_offset=camera_utc_offset, max_gap_s=max_gap_s,
                species=r.get("WetSoortNaam") or "", cur_alt=cur_alt)
            if row is not None:
                out.append(row)
            elif reason:
                stats[reason] = stats.get(reason, 0) + 1
    out.sort(key=lambda r: r["when"])
    return out, stats


def plan_entries(entries: list[dict], points: list[dict], *, camera_utc_offset,
                 max_gap_s: float = 900) -> tuple[list[dict], dict]:
    """Same correlation as plan(), over an already-known set of pictures
    instead of a master date-range query — for the Read-Card path, where the
    candidates are exactly the files a card read just produced (each entry:
    {"key":, "date":, "has_gps":, "cur_alt":}, built by the caller from a
    batched exiftool read, e.g. gui.geolocate.batch_datetime_gps).

    Returns (rows, stats) — see plan()."""
    out: list[dict] = []
    stats: dict = {"considered": 0}
    for e in entries:
        stats["considered"] += 1
        row, reason = _correlate(e["key"], e.get("date"), bool(e.get("has_gps")), points,
                                 camera_utc_offset=camera_utc_offset, max_gap_s=max_gap_s,
                                 cur_alt=e.get("cur_alt"))
        if row is not None:
            out.append(row)
        elif reason:
            stats[reason] = stats.get(reason, 0) + 1
    out.sort(key=lambda r: r["when"])
    return out, stats


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


def _find_day_tracklogs_by_name(day: str, root: str) -> list[str]:
    """Every tracklog for `day` in `<root>/<year>/` — OM-1 `.log` and phone
    `.gpx` files whose name STARTS WITH the day's `yyyymmdd` stem.

    `<yyyymmdd>.log` is the common case, but a phone names every export just
    after the date, so a second session the same day would overwrite the
    first. Anything appended after the stem is accepted — `<yyyymmdd>a.log`,
    `<yyyymmdd>-afternoon.log`, `<yyyymmdd>_2.gpx` — and all matches are loaded
    and merged. `day` is 'YYYY-MM-DD'. `.log` files sort before `.gpx`, then by
    name. Empty list when the folder or `root` is missing, or nothing matches."""
    if not root or not day:
        return []
    year = day[:4]
    stem = day.replace("-", "")
    folder = Path(root) / year
    if not folder.is_dir():
        return []
    out: list[str] = []
    for p in folder.iterdir():
        if not p.is_file() or p.suffix.lower() not in (".log", ".gpx"):
            continue
        if not p.name.startswith(stem):
            continue
        # The character right after the date stem must not be a digit, so a
        # neighbouring day can't slip in on a shared numeric prefix.
        if p.name[len(stem):len(stem) + 1].isdigit():
            continue
        out.append(str(p))
    return sorted(out, key=lambda s: (Path(s).suffix.lower() != ".log", Path(s).name))


def find_day_tracklogs(day: str, root: str) -> list[str]:
    """Every tracklog that covers `day`: first by filename (see
    `_find_day_tracklogs_by_name`), then — only when that finds nothing — by
    checking whether a log named for the day BEFORE or AFTER actually runs
    into `day`. A tracklog recorder left running past midnight is named for
    the day it started, so its own filename never matches the second day; the
    only way to know it covers `day` is to parse it and look at its real
    timestamps (`track_date_range`). Each neighbour file is parsed on its own,
    so only the file(s) whose content actually spans `day` are included —
    not every file the neighbour day's filename match returns. Bounded to the
    two adjacent days, never a folder-wide scan. `day` is 'YYYY-MM-DD'."""
    matches = _find_day_tracklogs_by_name(day, root)
    if matches:
        return matches
    from datetime import date, timedelta
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return []
    out: list[str] = []
    for neighbor in (d - timedelta(days=1), d + timedelta(days=1)):
        for p in _find_day_tracklogs_by_name(neighbor.isoformat(), root):
            try:
                points = load_track([p])
            except ValueError:
                continue
            lo, hi = track_date_range(points)
            if lo is not None and lo <= day <= hi:
                out.append(p)
    return sorted(out, key=lambda s: (Path(s).suffix.lower() != ".log", Path(s).name))


def find_day_tracklog(day: str, root: str) -> str | None:
    """The first tracklog for `day` (see find_day_tracklogs) — for callers that
    only need to know whether one exists, or a name to show. The geotag passes
    use find_day_tracklogs and merge every match. None if there is none."""
    logs = find_day_tracklogs(day, root)
    return logs[0] if logs else None


def auto_geotag_entries(entries_by_day: dict[str, list[dict]], files_by_key: dict, *,
                        root: str, max_gap_s: float = 900) -> dict:
    """Silent, no-review counterpart to plan_entries()+apply_entries(), for
    the Read dialog's automatic per-day tracklog pass. `entries_by_day`:
    `{day ('YYYY-MM-DD'): [{"key":, "date":, "has_gps":, "cur_alt":}, …]}` —
    grouped by the caller from the copy-time day-folder decision (see
    gui/trip_process.py), NOT re-derived here from raw DateTimeOriginal.

    For each day: find its log(s) (find_day_tracklogs — every file whose name
    starts with the day's stem); if found, load and clean the merged track,
    then take the camera's UTC offset FROM THE PICTURES THEMSELVES
    (engine.geo.camera_offset_from_dates, i.e. EXIF OffsetTimeOriginal carried
    into the sidecar). A day with no log, or whose pictures don't state their
    offset, is skipped — counted, never an error and never a guess.

    This used to correlation-search the offset instead. That search cannot work
    on a short session: 202 Goeree pictures spanning 34 minutes against a 9.5-hour
    track made 22 different offsets score a perfect 202/202, so the answer was
    decided by scan order — it returned the bottom edge of a 9-hour tie (-06:14),
    and 24 pictures were geotagged 8.6 km from where they were taken. The camera
    had recorded the correct offset (+01:00) in every single file all along.

    A day whose pictures already all have GPS and altitude is counted as
    "days_complete" and left alone — see the check itself for why that has to
    come before everything else.

    Returns a summary dict for logging: `{"days_matched", "days_no_log",
    "days_no_offset", "days_complete", "gps_files", "altitude_files",
    "pictures", "logs_used": [filenames]}`."""
    from engine.geo import camera_offset_from_dates

    summary = {"days_matched": 0, "days_no_log": 0, "days_no_offset": 0,
              "days_complete": 0,
              "gps_files": 0, "altitude_files": 0, "pictures": 0, "logs_used": []}
    for day, day_entries in entries_by_day.items():
        # Nothing to add is not a failure. A day whose pictures all carry GPS
        # AND an altitude is already done — that is the normal state of a
        # RESTORED day, where the sidecars come back from the backup with the
        # geotagging the trip already did. Checked before the offset is even
        # needed, because otherwise such a day reported "log found but the
        # pictures don't record the camera's UTC offset — skipped", which reads
        # as lost GPS when in fact there was never any work to do.
        if all(e.get("has_gps") and e.get("cur_alt") is not None for e in day_entries):
            logger.info(f"Tracklog auto-geotag: {len(day_entries)} picture(s) on {day} — "
                       f"all of them already have GPS and altitude, nothing to add.")
            summary["days_complete"] += 1
            continue
        log_paths = find_day_tracklogs(day, root)
        if not log_paths:
            summary["days_no_log"] += 1
            continue
        names = ", ".join(Path(p).name for p in log_paths)
        try:
            points = load_track(log_paths)
        except ValueError as e:
            logger.warning(f"Tracklog auto-geotag: couldn't read {names}: {e}")
            summary["days_no_log"] += 1
            continue
        offset = camera_offset_from_dates([e.get("date") for e in day_entries])
        if offset is None:
            logger.info(f"Tracklog auto-geotag: {len(day_entries)} picture(s) on {day} — "
                       f"log found ({names}) but the pictures don't record "
                       f"the camera's UTC offset, so their times can't be placed on the "
                       f"track. Skipped — use Geotag from tracklog… to set it by hand.")
            summary["days_no_offset"] += 1
            continue
        rows, _stats = plan_entries(day_entries, points, camera_utc_offset=offset,
                                    max_gap_s=max_gap_s)
        if rows:
            result = apply_entries(files_by_key, rows)
            summary["gps_files"] += result["gps_files"]
            summary["altitude_files"] += result["altitude_files"]
            summary["pictures"] += result["pictures"]
        summary["days_matched"] += 1
        summary["logs_used"].extend(Path(p).name for p in log_paths)
    return summary


class MasterSource:
    """Adapter over the master-scoped plan()/apply(), for TracklogGeotagDialog."""

    confirm_note = "data_pictures.csv is backed up first."

    def __init__(self, db, master_root):
        self._db, self._master_root = db, master_root

    def plan(self, points, camera_utc_offset, max_gap_s):
        return plan(self._db, points, camera_utc_offset=camera_utc_offset, max_gap_s=max_gap_s)

    def apply(self, rows):
        return apply(self._db, self._master_root, rows)

    def sample_dates(self, points: list[dict]) -> list[str]:
        """Raw DateTimeOriginal strings for every master picture in the
        track's covered date range — just enough for
        engine.geo.camera_offset_from_dates to read the camera's own UTC
        offset BEFORE plan() has a confirmed offset to call with. Same
        date-range query plan() itself runs; cheap to repeat for this."""
        from_date, to_date = track_date_range(points)
        if not from_date:
            return []
        by_day = self._db.pictures_in_daterange(from_date, to_date)
        return [r.get("DateTimeOriginal") or "" for rows in by_day.values() for r in rows]


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

    def sample_dates(self, points: list[dict]) -> list[str]:
        """Raw DateTimeOriginal strings already held in memory — no I/O,
        unlike MasterSource's version. `points` is accepted only to keep the
        two adapters' signatures identical; unused here."""
        return [e.get("date") or "" for e in self._entries]
