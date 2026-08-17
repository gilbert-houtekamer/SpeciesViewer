"""Tiny geo helpers shared by the map view and the master DB.

PyQt-free on purpose (the DB layer imports parse_gps_dms, and the standalone
tools can too). The map's Web-Mercator projection lives in gui/map_view.py; this
module only turns stored coordinate strings into signed decimal degrees.
"""

from __future__ import annotations

import re

# Matches one "32 deg 0' 55.66" N" component (minutes/seconds optional).
_DMS = re.compile(
    r"""(\d+(?:\.\d+)?)\s*deg\s*
        (?:(\d+(?:\.\d+)?)\s*'?\s*)?      # minutes (optional)
        (?:(\d+(?:\.\d+)?)\s*"?\s*)?      # seconds (optional)
        ([NSEW])""",
    re.IGNORECASE | re.VERBOSE,
)


def _one(m: "re.Match") -> float:
    deg = float(m.group(1))
    minutes = float(m.group(2) or 0.0)
    seconds = float(m.group(3) or 0.0)
    val = deg + minutes / 60.0 + seconds / 3600.0
    if m.group(4).upper() in ("S", "W"):
        val = -val
    return val


def parse_gps_dms(s: str):
    """Parse a stored GPSPosition into signed decimal ``(lat, lon)`` or None.

    Handles the exiftool DMS form ("32 deg 0' 55.66\" N, 110 deg 38' 4.01\" W")
    and a plain decimal fallback ("32.0154, -110.634"). Returns None on anything
    unparseable or out of range."""
    s = (s or "").strip()
    if not s:
        return None
    matches = list(_DMS.finditer(s))
    if len(matches) >= 2:
        lat, lon = _one(matches[0]), _one(matches[1])
    else:
        nums = re.findall(r"[-+]?\d+(?:\.\d+)?", s)
        if len(nums) < 2:
            return None
        lat, lon = float(nums[0]), float(nums[1])
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return (lat, lon)
    return None


# ── Tracklog geotag (2a): GPX / OM-1 NMEA log parsing + altitude cleaning ────
#
# Both parsers return the same shape — [{"t_utc": datetime, "lat": float,
# "lon": float, "alt": float | None}, …] sorted by time — so the cleaning and
# correlation code downstream (engine/tracklog_geotag.py) never needs to know
# which device produced a point. See docs/plans/maps_plan.md "2a — altitude-
# source addendum" for the research behind the cleaning parameters.

import datetime as _dt


def nmea_coord_to_deg(value: str, hemisphere: str) -> float:
    """NMEA ddmm.mmmm (or dddmm.mmmm for longitude) -> signed decimal degrees.
    Distinct from parse_gps_dms: that one reads exiftool's DMS *string* output
    ("32 deg 0' 55.66\" N"), this one reads the raw NMEA sentence field, a
    different format entirely — don't conflate them."""
    value = (value or "").strip()
    if not value:
        raise ValueError("empty NMEA coordinate")
    dot = value.index(".")
    deg_digits = dot - 2
    deg = float(value[:deg_digits])
    minutes = float(value[deg_digits:])
    dec = deg + minutes / 60.0
    if hemisphere.upper() in ("S", "W"):
        dec = -dec
    return dec


# "@OM Digital Solutions/+0200/+0200" — the OM-1 app's log header line, giving
# the local UTC offset the rest of the file's timestamps were written in.
_OM1_OFFSET_RE = re.compile(r"@OM Digital Solutions/([+-]\d{2}:?\d{2})")
_LOG_DATE_RE = re.compile(r"(\d{4})(\d{2})(\d{2})")


def parse_utc_offset(s: str) -> "_dt.timezone | None":
    s = (s or "").strip()
    m = re.match(r"^([+-])(\d{2}):?(\d{2})$", s)
    if not m:
        return None
    sign, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
    delta = _dt.timedelta(hours=hh, minutes=mm)
    if sign == "-":
        delta = -delta
    return _dt.timezone(delta)


