# trip_retire_scan.py: pre-flight safety scan for retiring (deleting) a trip's
# data structure once it lives on in the master + a virtual trip.
#
# Deletes ONLY the trip repo (<tripsroot>/<name>db). Never CurrentPictures, the
# master, or the virttrip. A trip db's biota/ holds COPIES of pictures that also
# live in CurrentPictures + the master, so removing it reclaims duplicates — but
# the day blogs and trip intro are narrative that must survive in the virttrip.
#
# Reporting only (review over auto-fix). Three blockers describe what this machine
# would lose; the rest are warnings. The GUI enables the delete when they pass, and
# otherwise lets the user override the failed blockers BY NAME — a hard refusal is
# what pushes people to delete in Explorer, which leaves a husk folder behind that
# still shows up in the Trips panel (see `gutted` in the report).
#   B1 virttrip exists   B2 virttrip has day blogs   B3 not the active trip
#   W1 virttrip intro    W2 trip blogs migrated      W3 virttrip biota non-empty
#   W4 species verdict archived in the master
# (an earlier note here also listed a "CurrentPictures still present" warning;
#  it was never implemented.)

import os
import glob
import shutil
import logging
import configparser

from utils.trip_identity import resolve, find_linked_virttrip

logger = logging.getLogger("speciesviewer")


def _same_path(a, b):
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _dir_size(root):
    """(total bytes, file count) in one walk — a leftover husk and a full trip
    repo look identical by name, so the report has to state which one it is."""
    total = 0
    count = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            count += 1
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total, count


def _blog_names(repo):
    """Set of blog_*.ini filenames under <repo>/metadata/blog."""
    bdir = os.path.join(repo, "metadata", "blog")
    if not os.path.isdir(bdir):
        return set()
    return {os.path.basename(p) for p in glob.glob(os.path.join(bdir, "blog_*.ini"))}


def _ini_has_intro(ini_path):
    if not os.path.isfile(ini_path):
        return False
    cfg = configparser.ConfigParser()
    try:
        cfg.read(ini_path, encoding="utf-8")
    except Exception:
        return False
    for sect in cfg.sections():
        for key in ("message_nl", "message_en", "title_nl", "title_en"):
            if (cfg.get(sect, key, fallback="") or "").strip():
                return True
    return False


def _count_pictures(trip_db):
    path = os.path.join(trip_db, "metadata", "active", "data_pictures.csv")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            return max(0, sum(1 for _ in fh) - 1)
    except Exception:
        return 0


def _verdict_warning(master_db, trip_id):
    """W4 — is this trip's species verdict archived in the master?"""
    if not master_db:
        return {"id": "W4", "label": "Species verdict archived in the master",
                "passed": True, "detail": "not checked (no master configured)"}
    import engine.trip_verdict
    path = os.path.join(engine.trip_verdict.archive_dir(master_db),
                        f"{trip_id} species overview.txt")
    if os.path.isfile(path):
        parsed = {}
        try:
            parsed = engine.trip_verdict.parse_report(path)
        except Exception:
            pass
        new_count = parsed.get("new_count")
        return {"id": "W4", "label": "Species verdict archived in the master",
                "passed": True,
                "detail": (f"{new_count} new of {parsed.get('species_count')} species"
                           if new_count is not None else "archived")}
    return {"id": "W4", "label": "Species verdict archived in the master",
            "passed": False,
            "detail": "no archived species count — run Merge Pictures into Master "
                      "(step 1) first, or the trip's new-species count is lost for good"}


