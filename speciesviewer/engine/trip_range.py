"""Does a trip's date range hold every day it has?

ONE JOB: compare a trip's stored dates (trip.ini fromdate / todate) with the
days it has actually read (metadata/review/<YYYY-MM-DD> with a data_files.csv),
and propose the range that would hold them.

INVARIANT: a stored range is only ever WIDENED, never narrowed. A range wider
than the days is a plan (days still to come, or a trip cut short) and is fine.
The one exception is a range that holds none of the days at all — a default the
trip was created with — where the days' own range is offered as well.

No GUI and no writes, except set_trip_dates, which changes those two lines of
trip.ini and nothing else.
"""

import os
import re

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def trip_days(trip_db) -> list:
    """The days this trip has read (Read & Summarize ran), sorted."""
    review = os.path.join(str(trip_db), "metadata", "review")
    try:
        names = os.listdir(review)
    except OSError:
        return []
    return sorted(d for d in names if _DAY.match(d)
                  and os.path.isfile(os.path.join(review, d, "data_files.csv")))


def check_range(fromdate: str, todate: str, days: list) -> dict | None:
    """None when every day lies inside fromdate..todate (or there are no days).
    Otherwise {"outside": [days outside], "widened": (from, to) holding the
    stored range AND the days, "days_only": (first, last) when NONE of the days
    is inside the stored range, else None}."""
    days = sorted(d for d in days if _DAY.match(d))
    if not days:
        return None
    f, t = (fromdate or "").strip(), (todate or "").strip()
    if not (f and t):
        return {"outside": days, "widened": (days[0], days[-1]),
                "days_only": None}
    outside = [d for d in days if d < f or d > t]
    if not outside:
        return None
    return {"outside": outside,
            "widened": (min(f, days[0]), max(t, days[-1])),
            "days_only": (days[0], days[-1]) if len(outside) == len(days) else None}


def set_trip_dates(trip_db, fromdate: str, todate: str) -> bool:
    """Write fromdate / todate into trip.ini, keeping every other line."""
    import logging
    from utils.trip_config import set_trip_flag
    try:
        return (set_trip_flag(str(trip_db), "fromdate", fromdate)
                and set_trip_flag(str(trip_db), "todate", todate))
    except (OSError, UnicodeDecodeError) as e:
        logging.getLogger("speciesviewer").error(f"Could not save the trip's dates: {e}")
        return False
