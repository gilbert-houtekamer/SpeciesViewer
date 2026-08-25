#!/usr/bin/env python3

# Emptying an SD card safely — decide whether a card may be formatted, and
# consolidate the frames worth keeping onto a second card first.
#
# Design: docs/plans/empty_sdcard_plan.md. The short version:
#
#   The SOURCE card is the WITNESS, not a copy source. Its .sv_ingest_cache.json
#   is the only record of what was physically on it, and all cards look alike, so
#   it has to be in a slot to be identified at all. The PICTURES come off the
#   laptop, because that is where the prefixed names and the .xmp files carrying
#   the culling work live.
#
#   The gate is the feature; the copy is the easy part. Before a card may be
#   formatted, EVERY frame it holds must have a verdict: kept, or deliberately
#   discarded. A frame with no verdict blocks, unconditionally.
#
# ⭐ Rules that must not be undone (they are the whole safety argument):
#   - The card's own json is the roster. NEVER the laptop day folder: a frame
#     that was shot and never copied is absent from the day folder, so a check
#     against it passes by not existing — and that frame is the one this whole
#     module exists to protect.
#   - A card with no roster is UNKNOWN, never clear. Absence of evidence blocks.
#   - SV never formats and never deletes on the source card. It writes a "clear"
#     stamp into the card's own json; that is the entire extent of its authority
#     there. The format is done by hand, in the camera.
#   - The stamp is written LAST, from the files that actually landed — the work
#     backup learned this when a manifest written up front produced a ZIP
#     claiming files it did not contain, and replaced the good one.

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("speciesviewer")

CARD_CACHE_NAME = ".sv_ingest_cache.json"

# Colour labels, lower-cased. Red/Yellow/Green survive; Blue/Purple are a death
# sentence that is not always carried out immediately, which is why CONDEMNED is
# a state of its own rather than being folded into "discarded".
KEEPER_COLORS    = frozenset({"red", "yellow", "green"})
CONDEMNED_COLORS = frozenset({"blue", "purple"})

# The verdicts, in report order. The two that BLOCK are first — a report is read
# from the top, and the blocking news should not be below a wall of good news.
UNACCOUNTED = "unaccounted"    # on the card, never ingested — the dangerous one
UNJUDGED    = "unjudged"       # on the laptop, no colour yet — not culled
MISMATCH    = "mismatch"       # name matched, size did not — we matched wrongly
KEEPER      = "keeper"         # red/yellow/green — goes to the keeper card
CONDEMNED   = "condemned"      # blue/purple, sentence not yet executed
EXECUTED    = "executed"       # found in a .trash — evidence, not inference
PRESUMED    = "presumed"       # ingested, now nowhere — trash emptied by hand

VERDICT_ORDER = [UNACCOUNTED, UNJUDGED, MISMATCH, KEEPER, CONDEMNED,
                 EXECUTED, PRESUMED]
BLOCKING = frozenset({UNACCOUNTED, UNJUDGED, MISMATCH})

VERDICT_TEXT = {
    UNACCOUNTED: "never ingested — still only on this card",
    UNJUDGED:    "ingested but not yet culled — no keep-or-discard colour",
    MISMATCH:    "a file of the same name but a different size — cannot match",
    KEEPER:      "kept (red/yellow/green)",
    CONDEMNED:   "marked for deletion (blue/purple), not yet deleted",
    EXECUTED:    "deleted — found in the trash",
    PRESUMED:    "ingested and no longer on the laptop — deleted",
}


# ── The card side: roster and identity ────────────────────────────────────────

def read_card_index(card_root) -> dict | None:
    """The card's own ingest cache, or None when it has none.

    Read directly rather than through `_CardCache`, which lays down the 1 MB
    reserve as a side effect of loading. Deciding whether a card is clear is a
    read-only question and must not write to the card to answer it.

    None means UNKNOWN — a card read by another tool, or write-protected when we
    saw it, has no roster and can never be declared clear.
    """
    path = Path(card_root) / CARD_CACHE_NAME
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or "files" not in raw:
        return None
    return raw


def card_roster(index) -> dict:
    """{relative path (lower, forward slashes): entry} — every file the card has
    shown us. `last_copied` is the field that matters: it is stamped at copy time
    and deliberately PRESERVED across later rewrites, so its absence means this
    frame was never ingested by anyone, ever."""
    return dict((index or {}).get("files") or {})