def scan(trip_db, virttrips_root, active_trip_db="", master_db=""):
    """Assess whether a trip db is safe to retire. Returns a report dict (no
    writes): {ok, name, trip_db, virttrip_db, size_bytes, n_pictures,
    blockers:[...], warnings:[...], can_retire, has_warnings}.

    `master_db` enables W4 — whether this trip's species verdict was archived in
    the master. Omitted, W4 cannot be judged and passes."""
    trip_db = os.path.normpath(trip_db)
    if not os.path.isdir(trip_db):
        return {"ok": False, "error": f"trip folder not found: {trip_db}"}

    # Match the virttrip via the explicit trip→virttrip link (source_trip_id),
    # not the folder basename — so a trip whose id != web slug (the ebro case)
    # still resolves. Fall back to the basename convention for legacy virttrips
    # written before WP4 (no source_trip_id).
    ident = resolve(trip_db, kind="trip")
    name = ident.trip_id or os.path.basename(trip_db)      # e.g. costa-ricadb
    linked = find_linked_virttrip(ident.trip_id, virttrips_root)
    if linked:
        virttrip_db = linked
        match_method = "linked"
    else:
        virttrip_db = os.path.join(virttrips_root, name) if virttrips_root else ""
        match_method = "name"
    vt_exists = bool(virttrip_db) and os.path.isfile(os.path.join(virttrip_db, "virttrip.ini"))
    vt_blogs = _blog_names(virttrip_db) if vt_exists else set()
    trip_blogs = _blog_names(trip_db)
    unmigrated = sorted(trip_blogs - vt_blogs)
    # A virttrip shares the master's files, so its top-level biota/ is empty by
    # design — the proof it was actually built is the frozen website output.
    vt_frozen = os.path.join(virttrip_db, "metadata", "review", "frozen", "biota") if vt_exists else ""
    vt_built_files = 0
    if vt_frozen and os.path.isdir(vt_frozen):
        for _d, _s, files in os.walk(vt_frozen):
            vt_built_files += len(files)

    blockers = [
        {"id": "B1", "label": "Virtual trip exists",
         "passed": vt_exists,
         "detail": (f"{virttrip_db} ({'linked' if match_method == 'linked' else 'matched by name'})"
                    if vt_exists else f"no virttrip at {virttrip_db or '(no virttrips root)'}")},
        {"id": "B2", "label": "Virtual trip has day blogs",
         "passed": len(vt_blogs) > 0,
         "detail": f"{len(vt_blogs)} blog(s)" if vt_blogs else "no blogs in the virtual trip"},
        {"id": "B3", "label": "Not the active trip",
         "passed": not _same_path(trip_db, active_trip_db),
         "detail": "active trip — switch to another trip or the master first"
                   if _same_path(trip_db, active_trip_db) else "ok"},
    ]
    warnings = [
        {"id": "W1", "label": "Virtual trip has intro text",
         "passed": vt_exists and _ini_has_intro(os.path.join(virttrip_db, "virttrip.ini")),
         "detail": "virttrip.ini has a title/message" if vt_exists else "no virttrip"},
        {"id": "W2", "label": "Trip blogs are in the virtual trip",
         "passed": not unmigrated,
         "detail": "no un-migrated blogs" if not unmigrated
                   else f"{len(unmigrated)} trip blog(s) not in the virttrip: {', '.join(unmigrated)}"},
        {"id": "W3", "label": "Virtual trip website was built",
         "passed": vt_built_files > 0,
         "detail": f"{vt_built_files} built file(s) in the virttrip"
                   if vt_built_files else "no built website (frozen biota is empty)"},
        # Retiring deletes metadata/reports/ along with everything else, and the
        # day summaries the report is built from go with it — so once this trip
        # is gone, an unarchived species count is gone for good and cannot be
        # recomputed. Unlike the blogs, nothing else preserves it.
        _verdict_warning(master_db, name),
    ]

    can_retire = all(b["passed"] for b in blockers)
    has_warnings = any(not w["passed"] for w in warnings)
    size_bytes, n_files = _dir_size(trip_db)
    # A trip whose own day data (metadata/review) is gone is a LEFTOVER: someone
    # already deleted the contents outside the app, and what is listed in the
    # Trips panel is an empty husk. The panel is a directory listing, so the row
    # only disappears when the folder itself goes.
    gutted = not os.path.isdir(os.path.join(trip_db, "metadata", "review"))
    return {"ok": True, "name": name, "trip_db": trip_db, "virttrip_db": virttrip_db,
            "match_method": match_method,
            "size_bytes": size_bytes, "n_files": n_files,
            "n_pictures": _count_pictures(trip_db),
            "unmigrated_blogs": unmigrated,
            "trip_blogs": sorted(trip_blogs), "gutted": gutted,
            "blockers": blockers, "warnings": warnings,
            "failed_blockers": [b["label"] for b in blockers if not b["passed"]],
            "can_retire": can_retire, "has_warnings": has_warnings}