# "2026:08:14 10:34:01+01:00" / "…10:34:01.62+01:00" / "…Z" — a capture time
# that carries its own zone, which is what a sidecar written since 2026-08-17
# holds (see sv0ReadSDCard._write_sidecars).
_DATE_OFFSET_RE = re.compile(r"([+-]\d{2}:?\d{2}|Z)\s*$")


def camera_offset_from_dates(dates) -> "_dt.timezone | None":
    """The camera's OWN UTC offset, read from capture-time strings that carry
    one — no correlation, no guess, no track needed.

    This is what EXIF 2.31's OffsetTimeOriginal records, and it is the value the
    tracklog geotag needs to make a picture's time absolute. Measured on 2026
    Goeree: using it puts a picture's camera GPS within a 2 m median of the track
    position at the same instant, where correlation search had them 8.6 km apart.

    Returns None when no date carries an offset (a pre-EXIF-2.31 camera, or an
    older sidecar), or when the ones that do DISAGREE — two cameras set to
    different zones is not something one offset can describe, and a caller that
    can only apply one must be told rather than handed a majority vote.

    Note this is the offset the camera was SET to, which is right for converting
    its clock to UTC even when the zone is stale — changing a camera's time zone
    moves the displayed clock and the recorded offset together, so UTC survives.
    What it cannot detect is a hand-set or drifting clock."""
    found = set()
    for s in dates:
        m = _DATE_OFFSET_RE.search((s or "").strip())
        if not m:
            continue
        token = m.group(1)
        tz = _dt.timezone.utc if token == "Z" else parse_utc_offset(token)
        if tz is not None:
            found.add(tz.utcoffset(None))
    return _dt.timezone(found.pop()) if len(found) == 1 else None


