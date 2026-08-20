"""How old is the SmallMaster copy on this machine?

One job: say when a SmallMaster snapshot was taken, and how old that makes it.

Away from home, whether a species counts as a lifer and which names the species
box suggests are both answered from the master snapshot carried on the laptop.
A stale snapshot answers them confidently and wrongly, and there is no symptom
to notice — the verdict simply differs from the one the full master would give.
So the snapshot has to say its own age out loud.

The stamp is written INTO the ZIP by the export (batch/svu9SmallMaster.py) and
lands in the master root when the ZIP is installed, which means no separate step
can be forgotten: a copy either carries its date or was made before this
existed. For that older case there is a fallback — the metadata CSVs' own file
dates — reported as the estimate it is, never dressed up as a stamp.

Nothing here writes to the master; `stamp_json` only builds the text for the
export to put in its ZIP.
"""

import json
import time
from pathlib import Path

# At the ZIP's root, so extracting to the master directory puts it at the master
# root — no install-side code needed to place it.
STAMP_NAME = "smallmaster.json"

# A snapshot older than this is worth a warning; older still is worth an alarm.
# Chosen against the trip case: a fortnight away on a copy taken the week before
# leaving is fine, the same copy two months later is not.
WARN_DAYS = 30
ALERT_DAYS = 90

_META_REL = ("metadata", "active")


def stamp_json(*, source_root="", machine="", biota_files=0, metadata_files=0) -> str:
    """The stamp text an export writes into its ZIP. Written LAST, from what
    actually went in — the same rule the day-backup manifest follows, for the
    same reason: a stamp made up front can describe a ZIP that never happened."""
    import platform
    return json.dumps({
        "format": 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": machine or platform.node(),
        "source_root": str(source_root),
        "biota_files": int(biota_files),
        "metadata_files": int(metadata_files),
    }, indent=2)


def _parse(text) -> dict | None:
    try:
        info = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(info, dict) or not info.get("created"):
        return None
    info["estimated"] = False
    return info


def read_stamp(master_root) -> dict | None:
    """The stamp sitting in an installed master root, or None."""
    if not master_root:
        return None
    try:
        return _parse(Path(master_root, STAMP_NAME).read_text(encoding="utf-8"))
    except OSError:
        return None


def read_zip_stamp(zip_path) -> dict | None:
    """The stamp inside a smallmaster.zip, or None for a ZIP made before stamps
    existed. Falls back to the ZIP file's own date, marked as an estimate."""
    import zipfile
    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                return _parse(zf.read(STAMP_NAME).decode("utf-8"))
            except KeyError:
                pass
    except (OSError, zipfile.BadZipFile):
        return None
    return _from_mtime(Path(zip_path), "the ZIP's own file date")


def _from_mtime(path: Path, why: str) -> dict | None:
    try:
        when = path.stat().st_mtime
    except OSError:
        return None
    return {"format": 0, "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(when)),
            "machine": "", "estimated": True, "estimated_from": why}


def estimate_from_files(master_root) -> dict | None:
    """No stamp — date the copy by its newest metadata CSV instead.

    For a laptop that was installed before stamps existed. It is an estimate and
    says so: the CSVs are written when the ZIP is built, so the date is right
    unless something has touched them since.
    """
    if not master_root:
        return None
    meta = Path(master_root, *_META_REL)
    try:
        newest = max((p for p in meta.glob("*.csv")), key=lambda p: p.stat().st_mtime)
    except (OSError, ValueError):
        return None
    return _from_mtime(newest, "the date on the metadata files")


def age_days(info) -> float | None:
    if not info or not info.get("created"):
        return None
    try:
        made = time.mktime(time.strptime(str(info["created"])[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None
    return max(0.0, (time.time() - made) / 86400.0)


def level(info) -> str:
    """"ok" / "warn" / "alert" — what the age is worth saying it in."""
    days = age_days(info)
    if days is None:
        return "ok"
    if days >= ALERT_DAYS:
        return "alert"
    if days >= WARN_DAYS:
        return "warn"
    return "ok"


def describe_age(info) -> str:
    """"today" / "yesterday" / "9 days old" / "7 weeks old"."""
    days = age_days(info)
    if days is None:
        return "date unknown"
    whole = int(days)
    if whole == 0:
        return "taken today"
    if whole == 1:
        return "taken yesterday"
    if whole < 21:
        return f"{whole} days old"
    if whole < 120:
        return f"{whole // 7} weeks old"
    return f"{whole // 30} months old"


def describe(info, *, short=False) -> str:
    """One line for a person: "9 days old — 11 Aug 2026, from BIGBOY"."""
    if not info:
        return "no snapshot date"
    try:
        when = time.strftime("%d %b %Y", time.strptime(str(info["created"])[:19],
                                                       "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, KeyError):
        when = "date unknown"
    if short:
        return f"{describe_age(info)} ({when})"
    parts = [f"{describe_age(info)} — {when}"]
    if info.get("machine"):
        parts.append(f"from {info['machine']}")
    line = ", ".join(parts)
    if info.get("estimated"):
        line += f" (estimated from {info.get('estimated_from', 'file dates')})"
    return line