def execute_retire(trip_db, attempts=10):
    """Permanently delete the trip repo. Returns (ok, error). The caller must
    have confirmed the scan blockers passed and the user acknowledged warnings.

    Robust against the common Windows failure modes: read-only files (cleared and
    retried) and transient sharing-violations (WinError 32 — most often Dropbox
    syncing, which releases one file as it grabs the next). Each pass deletes
    everything that isn't locked right now and collects the rest; it retries the
    whole thing several times, so a moving set of transient locks clears in a few
    passes rather than one-lock-at-a-time."""
    import stat
    import time

    trip_db = os.path.normpath(trip_db)
    if not os.path.isdir(trip_db):
        return (False, f"trip folder not found: {trip_db}")

    locked = []

    def _onerror(func, path, _exc_info):
        # Clear a read-only flag and retry; if it's still locked, record it and
        # keep going so this pass removes everything that CAN be removed.
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            locked.append(path)

    for attempt in range(attempts):
        locked.clear()
        try:
            shutil.rmtree(trip_db, onerror=_onerror)
        except Exception as e:
            locked.append(str(e))
        if not os.path.isdir(trip_db):
            logger.info(f"Retire trip: deleted {trip_db}"
                        + (f" (after {attempt + 1} passes)" if attempt else ""))
            return (True, "")
        logger.warning(f"Retire trip: pass {attempt + 1}/{attempts}: "
                       f"{len(locked)} file(s) still locked, retrying…")
        time.sleep(0.8)

    return (False,
            f"{len(locked)} file(s) are still locked after {attempts} passes — most "
            f"often Dropbox syncing this folder, or an open Explorer/app window. "
            f"Pause Dropbox syncing (or close whatever is using the trip), then "
            f"retire again to finish. Most of the trip was already removed; "
            f"re-running clears the rest.")


def _fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def format_report(rep):
    if not rep.get("ok"):
        return f"Trip-retire scan failed: {rep.get('error', 'unknown error')}"
    lines = [
        f"Retire trip: {rep['name']}  (dry-run — no changes)",
        f"  trip data : {rep['trip_db']}",
        f"  virttrip  : {rep['virttrip_db'] or '(none)'}",
        f"  size      : {_fmt_size(rep['size_bytes'])}   files: {rep.get('n_files', 0)}"
        f"   pictures: {rep['n_pictures']}",
    ]
    if rep.get("gutted"):
        lines += [
            "",
            "  LEFTOVER — this trip's own day data (metadata/review) is already gone,",
            "  so what is left is a husk that only keeps the row in the Trips panel.",
        ]
    # The day blogs are the one thing in here that exists nowhere else, so name
    # every file rather than reporting a count.
    blogs = rep.get("trip_blogs") or []
    lines.append("")
    if blogs:
        lines.append(f"  Day blogs in this folder ({len(blogs)}) — deleted with it:")
        lines += [f"    {b}" for b in blogs]
    else:
        lines.append("  Day blogs in this folder: none")
    lines += [
        "",
        "  Blockers (all must pass):",
    ]
    for b in rep["blockers"]:
        lines.append(f"    [{'OK' if b['passed'] else 'XX'}] {b['label']} — {b['detail']}")
    lines.append("")
    lines.append("  Warnings (review before retiring):")
    for w in rep["warnings"]:
        lines.append(f"    [{'OK' if w['passed'] else '!!'}] {w['label']} — {w['detail']}")
    lines.append("")
    if rep["can_retire"]:
        lines.append("  Verdict: CAN RETIRE"
                     + (" (with warnings to acknowledge)" if rep["has_warnings"] else ""))
    else:
        # Not a dead end: the blockers describe what this machine would lose, and
        # the dialog lets you override them by name. Refusing outright is what
        # sends people to Explorer, which leaves exactly the husk above.
        lines.append("  Verdict: BLOCKED — delete only by overriding "
                     f"{len(rep['failed_blockers'])} blocker(s) explicitly.")
    return "\n".join(lines)
