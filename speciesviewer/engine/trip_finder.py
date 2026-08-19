"""Find the trips already hiding in the master — a read-only report.

Why it exists: creating a virtual trip means typing a `fromdate`/`todate` you
have to remember. Nobody remembers. The Tucson trip was found by hand-searching
filename prefixes, and the range that got typed (2023-03-23 → 2023-04-18) was
padded two days on each side of the pictures that actually exist
(2023-03-25 → 2023-04-16), because guessing wide feels safer than guessing wrong.

The master already knows. Every picture carries a DateTimeOriginal (100% of
73,957 of them) and most carry Country/State (85%), so a trip is simply a run of
consecutive shooting days, labelled by where you were. GPS is deliberately NOT
the signal here — only 56% of pictures have a coordinate, while the place-name
fields are far better populated and are what a human would name a trip by.

Two sources land in ONE list, which is what removes the "from an existing trip
or retroactive from master dates?" question the Create dialog would otherwise
have to ask — the answer is just which row you picked:

  * KNOWN   — a real trip from the master's `trip_admin.csv`, dates already
              recorded, nothing inferred.
  * FOUND   — a date cluster discovered in the master.

This module is report-only: it reads, it never writes. Feeding a picked row into
Create Virtual Trip is a separate, later step.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import os
import re
from pathlib import Path
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")

# A UPN is "<your name for the trip><sep><camera filename>":
#   2013 panama dsc_10646     2025 gambia-n2170007     bay area p1000923
# The separator is a SPACE on the old pictures and a HYPHEN on the newer ones,
# and hyphens also occur INSIDE names ("2024 zomer zuid-afrika-n6240016") and
# inside filenames ("2013 december nl dsc_1975-v2") — so splitting on the last
# space or the last hyphen is wrong both ways. Instead recognise the camera
# filename itself: a separator, 1-6 letters, an optional underscore, then a
# digit. Verified against the whole master: extracts a prefix for 99% of 73,957
# UPNs, and every early cluster the place fields could not name resolves
# (panama 99%, florida 100%, oostzee 99%).
_FILENAME_TAIL = re.compile(r"[\s\-][a-z]{1,6}_?\d[\w\-]*$", re.IGNORECASE)

# Below this share the cluster holds a mix of prefixes and none of them speaks
# for it, so we fall back to the place fields rather than name it wrongly.
PREFIX_MIN_SHARE = 0.60


def upn_prefix(upn: str) -> str:
    """'2013 panama dsc_10646' → '2013 panama'. '' when it doesn't match."""
    m = _FILENAME_TAIL.search(upn or "")
    return upn[:m.start()].strip(" -_") if m else ""

# A gap of more than this many days between shooting days ends a cluster. Two
# lets a trip survive a travel day (or a rained-off day) without splitting in
# half; three started merging separate long weekends at home.
MAX_GAP_DAYS = 2

# Below these a cluster is noise rather than a trip — an afternoon in the garden
# is not a candidate. Deliberately generous: the report ranks, the human decides.
MIN_DAYS = 3
MIN_PICTURES = 100

# Pictures per day separating "I was away photographing" from "I live here and
# shot the garden now and then". Measured on real data: Tucson 87/day, home
# 5.9/day. Used for RANKING only — a low-intensity cluster is pushed down the
# list, never dropped, because the close-to-home collections (Braakman,
# Helgoland) are exactly the ones an aggressive home-filter would hide.
AWAY_INTENSITY = 20.0


def _norm_date(raw: str) -> str:
    """EXIF writes '2023:03:25', ISO wants '2023-03-25'. Accepts either."""
    return (raw or "")[:10].replace(":", "-")


def _parse_iso(d: str):
    try:
        return dt.date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def _load_days(db) -> dict:
    """ONE pass over the pictures table → {iso_date: {"n": count, "places":
    {(country, state): k}, "cities": {city: k}}}.

    Deliberately one scan, not a query per candidate. The first version asked the
    DB for the place, the city and the count of every candidate separately — 65
    candidates × 3 queries, each a full scan because `substr(replace(date))` can
    use no index, which took 7.1 s. The whole table is 73,957 rows of four short
    fields; reading it once and aggregating in Python is ~0.3 s and does not get
    slower as more candidates appear.
    """
    con = db._connect()
    days: dict = {}
    for r in con.execute(
        'SELECT "DateTimeOriginal" AS d, "Country" AS c, "State" AS s, "City" AS t, '
        '"UniquePictureName" AS u '
        'FROM pictures WHERE "DateTimeOriginal" IS NOT NULL AND "DateTimeOriginal" != ""'
    ):
        d = _norm_date(r["d"])
        if not _parse_iso(d):
            continue
        day = days.get(d)
        if day is None:
            day = days[d] = {"n": 0, "places": {}, "cities": {}, "prefixes": {}}
        day["n"] += 1
        country = (r["c"] or "").strip()
        if country:
            key = (country, (r["s"] or "").strip())
            day["places"][key] = day["places"].get(key, 0) + 1
        city = (r["t"] or "").strip()
        if city:
            day["cities"][city] = day["cities"].get(city, 0) + 1
        pre = upn_prefix(r["u"] or "")
        if pre:
            day["prefixes"][pre] = day["prefixes"].get(pre, 0) + 1
    return days