def om1_log_utc_offset(path) -> "_dt.timezone | None":
    """The UTC offset recorded in an OM-1 `.log` file's header, or None when
    the header isn't present.

    This is the LOGGING DEVICE's own local zone. It is NOT the zone the file's
    timestamps are written in — NMEA times are always UTC (see
    parse_om1_nmea_log). Read it to report or cross-check which zone the phone
    thought it was in; never to interpret a timestamp."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for _ in range(20):
                line = fh.readline()
                if not line:
                    break
                m = _OM1_OFFSET_RE.search(line)
                if m:
                    return parse_utc_offset(m.group(1))
    except OSError:
        pass
    return None


def parse_om1_nmea_log(path) -> list[dict]:
    """Parse an OM-1 companion app's per-day NMEA log (`YYYYMMDD.log`,
    `$GPGGA` fix lines) into [{"t_utc", "lat", "lon", "alt"}, …].

    NMEA TIMES ARE UTC. `$GPGGA` field 1 and `$GPRMC` field 1 are both UTC by
    specification, and the header's `@OM Digital Solutions/+0200/+0200` records
    the PHONE's local zone, not the meaning of those timestamps. Until
    2026-08-17 this parser stamped each fix with the header offset and
    converted, moving the whole track EARLIER by that offset — two hours for a
    Dutch summer log. Nothing complained: the shifted track still looked like a
    normal day, and every geotag taken from it was silently two hours out.

    The UTC date comes from `$GPRMC` field 9 (ddmmyy). The filename is only a
    fallback because it is the phone's LOCAL day: a log running past UTC
    midnight would stamp its later fixes a day early. A time-of-day that jumps
    backwards is a UTC midnight rollover and advances the date.

    `$GPGGA` field 9 (altitude) is MSL-corrected already — see the addendum in
    maps_plan.md, no further datum conversion is applied. Lines with fix
    quality 0 (no fix) are skipped."""
    from pathlib import Path
    name = Path(path).stem
    m = _LOG_DATE_RE.search(name)
    file_day = (_dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if m else None)

    # $GPRMC normally FOLLOWS the first $GPGGA, so the log's own UTC date can't
    # be picked up inline for the very first fix — find it first.
    rmc_day = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.startswith("$GPRMC"):
                    continue
                f = line.strip().split(",")
                if len(f) > 9 and len(f[9]) == 6 and f[9].isdigit():
                    try:                       # ddmmyy
                        rmc_day = _dt.date(2000 + int(f[9][4:6]),
                                           int(f[9][2:4]), int(f[9][0:2]))
                    except ValueError:
                        rmc_day = None
                    if rmc_day:
                        break
    except OSError:
        pass

    day = rmc_day or file_day
    if day is None:
        raise ValueError(
            f"no $GPRMC date and no YYYYMMDD date in log filename: {name}")

    out: list[dict] = []
    prev_tod = None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("$GPGGA"):
                continue
            f = line.split(",")
            if len(f) < 10:
                continue
            try:
                if int(f[6] or 0) == 0:            # fix quality: 0 = no fix
                    continue
                hh, mm, ss = int(f[1][0:2]), int(f[1][2:4]), float(f[1][4:])
                lat = nmea_coord_to_deg(f[2], f[3])
                lon = nmea_coord_to_deg(f[4], f[5])
                alt = float(f[9]) if f[9] else None
            except (ValueError, IndexError):
                continue
            tod = hh * 3600 + mm * 60 + ss
            if prev_tod is not None and tod < prev_tod - 1.0:
                day += _dt.timedelta(days=1)       # crossed UTC midnight
            prev_tod = tod
            out.append({"t_utc": _dt.datetime.combine(
                            day, _dt.time(hh, mm, int(ss), int((ss % 1) * 1e6)),
                            tzinfo=_dt.timezone.utc),
                        "lat": lat, "lon": lon, "alt": alt})
    out.sort(key=lambda p: p["t_utc"])
    return out


def parse_gpx_track(path) -> list[dict]:
    """Parse a GPX file's `<trkpt>` points into [{"t_utc", "lat", "lon",
    "alt"}, …]. GPX `<time>` is UTC (`Z`-suffixed ISO-8601) by spec, so no
    offset handling is needed here — that only applies to the OM-1 log."""
    import xml.etree.ElementTree as ET
    tree = ET.parse(str(path))
    root = tree.getroot()
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag[: root.tag.index("}") + 1]
    out: list[dict] = []
    for trkpt in root.iter(f"{ns}trkpt"):
        lat_s, lon_s = trkpt.get("lat"), trkpt.get("lon")
        if lat_s is None or lon_s is None:
            continue
        time_el = trkpt.find(f"{ns}time")
        if time_el is None or not (time_el.text or "").strip():
            continue
        t = (time_el.text or "").strip().replace("Z", "+00:00")
        try:
            t_utc = _dt.datetime.fromisoformat(t)
        except ValueError:
            continue
        if t_utc.tzinfo is None:
            t_utc = t_utc.replace(tzinfo=_dt.timezone.utc)
        t_utc = t_utc.astimezone(_dt.timezone.utc)
        ele_el = trkpt.find(f"{ns}ele")
        alt = None
        if ele_el is not None and (ele_el.text or "").strip():
            try:
                alt = float(ele_el.text.strip())
            except ValueError:
                alt = None
        out.append({"t_utc": t_utc, "lat": float(lat_s), "lon": float(lon_s), "alt": alt})
    out.sort(key=lambda p: p["t_utc"])
    return out


def clean_altitude_series(points: list[dict], *, window_s: int = 300,
                          sd_mult: float = 2.0) -> list[dict]:
    """Flag altitude outliers and compute a smoothed value for each point.

    Validated on real concurrent OM-1/NomadTracks field data (2026-08-15):
    bad altitude readings are a SHARED satellite-geometry event, not
    independent per-device noise, so a local-window statistical filter
    correctly isolates a dropout instead of trusting a single bad sample.

    For each point, look at every point within `window_s` seconds (before
    and after); a point more than `sd_mult` local standard deviations from
    the local mean altitude is flagged `is_outlier`. `alt_clean` is then the
    mean altitude of the non-outlier points in that same window (falls back
    to the point's own raw altitude if every neighbour was flagged, or None
    if the point itself has no altitude reading).

    Returns new dicts (input `points` is not mutated), same order as input.
    Pure function — no I/O, unit-testable directly against a fixture log.

    Points are already time-sorted, so the window per point is found with a
    two-pointer sweep (O(n) total) rather than rescanning every other point
    for every point (O(n^2)) — the earlier version was fine on a single day's
    ~2,000-point log but wouldn't scale to a longer/denser track (a
    continuous multi-day export, or a high-rate phone logger). Mean/stdev are
    plain sum()-based rather than the stdlib `statistics` module, which is
    exact-rational-arithmetic slow enough to dominate the runtime once this
    runs per-point over a large track."""
    pts = sorted(points, key=lambda p: p["t_utc"])
    times = [p["t_utc"] for p in pts]
    alts = [p.get("alt") for p in pts]
    n = len(pts)
    window = _dt.timedelta(seconds=window_s)

    def _window_ranges():
        """[(lo, hi), …] — for each i, the index range [lo, hi) of points
        within `window` seconds either side, exploiting sorted times so lo
        and hi only ever advance (never rescan from 0)."""
        ranges = []
        lo = hi = 0
        for i in range(n):
            while lo < i and times[lo] < times[i] - window:
                lo += 1
            if hi < i:
                hi = i
            while hi < n and times[hi] <= times[i] + window:
                hi += 1
            ranges.append((lo, hi))
        return ranges

    ranges = _window_ranges()

    def _mean_sd(vals: list[float]):
        cnt = len(vals)
        mean = sum(vals) / cnt
        var = sum((v - mean) ** 2 for v in vals) / (cnt - 1)
        return mean, var ** 0.5

    is_outlier = [False] * n
    for i in range(n):
        if alts[i] is None:
            continue
        lo, hi = ranges[i]
        nb_alts = [alts[j] for j in range(lo, hi) if alts[j] is not None]
        if len(nb_alts) < 2:
            continue
        mean, sd = _mean_sd(nb_alts)
        if sd > 0 and abs(alts[i] - mean) > sd_mult * sd:
            is_outlier[i] = True

    out: list[dict] = []
    for i in range(n):
        clean = None
        if alts[i] is not None:
            lo, hi = ranges[i]
            clean_alts = [alts[j] for j in range(lo, hi)
                         if alts[j] is not None and not is_outlier[j]]
            clean = sum(clean_alts) / len(clean_alts) if clean_alts else alts[i]
        d = dict(pts[i])
        d["is_outlier"] = is_outlier[i]
        d["alt_clean"] = clean
        out.append(d)
    return out


def nearest_track_point(points: list[dict], t_utc, max_gap_s: float):
    """The cleaned track point nearest `t_utc` (a timezone-aware UTC
    datetime), or None if nothing is within `max_gap_s` seconds — the
    "silent-optional" cutoff: a photo with no nearby track coverage gets
    skipped, never a stale/garbage match. `points` should already be run
    through clean_altitude_series (which sorts by time — required here: a
    binary search, since this runs once per candidate photo and a full
    linear rescan of a large track for every photo doesn't scale)."""
    if not points:
        return None
    import bisect
    times = [p["t_utc"] for p in points]
    idx = bisect.bisect_left(times, t_utc)
    candidates = [i for i in (idx - 1, idx) if 0 <= i < len(points)]
    best_i = min(candidates, key=lambda i: abs((times[i] - t_utc).total_seconds()))
    best_gap = abs((times[best_i] - t_utc).total_seconds())
    if best_gap > max_gap_s:
        return None
    out = dict(points[best_i])
    out["gap_s"] = best_gap
    return out