def card_identity(index) -> dict:
    """What this card says about itself: the most recent read, and any clear
    stamp. This is how a card in a slot is told apart from an identical-looking
    one in the same bag."""
    reads = (index or {}).get("reads") or []
    return {"last_read": reads[-1] if reads else None,
            "reads": len(reads),
            "cleared": (index or {}).get("cleared")}


def card_fullness(card_root) -> dict:
    """{total, free, pct_used} for the card, or {} when it cannot be measured."""
    try:
        usage = shutil.disk_usage(str(card_root))
    except OSError:
        return {}
    if not usage.total:
        return {}
    return {"total": usage.total, "free": usage.free,
            "pct_used": (usage.total - usage.free) * 100.0 / usage.total}


# ── The laptop side: where each frame ended up ────────────────────────────────

def _is_day_name(name: str) -> bool:
    """'2026-05-04' — the day-folder shape the whole pipeline uses."""
    return (len(name) == 10 and name[4] == "-" and name[7] == "-"
            and name[:4].isdigit() and name[5:7].isdigit() and name[8:].isdigit())


def index_project(project_root) -> tuple[dict, dict]:
    """ONE walk of the project pictures root → (live, trashed).

    live    {filename lower: (path, day)}   — in a day folder
    trashed {filename lower: path}          — under a .trash anywhere below it

    Both are keyed on the laptop's own filename, lower-cased. The card's names
    are NOT the same: ingest prefixes them ("N5040114.ORF" becomes
    "2026 North Greece - N5040114.ORF"), so the bridge between the two is
    `derive_prefixes` below, not a plain lookup.

    Culling moves a condemned stem to <day>/.trash/<stem>/ (or the project-root
    .trash, when the viewer was opened on a file list spanning days), which is
    why trashed files are indexed rather than skipped: their presence is the
    evidence that a death sentence was actually carried out.
    """
    root = Path(project_root)
    live: dict[str, tuple[Path, str]] = {}
    trashed: dict[str, Path] = {}
    if not root.is_dir():
        return live, trashed

    import os
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel = here.relative_to(root).parts
        in_trash = any(part.lower() == ".trash" for part in rel)
        # Prune the caches, but NOT .trash — it is evidence.
        dirnames[:] = [d for d in dirnames
                       if d.lower() == ".trash" or not d.startswith(".")]
        day = rel[0] if rel and _is_day_name(rel[0]) else ""
        for name in filenames:
            if name.startswith("."):
                continue
            key = name.lower()
            if in_trash:
                trashed.setdefault(key, here / name)
            else:
                live.setdefault(key, (here / name, day))
    return live, trashed


def derive_prefixes(card_names, laptop_names, sample=40) -> list[str]:
    """The ingest prefixes in play, worked out from the two name sets.

    ⚠ THIS IS THE BRIDGE, and getting it wrong fails SAFE-LOOKING rather than
    safe: a card whose names match nothing on the laptop reads as a card whose
    every frame was deliberately deleted, i.e. as ready to format. It was written
    without this step first and did exactly that — the only reason the first real
    card passed was that it had been built by copying already-prefixed files back
    onto a card, so the two names happened to be identical.

    Rather than being told the prefix (which the caller may have changed between
    reads, or may not know for a card from a previous trip), derive it: for a
    sample of card names, find laptop names ENDING with them and keep what came
    before. "" is always included, for a card read with no prefix at all.
    """
    tails = list(card_names)[:sample]
    found = {""}
    for tail in tails:
        for laptop in laptop_names:
            if laptop.endswith(tail) and len(laptop) > len(tail):
                found.add(laptop[:-len(tail)])
    return sorted(found, key=len)


