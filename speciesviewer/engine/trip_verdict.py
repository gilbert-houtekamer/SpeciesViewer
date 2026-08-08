"""Keep the trip's species verdict after the trip itself is gone.

ONE JOB: copy the Count Species result out of the trip and into the master,
before the merge, so the answer to "how many lifers did that trip give me?"
survives everything that happens afterwards.

WHY THE MASTER: Retire Trip is `shutil.rmtree(trip_db)` — the whole trip repo,
including metadata/reports/ and metadata/initialize/. And Reset Trip clears
metadata/review/, which is where Count Species reads the trip's species from, so
after a reset the report cannot even be regenerated. A verdict that lives only
in the trip is a verdict you will lose.

WHEN: step 1 of Merge Pictures into Master, before anything touches the master.
At that moment the master is still pre-trip, so the verdict is right even for a
trip that has no frozen baseline (see engine/species_baseline.py). Ad-hoc runs
of Count Species deliberately do NOT archive — only the merge writes the
official verdict, so a casual re-run after the merge can never overwrite it.

WHAT IT WRITES, all under <master>/admin/:

    trip_reports/<trip_id> species overview.txt   the full report
    trip_reports/<trip_id> baseline.csv           what it was judged against
    trip_species_summary.csv                      one row per trip

Only the last version is kept: the report and baseline are overwritten and the
summary row is replaced on trip_id. The summary CSV is the part that pays off
later — lifers per trip becomes a query instead of scraping text reports.
"""

import csv
import logging
import os
import re
import shutil

from engine import species_baseline

logger = logging.getLogger("speciesviewer")

REPORT_NAME = "Species overview.txt"
ARCHIVE_DIRNAME = "trip_reports"
SUMMARY_NAME = "trip_species_summary.csv"

SUMMARY_FIELDS = ["trip_id", "trip_name", "fromdate", "todate",
                  "species_count", "new_count", "baseline", "baseline_date",
                  "days_summarized", "days_total", "generated_at"]

_TOTALS_RE = re.compile(r"^Totals:\s*(\d+)\s+species,\s*(\d+)\s+new", re.M)
_DAYS_RE = re.compile(r"^Days:\s*(\d+)\s+of\s+(\d+)\s+summarized", re.M)
_GENERATED_RE = re.compile(r"^Generated\s+(.+)$", re.M)
_BASELINE_RE = re.compile(r"^Lifer baseline:\s*(.+)$", re.M)
# The three ways a baseline can be arrived at, in the wording svr2CountSpecies
# prints. The time part is optional: a reconstructed baseline is stamped with
# whatever the trip recorded, which may be a bare date.
_DATED_RE = re.compile(r"the master has a picture of from before (\d{4}-\d{2}-\d{2})")
_BASELINE_DATE_RE = re.compile(
    r"master as of ([0-9-]+(?: [0-9:]+)?), (frozen|reconstructed)")


def report_path(trip_repo):
    return os.path.join(trip_repo, "metadata", "reports", REPORT_NAME)


def archive_dir(master_repo):
    return os.path.join(master_repo, "admin", ARCHIVE_DIRNAME)


def summary_path(master_repo):
    return os.path.join(master_repo, "admin", SUMMARY_NAME)


def parse_report(path):
    """Read back what Count Species wrote. Returns a dict of the numbers it
    stated about itself — never re-derived from the fixed-width columns, which
    shift as soon as a species name overruns its field."""
    with open(path, encoding="utf-8") as f:
        text = f.read()

    totals = _TOTALS_RE.search(text)
    days = _DAYS_RE.search(text)
    generated = _GENERATED_RE.search(text)
    note = _BASELINE_RE.search(text)
    note_text = note.group(1).strip() if note else ""
    dated = _DATED_RE.search(note_text)
    frozen_at = _BASELINE_DATE_RE.search(note_text)
    if dated:
        baseline, baseline_date = "before-trip-start", dated.group(1)
    elif frozen_at:
        baseline, baseline_date = frozen_at.group(2), frozen_at.group(1)
    else:
        baseline, baseline_date = "live master", ""

    return {
        "species_count": int(totals.group(1)) if totals else None,
        "new_count": int(totals.group(2)) if totals else None,
        "days_summarized": int(days.group(1)) if days else None,
        "days_total": int(days.group(2)) if days else None,
        "generated_at": generated.group(1).strip() if generated else "",
        # "before-trip-start" (the date rule — the count that stays true),
        # "frozen" / "reconstructed" (fallbacks for a trip with no start date),
        # or "live master" (no baseline at all — true only until this trip is
        # merged).
        "baseline": baseline,
        "baseline_date": baseline_date,
        "note": note_text,
    }


def _read_summary(path):
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f, delimiter=";"))
    except OSError as e:
        logger.warning(f"Trip verdict: could not read {path}: {e}")
        return []


def _write_summary(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS, delimiter=";",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def archive(trip_repo, master_repo, trip_id, trip_name="",
            fromdate="", todate=""):
    """Copy this trip's verdict into the master. Returns the parsed summary.

    Raises FileNotFoundError when there is no report to archive — the caller
    runs Count Species first, so a missing report means that run did not
    happen and silently archiving nothing would be the worst outcome.
    """
    src = report_path(trip_repo)
    if not os.path.isfile(src):
        raise FileNotFoundError(
            f"No species report to archive at {src}. Run Count Species first.")
    if not trip_id:
        raise ValueError("Trip verdict: refusing to archive without a trip_id.")

    parsed = parse_report(src)
    if parsed["species_count"] is None:
        # An old report predating the Totals line, or a truncated write.
        raise ValueError(
            f"{src} has no 'Totals:' line — re-run Count Species to produce a "
            f"report this version can archive.")

    target_dir = archive_dir(master_repo)
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy(src, os.path.join(target_dir, f"{trip_id} species overview.txt"))

    # Archive the baseline file ONLY when the report actually used it. Under the
    # date rule the comparison set is derived from the master's own picture
    # dates and can be recomputed at any time from `baseline_date`, so copying
    # the trip's unused fallback file next to the report would suggest it was
    # what produced the number.
    baseline_src = species_baseline.baseline_path(trip_repo)
    baseline_dst = os.path.join(target_dir, f"{trip_id} baseline.csv")
    if parsed["baseline"] in ("frozen", "reconstructed") and os.path.isfile(baseline_src):
        shutil.copy(baseline_src, baseline_dst)
    elif os.path.isfile(baseline_dst):
        # A previous merge archived one under a rule we no longer used; leaving
        # it behind would misrepresent this verdict.
        os.remove(baseline_dst)

    row = {
        "trip_id": trip_id,
        "trip_name": trip_name,
        "fromdate": fromdate,
        "todate": todate,
        "species_count": parsed["species_count"],
        "new_count": parsed["new_count"],
        "baseline": parsed["baseline"],
        "baseline_date": parsed["baseline_date"],
        "days_summarized": parsed["days_summarized"],
        "days_total": parsed["days_total"],
        "generated_at": parsed["generated_at"],
    }
    rows = [r for r in _read_summary(summary_path(master_repo))
            if (r.get("trip_id") or "").strip() != trip_id]
    rows.append(row)
    rows.sort(key=lambda r: ((r.get("fromdate") or ""), (r.get("trip_id") or "")))
    _write_summary(summary_path(master_repo), rows)

    logger.info(f"Trip verdict archived for {trip_id}: {row['species_count']} species, "
                f"{row['new_count']} new ({row['baseline']} baseline).")
    return row