def _shooting_days(days: dict) -> list[tuple[str, int]]:
    """[(iso_date, picture_count)] chronological, from the single-pass load."""
    return [(d, days[d]["n"]) for d in sorted(days)]


def _range_days(days: dict, start: str, end: str) -> list[dict]:
    return [days[d] for d in days if start <= d <= end]


def _cluster(days: list[tuple[str, int]]) -> list[list[tuple[str, int]]]:
    """Split the shooting days into runs separated by gaps > MAX_GAP_DAYS."""
    if not days:
        return []
    clusters, current = [], [days[0]]
    for d, n in days[1:]:
        prev, this = _parse_iso(current[-1][0]), _parse_iso(d)
        if prev and this and (this - prev).days <= MAX_GAP_DAYS:
            current.append((d, n))
        else:
            clusters.append(current)
            current = [(d, n)]
    clusters.append(current)
    return clusters


def _place_for(days: dict, start: str, end: str) -> tuple[str, str, str]:
    """(label, country, state) — the dominant place in a date range.

    Dominant, not unique: a trip crosses regions, and the label only has to be
    recognisable enough to say "that's the Arizona one".
    """
    totals: dict = {}
    for day in _range_days(days, start, end):
        for key, k in day["places"].items():
            totals[key] = totals.get(key, 0) + k
    if not totals:
        return "(no place recorded)", "", ""
    (country, state), _ = max(totals.items(), key=lambda kv: kv[1])
    label = f"{state}, {country}" if state and state != country else country
    return label or "(no place recorded)", country, state


def _top_city(days: dict, start: str, end: str) -> str:
    """The most-photographed city in the range — the best naming hint we have.

    "Arizona, United States" is how the report LABELS the Tucson trip; "Tucson"
    is what the human actually calls it. Used only as a suggested name.
    """
    totals: dict = {}
    for day in _range_days(days, start, end):
        for city, k in day["cities"].items():
            totals[city] = totals.get(city, 0) + k
    return max(totals.items(), key=lambda kv: kv[1])[0] if totals else ""


def _count_pictures(days: dict, start: str, end: str) -> int:
    return sum(day["n"] for day in _range_days(days, start, end))


def _top_prefix(days: dict, start: str, end: str) -> tuple[str, float]:
    """(your_name_for_it, share_of_pictures) from the UPNs in the range.

    This is the strongest label we have, because it is not inferred at all — it
    is what YOU called the trip when the pictures were filed. It is also the only
    thing that names the early years: pictures from 2013 and before carry no
    Country/State, so the place fields leave ten clusters as "(no place
    recorded)" while the UPNs say panama, florida, oostzee.
    """
    totals: dict = {}
    n = 0
    for day in _range_days(days, start, end):
        n += day["n"]
        for pre, k in day["prefixes"].items():
            totals[pre] = totals.get(pre, 0) + k
    if not totals or not n:
        return "", 0.0
    pre, k = max(totals.items(), key=lambda kv: kv[1])
    return pre, k / n


def _read_known_trips(master_root: str) -> list[dict]:
    """Real trips from the master's trip_admin.csv, with the dates they recorded.

    13 of 14 carry a range; the one that doesn't (a waarneming.nl import) is
    skipped rather than guessed at.
    """
    path = Path(master_root) / "admin" / "trip_admin.csv"
    if not path.is_file():
        return []
    out = []
    try:
        with io.open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter=";"):
                start = _norm_date((row.get("fromdate") or "").strip())
                end = _norm_date((row.get("todate") or "").strip())
                name = (row.get("trip_name") or "").strip()
                if not (name and _parse_iso(start) and _parse_iso(end)):
                    continue
                out.append({"name": name, "start": start, "end": end,
                            "title_nl": (row.get("title_nl") or "").strip(),
                            "title_en": (row.get("title_en") or "").strip()})
    except OSError as e:
        logger.warning(f"trip_finder: could not read {path}: {e}")
    return out