def _label_of(picture: Path, caches: dict) -> str:
    """The colour on a picture's XMP sidecar, '' when it has none.

    Read through the per-folder sidecar cache, so a day already opened in the
    culling viewer costs a dict lookup rather than an exiftool run. The sidecar
    suffix is matched case-insensitively: this project's own folders hold '.XMP'
    as well as '.xmp', and a plain with_suffix() probe misses one of them off
    Windows.
    """
    from engine.sidecar_cache import SidecarCache
    folder = picture.parent
    entry = caches.get(folder)
    if entry is None:
        names = {}
        try:
            for p in folder.iterdir():
                if p.suffix.lower() == ".xmp":
                    names[p.name.lower()] = p
        except OSError:
            pass
        entry = (SidecarCache(folder), names)
        caches[folder] = entry
    cache, names = entry
    xmp = names.get(f"{picture.stem}.xmp".lower())
    if xmp is None:
        return ""
    row = cache.get(xmp)
    if row is None:
        from engine.sidecar_cache import read_xmp_rows
        rows = read_xmp_rows([xmp])
        row = rows[0] if rows else {}
        if row:
            cache.put(xmp, row)
    return str((row or {}).get("Label") or "").strip().lower()


# ── The gate ──────────────────────────────────────────────────────────────────

def classify_card(card_root, project_root, logger_=None) -> dict:
    """Give every frame on the card a verdict. Copies nothing, writes nothing.

    This function alone answers "can I format this card?", which is why it is
    worth having before any of the copying exists.
    """
    log = logger_ or logger
    index = read_card_index(card_root)
    report = {"card": str(card_root), "project": str(project_root),
              "identity": card_identity(index), "fullness": card_fullness(card_root),
              "files": [], "counts": {}, "days": {}, "blockers": [],
              "roster": bool(index)}

    if index is None:
        report["blockers"].append(
            "This card carries no ingest record, so there is no way to know what "
            "was on it. A card SV has never read cannot be declared safe to empty.")
        return report

    roster = card_roster(index)
    if not roster:
        report["blockers"].append("The card's ingest record is empty.")
        return report

    live, trashed = index_project(project_root)
    caches: dict = {}
    card_names = [rel.rsplit("/", 1)[-1] for rel in roster]
    prefixes = derive_prefixes(card_names, list(live) + list(trashed))
    report["prefixes"] = [p for p in prefixes if p]
    matched = 0

    def _find(where, name):
        """The laptop entry for a card filename, trying each derived prefix."""
        for pre in prefixes:
            hit = where.get(pre + name)
            if hit is not None:
                return hit
        return None

    for rel, entry in sorted(roster.items()):
        name = rel.rsplit("/", 1)[-1]
        row = {"rel": rel, "name": name, "size": entry.get("size"),
               "day": "", "label": "", "path": None}

        if not entry.get("last_copied"):
            row["verdict"] = UNACCOUNTED
            report["files"].append(row)
            continue

        hit = _find(live, name)
        if hit is not None:
            matched += 1
            path, day = hit
            row["path"], row["day"] = str(path), day
            row["laptop_name"] = path.name
            try:
                on_disk = path.stat().st_size
            except OSError:
                on_disk = None
            # Same name, different size: the match is wrong, and a wrong match
            # is worse than none — it would let a frame be called "kept" on the
            # strength of a different file. Block and say so.
            if on_disk is not None and entry.get("size") and on_disk != entry["size"]:
                row["verdict"] = MISMATCH
            else:
                label = _label_of(path, caches)
                row["label"] = label
                row["verdict"] = (KEEPER if label in KEEPER_COLORS else
                                  CONDEMNED if label in CONDEMNED_COLORS else
                                  UNJUDGED)
        elif (gone := _find(trashed, name)) is not None:
            matched += 1
            row["path"] = str(gone)
            row["laptop_name"] = gone.name
            row["verdict"] = EXECUTED
        else:
            row["verdict"] = PRESUMED
        report["files"].append(row)

    for row in report["files"]:
        v = row["verdict"]
        report["counts"][v] = report["counts"].get(v, 0) + 1
        day = row["day"] or "—"
        report["days"].setdefault(day, {})
        report["days"][day][v] = report["days"][day].get(v, 0) + 1

    # ⭐ THE GUARDRAIL. Every frame "presumed deleted" and not one match is not a
    # card of deleted pictures, it is a card that was matched against the wrong
    # folder — or a naming scheme this bridge does not understand. Both look
    # identical in the counts and one of them says "safe to format". Refuse.
    if roster and not matched:
        report["blockers"].append(
            f"Not one of the {len(roster)} pictures on this card could be found "
            f"in {project_root}. That is far more likely to be the wrong folder "
            f"than a card whose every picture was deleted, so nothing is assumed.")

    for v in (UNACCOUNTED, UNJUDGED, MISMATCH):
        n = report["counts"].get(v, 0)
        if not n:
            continue
        if v == UNJUDGED:
            # An unjudged frame that nonetheless CARRIES a label is not the same
            # news as a blank one, and reporting both as "no colour set" sends
            # the reader to a file that visibly has one. Grey is the case in the
            # wild: it is a swatch on the viewer's colour FILTER bar and can
            # reach an XMP, but it is not one of label_colors.COLORS and carries
            # no keep-or-discard meaning, so it blocks like a blank — while
            # saying what it actually found.
            odd = sorted({r["label"] for r in report["files"]
                          if r["verdict"] == UNJUDGED and r["label"]})
            blank = n - sum(1 for r in report["files"]
                            if r["verdict"] == UNJUDGED and r["label"])
            if blank:
                report["blockers"].append(
                    f"{blank} picture(s) ingested but not yet culled — no colour set.")
            if odd:
                report["blockers"].append(
                    f"{n - blank} picture(s) carry a colour SV does not use "
                    f"({', '.join(o.title() for o in odd)}) — that is not a "
                    f"keep-or-discard verdict.")
            continue
        report["blockers"].append(f"{n} picture(s) {VERDICT_TEXT[v]}.")

    log.info(f"Card {card_root}: {len(report['files'])} frame(s) — "
             + ", ".join(f"{report['counts'][v]} {v}"
                         for v in VERDICT_ORDER if report["counts"].get(v)))
    return report


