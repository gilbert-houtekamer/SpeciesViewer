"""Check Range — what does the master ALREADY hold for a date range?

Answers the question you have the moment you type a from/to date into Create
Trip or Create Album: *is anything of this period in the master already, and is
some other trip or album already covering it?*

Two facts, one report:

  DAYS      one row per calendar day in the range: how many pictures the master
            holds, how many species, and WHICH CAMERAS took them. The camera is
            the column that earns its place — "there are iPhone pictures in the
            master for that week but no Nikon/Olympus" is the real question, and
            a picture count alone cannot answer it. An empty day is listed too,
            because the empty days are the actionable ones.

  OVERLAPS  the trips and albums whose own date range intersects this one, from
            three sources: trip folders on disk, album folders on disk, and the
            master's trip_admin.csv (which also covers trips whose folder does
            not live on this machine).

REPORT ONLY — this module reads, it never writes. What to do about what it says
(narrow the range, drop the idea, carry on regardless) is the user's call; the
same stance as `trip_finder`.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

logger = logging.getLogger("speciesviewer")

# Above this many days the per-day table stops listing empty days. A month-long
# range wants its gaps shown (they are where the unsorted pictures go); a
# five-year album range would drown the interesting rows in ~1800 blanks.
LIST_EMPTY_DAYS_UPTO = 400


def _parse_iso(d):
    try:
        return dt.date.fromisoformat(str(d)[:10].replace(":", "-"))
    except (ValueError, TypeError):
        return None


def _norm(d) -> str:
    return str(d or "")[:10].replace(":", "-")


def _overlap_days(a_start: str, a_end: str, b_start: str, b_end: str) -> int:
    """Days the two closed ranges share. 0 when they do not touch."""
    lo, hi = max(a_start, b_start), min(a_end, b_end)
    d_lo, d_hi = _parse_iso(lo), _parse_iso(hi)
    if not (d_lo and d_hi) or d_lo > d_hi:
        return 0
    return (d_hi - d_lo).days + 1


def _place_of(pics) -> str:
    """The most-seen City / State, Country of a day. The place fields are far
    better populated than GPS (85% vs 56%), and they are what a human recognises
    a day by."""
    counts = {}
    for p in pics:
        key = ((p.get("City") or "").strip(),
               (p.get("State") or "").strip(),
               (p.get("Country") or "").strip())
        if any(key):
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    city, state, country = max(counts.items(), key=lambda kv: kv[1])[0]
    return ", ".join(x for x in (city or state, country) if x)


def _cameras_of(pics):
    """(camera models most-used first, count with no camera at all).

    The discriminator this whole report exists for: a picture count alone cannot
    tell "that week is covered" from "that week is phone snaps only". The
    no-camera count matters just as much — a master row with no CameraModel is
    almost always a waarneming.nl determination record (an observation with a
    name attached), NOT a photograph of yours. A range that looks well populated
    can be entirely records, which is the opposite of covered.
    """
    counts, no_camera = {}, 0
    for p in pics:
        model = (p.get("CameraModel") or "").strip()
        if model:
            counts[model] = counts.get(model, 0) + 1
        else:
            no_camera += 1
    return [m for m, _ in sorted(counts.items(), key=lambda kv: -kv[1])], no_camera


def _read_disk_ranges(root: str, kind: str):
    """Trip or album folders on disk that record a date range of their own.

    A malformed or range-less ini is SKIPPED rather than guessed at — an overlap
    report that invents a range is worse than one that is short.
    """
    base = Path(root) if root else None
    if not base or not base.is_dir():
        return []
    if kind == "album":
        from utils.virttrip_config import read_virttrip_ini as read_ini_of
    else:
        from utils.trip_config import read_trip_ini as read_ini_of

    out = []
    try:
        children = sorted(base.iterdir())
    except OSError as e:
        logger.info(f"range_check: cannot list {base}: {e}")
        return []
    for child in children:
        if not child.is_dir():
            continue
        try:
            info = read_ini_of(str(child)) or {}
        except Exception as e:              # one bad ini must not kill the report
            logger.info(f"range_check: skipping {child}: {e}")
            continue
        start, end = _norm(info.get("fromdate")), _norm(info.get("todate"))
        if not (_parse_iso(start) and _parse_iso(end)):
            continue
        out.append({"kind": kind,
                    "name": (info.get("trip_name") or child.name).strip(),
                    "folder": child.name, "start": start, "end": end,
                    "title": (info.get("title_nl") or info.get("title_en") or "").strip(),
                    "on_disk": True, "listed": False})
    return out


def find_overlaps(from_date: str, to_date: str, *, master_root: str = "",
                  trips_root: str = "", virttrip_root: str = "", exclude: str = ""):
    """Trips and albums whose range intersects [from_date, to_date].

    Merged from three sources so neither half of the truth is missing: a trip
    can be on disk but never published, or published from another machine and
    absent here. A folder found in both is ONE row, marked as both.
    """
    lo, hi = _norm(from_date), _norm(to_date)
    rows = _read_disk_ranges(trips_root, "trip") + _read_disk_ranges(virttrip_root, "album")

    # The master's own list of advertised trips/albums — read with trip_finder's
    # reader rather than a second parser for the same file.
    if master_root:
        try:
            from engine.trip_finder import _read_known_trips
            for t in _read_known_trips(master_root):
                rows.append({"kind": "listed", "name": t["name"], "folder": "",
                             "start": t["start"], "end": t["end"],
                             "title": t.get("title_nl") or t.get("title_en") or "",
                             "on_disk": False, "listed": True})
        except Exception as e:
            logger.info(f"range_check: could not read the master trip list: {e}")

    merged = {}
    for r in rows:
        # The trip or album being checked is not an overlap with itself.
        if exclude and r["name"].lower() == exclude.lower():
            continue
        days = _overlap_days(lo, hi, r["start"], r["end"])
        if not days:
            continue
        key = (r["name"].lower(), r["start"], r["end"])
        seen = merged.get(key)
        if seen is None:
            merged[key] = dict(r, overlap_days=days)
            continue
        # Same thing from two sources: keep the on-disk row (it knows whether it
        # is a trip or an album) and remember that it is advertised as well.
        seen["listed"] = seen["listed"] or r["listed"]
        seen["on_disk"] = seen["on_disk"] or r["on_disk"]
        if r["on_disk"] and seen["kind"] == "listed":
            seen["kind"], seen["folder"] = r["kind"], r["folder"]

    return sorted(merged.values(), key=lambda r: (r["start"], r["name"]))


def check_range(master_root: str, from_date: str, to_date: str, *,
                trips_root: str = "", virttrip_root: str = "", exclude: str = "") -> dict:
    """The whole report. Read-only.

    Returns {"from", "to", "days", "totals", "overlaps", "empty_days_listed"};
    a `days` row is {date, pictures, jpgs, species, cameras, place}.
    """
    from db.master_db import MasterDB

    lo, hi = _norm(from_date), _norm(to_date)
    d_lo, d_hi = _parse_iso(lo), _parse_iso(hi)
    if not (d_lo and d_hi):
        raise ValueError(f"Not a date range: {from_date} - {to_date}")
    if d_lo > d_hi:                      # a back-to-front range is a typo, not an error
        d_lo, d_hi, lo, hi = d_hi, d_lo, hi, lo

    db = MasterDB(master_root)
    db.ensure_fresh()
    by_day = db.pictures_in_daterange(lo, hi)

    span = (d_hi - d_lo).days + 1
    list_empty = span <= LIST_EMPTY_DAYS_UPTO
    dates = ([(d_lo + dt.timedelta(days=i)).isoformat() for i in range(span)]
             if list_empty else sorted(by_day))

    days, all_species = [], set()
    for date in dates:
        pics = by_day.get(date, [])
        species = {(p.get("WetSoortNaam") or "").strip().lower()
                   for p in pics if (p.get("WetSoortNaam") or "").strip()}
        all_species |= species
        cameras, no_camera = _cameras_of(pics)
        days.append({
            "date": date,
            "pictures": len(pics),
            "jpgs": sum(1 for p in pics if (p.get("BestJPG") or "").strip()),
            "species": len(species),
            "cameras": cameras,
            "no_camera": no_camera,
            "place": _place_of(pics),
        })

    filled = sum(1 for d in days if d["pictures"])
    report = {
        "from": lo, "to": hi, "days": days,
        "empty_days_listed": list_empty,
        "totals": {
            "pictures": sum(d["pictures"] for d in days),
            "jpgs": sum(d["jpgs"] for d in days),
            "no_camera": sum(d["no_camera"] for d in days),
            "species": len(all_species),
            "span_days": span,
            "days_with_pictures": filled,
            "days_empty": span - filled,
        },
        "overlaps": find_overlaps(lo, hi, master_root=master_root,
                                  trips_root=trips_root, virttrip_root=virttrip_root,
                                  exclude=exclude),
    }
    logger.info(f"Check Range {lo} - {hi}: {report['totals']['pictures']} pictures on "
                f"{filled} of {span} days, {len(report['overlaps'])} overlapping "
                f"trip(s)/album(s).")
    return report