def _read_existing_virttrips(virttrip_root: str) -> list[dict]:
    """Ranges already turned into a virtual trip, so the report can say so.

    They are MARKED, never hidden: a hidden row makes the list lie, and
    re-creating one is a legitimate thing to want.
    """
    root = Path(virttrip_root) if virttrip_root else None
    if not root or not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        ini = child / "virttrip.ini"
        if not ini.is_file():
            continue
        try:
            import configparser
            cp = configparser.ConfigParser()
            read_ini(cp, ini)
            start = _norm_date(cp.get("trip_config", "fromdate", fallback=""))
            end = _norm_date(cp.get("trip_config", "todate", fallback=""))
            name = cp.get("trip_config", "trip_name", fallback=child.name).strip()
            if _parse_iso(start) and _parse_iso(end):
                out.append({"name": name, "start": start, "end": end})
        except Exception as e:                       # a malformed ini must not kill the report
            logger.info(f"trip_finder: skipping {ini}: {e}")
    return out


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start <= b_end and b_start <= a_end


def find_candidates(master_root: str, virttrip_root: str = "") -> list[dict]:
    """The report. Returns candidate dicts, best first.

    Each row: source ('known'|'found'), name, start, end, days, pictures,
    per_day, place, city, existing (name of the virtual trip already covering
    this range, or ""), and away (bool — used for ranking).
    """
    from db.master_db import MasterDB

    db = MasterDB(master_root)
    db.ensure_fresh()
    try:
        days = _load_days(db)                    # the ONE table scan
        existing = _read_existing_virttrips(virttrip_root)
        known = _read_known_trips(master_root)
        rows: list[dict] = []

        # 1. KNOWN trips — dates are recorded, nothing is inferred.
        for t in known:
            place, country, _state = _place_for(days, t["start"], t["end"])
            span = (_parse_iso(t["end"]) - _parse_iso(t["start"])).days + 1
            pics = _count_pictures(days, t["start"], t["end"])
            rows.append({
                "source": "known", "name": t["name"],
                "start": t["start"], "end": t["end"],
                "days": span, "pictures": pics,
                "per_day": pics / span if span else 0.0,
                "place": place, "country": country,
                "city": _top_city(days, t["start"], t["end"]),
                "prefix": _top_prefix(days, t["start"], t["end"])[0],
                "prefix_share": _top_prefix(days, t["start"], t["end"])[1],
                "title": t.get("title_nl") or t.get("title_en") or "",
            })

        # 2. FOUND clusters — the discovery half.
        known_spans = [(t["start"], t["end"]) for t in known]
        for cl in _cluster(_shooting_days(days)):
            start, end = cl[0][0], cl[-1][0]
            pics = sum(n for _, n in cl)
            if len(cl) < MIN_DAYS or pics < MIN_PICTURES:
                continue
            # A cluster that IS a known trip would be a duplicate row.
            if any(_overlaps(start, end, ks, ke) for ks, ke in known_spans):
                continue
            place, country, _state = _place_for(days, start, end)
            prefix, prefix_share = _top_prefix(days, start, end)
            span = (_parse_iso(end) - _parse_iso(start)).days + 1
            rows.append({
                "source": "found", "name": "",
                "start": start, "end": end,
                "days": span, "pictures": pics,
                "per_day": pics / span if span else 0.0,
                "place": place, "country": country,
                "city": _top_city(days, start, end),
                "prefix": prefix, "prefix_share": prefix_share,
                "title": "",
            })

        # Mark rows already covered by a virtual trip (marked, never dropped).
        for r in rows:
            hit = next((e["name"] for e in existing
                        if _overlaps(r["start"], r["end"], e["start"], e["end"])), "")
            r["existing"] = hit
            r["away"] = r["per_day"] >= AWAY_INTENSITY

        # Best first: real trips, then intense clusters, then recent.
        rows.sort(key=lambda r: (r["source"] != "known", not r["away"],
                                 -r["per_day"], r["start"]), reverse=False)
        return rows
    finally:
        db.close()


def suggested_name(row: dict) -> str:
    """A folder-safe name to offer for a FOUND row: '2013_panama', 'Tucson_2023'.

    Order of preference, strongest evidence first:
      1. the trip's real name, when the row IS a known trip;
      2. the dominant UPN prefix — what YOU called these pictures when you filed
         them. Not inferred at all, and the only thing that names the early years
         (2013 and before carry no Country/State);
      3. the most-photographed city, then the country, both geocoded.
    A prefix that speaks for less than PREFIX_MIN_SHARE of the cluster is ignored:
    a mixed cluster would otherwise take the name of whichever trip happened to
    contribute the most pictures.
    """
    if row.get("name"):
        return row["name"]
    year = (row.get("start") or "")[:4]
    prefix = (row.get("prefix") or "").strip()
    if prefix and row.get("prefix_share", 0.0) >= PREFIX_MIN_SHARE:
        # The prefix usually already carries the year ("2013 panama") — don't
        # append it twice.
        base, add_year = prefix, not prefix.startswith(year)
    else:
        base, add_year = (row.get("city") or row.get("country") or "trip"), True
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in base).strip("_")
    return f"{safe}_{year}" if (add_year and year) else safe