def fullness_warning(report, threshold_pct=99.0) -> str:
    """A sentence when the card is not full enough to be worth recycling, '' when
    it is. Deliberately ADVICE, not a blocker: mid-trip and out of cards, freeing
    a part-used card is exactly when this feature earns its keep, and a hard gate
    would make SV the obstacle at the worst possible moment. The unaccounted-frame
    check is the one that refuses outright."""
    full = report.get("fullness") or {}
    pct = full.get("pct_used")
    if pct is None or pct >= threshold_pct:
        return ""
    from utils.disk_space import human_bytes
    return (f"This card is {pct:.0f}% full ({human_bytes(full['free'])} still free) — "
            f"under the {threshold_pct:.0f}% that makes a card worth recycling. "
            f"You can go ahead anyway.")


def can_empty(report) -> bool:
    """True when every frame on the card has a verdict. The fullness rule is
    deliberately not consulted here — it is advice, this is the gate."""
    return bool(report.get("roster")) and not report.get("blockers")


def format_report(report) -> list[str]:
    """The report as plain lines, for the log and the dialog. Plain language:
    'never ingested' rather than a verdict constant."""
    out = []
    ident = report.get("identity") or {}
    last = ident.get("last_read") or {}
    if last:
        out.append(f"This card was last read into \"{last.get('trip', '?')}\" "
                   f"on {str(last.get('when', '?'))[:10]}.")
    if ident.get("cleared"):
        c = ident["cleared"]
        out.append(f"Already cleared on {str(c.get('when', '?'))[:10]} — "
                   f"{c.get('keepers', '?')} picture(s) went to {c.get('keeper_card', '?')}.")
    if not report.get("roster"):
        out.extend(report.get("blockers") or [])
        return out
    out.append(f"{len(report['files'])} frame(s) on the card:")
    for v in VERDICT_ORDER:
        n = report["counts"].get(v, 0)
        if n:
            out.append(f"  {n:5d}  {VERDICT_TEXT[v]}")
    return out


# ── Consolidation ─────────────────────────────────────────────────────────────

def card_folder_name(trip_name, card_label) -> str:
    """'2026 North Greece - card 03' — one folder per source card, because the
    card is the unit being freed and the unit a restore points at."""
    trip = (trip_name or "pictures").strip()
    label = (card_label or "").strip()
    return f"{trip} - {label}" if label else trip


def plan_consolidation(report, keeper_root, trip_name, card_label,
                       include_condemned=True) -> list[tuple[Path, Path]]:
    """(source, destination) for every picture that should reach the keeper card.

    Keepers mirror the laptop's day folders. Condemned frames go under
    `condemned/` — copied, not dropped, because formatting the source card is
    what makes that sentence final, and it should not be made silently on frames
    the user deliberately had not finished with.
    """
    base = Path(keeper_root) / card_folder_name(trip_name, card_label)
    pairs = []
    for row in report.get("files", []):
        if not row.get("path"):
            continue
        day = row.get("day") or "unknown-date"
        # The laptop's OWN filename, never the roster key: the card's index is
        # keyed lower-case (a Windows filesystem is case-insensitive, so the key
        # has to be), and copying under the key would land "P100.ORF" on the
        # keeper card as "p100.orf" — renamed, un-prefixed, and no longer the
        # name its .xmp and .dop are matched to by the rest of the pipeline.
        name = row.get("laptop_name") or Path(row["path"]).name
        if row["verdict"] == KEEPER:
            pairs.append((Path(row["path"]), base / day / name))
        elif row["verdict"] == CONDEMNED and include_condemned:
            pairs.append((Path(row["path"]), base / "condemned" / day / name))
    return pairs


def consolidate(report, keeper_root, trip_name, card_label,
                include_condemned=True, logger_=None,
                progress=None, should_cancel=None) -> dict:
    """Copy the keepers (and the condemned) to the keeper card, then stamp the
    source card. Refuses outright if the gate is not met — the caller may
    override the fullness advice, never this."""
    log = logger_ or logger
    if not can_empty(report):
        return {"error": "This card still has frames without a verdict.",
                "copied": 0, "total": 0}

    pairs = plan_consolidation(report, keeper_root, trip_name, card_label,
                               include_condemned)
    if not pairs:
        return {"error": "", "copied": 0, "total": 0,
                "note": "Nothing to copy — no kept pictures from this card."}

    from batch.sv0ReadSDCard import copy_known_files
    log.info(f"Consolidating {len(pairs)} picture(s) to "
             f"{card_folder_name(trip_name, card_label)}…")
    summary = copy_known_files(pairs, logger=log, progress=progress,
                               should_cancel=should_cancel)
    summary["planned"] = len(pairs)
    return summary


def stamp_cleared(card_root, trip_name, keeper_card, summary, report) -> bool:
    """Write the verdict back onto the SOURCE card, so the object in your hand
    answers "safe to format?" without the laptop — the same principle as the 1 MB
    reserve.

    Written LAST, and from `summary["copied"] + summary["skipped"]`, i.e. what is
    actually ON the keeper card now, never from what was planned.

    Self-cleaning: the stamp records the roster size it covers, so a card that
    has been shot on again — new files, no `last_copied` — no longer matches and
    the verdict reverts to unknown rather than lingering as a stale "safe".
    """
    landed = int(summary.get("copied", 0)) + int(summary.get("skipped", 0))
    if summary.get("error") or summary.get("failed"):
        logger.warning("Not stamping the card as cleared: the copy did not finish "
                       "cleanly. The card keeps its pictures.")
        return False

    path = Path(card_root) / CARD_CACHE_NAME
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return False

    raw["cleared"] = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "trip": trip_name,
        "keeper_card": str(keeper_card),
        "keepers": landed,
        "discarded": sum(report["counts"].get(v, 0)
                         for v in (EXECUTED, PRESUMED, CONDEMNED)),
        "roster_size": len(report.get("files", [])),
    }
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, separators=(",", ":"))
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"Could not stamp the card as cleared: {e}")
        return False
    logger.info(f"✅ Card stamped CLEAR — {landed} picture(s) are on {keeper_card}.")
    return True


def clear_verdict(card_root) -> str:
    """'CLEAR — safe to format', or '' — read straight off a card in a slot, with
    no help from the laptop. The stamp only counts while it still covers the card:
    shoot on it again and the roster grows past `roster_size`, which retires the
    stamp automatically rather than leaving a stale 'safe' on a card holding new
    pictures."""
    index = read_card_index(card_root)
    if not index:
        return ""
    stamp = index.get("cleared")
    if not stamp:
        return ""
    if len(card_roster(index)) != stamp.get("roster_size"):
        return ""
    return (f"CLEAR — safe to format. {stamp.get('keepers', '?')} picture(s) were "
            f"saved to {stamp.get('keeper_card', '?')} on "
            f"{str(stamp.get('when', '?'))[:10]}.")
