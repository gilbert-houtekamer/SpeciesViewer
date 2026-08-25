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
#   - SV never FORMATS the source card. It does delete pictures from it — see
#     delete_from_card — but only ones just verified on the keeper card, only on
#     a separate explicit request, and never a file it does not recognise. The
#     original "never deletes" rule was there because the card holds the only
#     original, which a verified copy is exactly what stops being true; doing it
#     here is also safer than doing it by hand, because every card looks alike
#     and a tool that stops at "safe to format" invites the wrong one to be wiped.
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
CARD_RESERVE_NAME = ".sv_ingest_reserve"
KEEPER_MARKER = "SV_KEEPERS.json"

# Renders that belong to a picture. They are DERIVED and in principle
# re-creatable from the RAW plus the .dop — but only by the PhotoLab version
# that made them (denoise codes are per-version and forward-incompatible), and
# only at minutes per frame. Measured on a real card: every sidecar and render
# together came to 0.6% of the RAW payload, which is why there is no setting for
# this. A choice that can only ever move the total by half a percent is not a
# choice, it is a question asked on every run forever.
RENDER_EXTS = frozenset({".jpg", ".jpeg"})

# What a re-run is allowed to REPLACE on the keeper card. The RAW never changes;
# species names, colours and renders keep changing for days afterwards, so a
# keeper card that could not catch up would freeze on the day it was made.
REFRESH_EXTS = frozenset({".xmp", ".dop"}) | RENDER_EXTS

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
            "card_id": card_id(index),
            "emptied": (index or {}).get("emptied") or [],
            "volume_serial": (index or {}).get("volume_serial", ""),
            "cleared": (index or {}).get("cleared")}


def volume_serial(card_root) -> str:
    """The filesystem's own serial for this volume, '' where it cannot be had.

    Corroboration only, never the identity itself: it is free on Windows
    (GetVolumeInformationW is already called for the label) and awkward
    elsewhere, and this project is going to the Mac. The identity that counts is
    `card_id`, which we write ourselves and which therefore works everywhere.
    """
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong(0)
        root = str(Path(card_root))
        if not root.endswith(("\\", "/")):
            root += "\\"
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), buf, ctypes.sizeof(buf) // 2,
            ctypes.byref(serial), None, None, None, 0)
        return f"{serial.value:08X}" if ok else ""
    except Exception:
        return ""            # not Windows, or a volume that will not answer


def card_id(index) -> str:
    """The card's own identity, '' if it has never been given one."""
    return str((index or {}).get("card_id") or "")


def ensure_card_id(card_root) -> str:
    """Give this card a permanent identity, creating one the first time.

    ⚠ WRITES to the card, so it is called only where we are already writing —
    never from classification, which must stay a read-only question.

    Why the card needs a name of its own: all the cards look alike, the drive
    letter is whatever the reader handed out this time, and the volume label is
    often blank. Once files start being DELETED on the strength of a check, "is
    this the same card I checked?" stops being a nicety. A UUID in the card's
    own record answers it on any machine and any platform.
    """
    path = Path(card_root) / CARD_CACHE_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if raw.get("card_id"):
        return str(raw["card_id"])
    import uuid
    raw["card_id"] = str(uuid.uuid4())
    raw.setdefault("volume_serial", volume_serial(card_root))
    raw.setdefault("first_seen", datetime.now().isoformat(timespec="seconds"))
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return ""
    return raw["card_id"]


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
        # A card SV itself emptied is not a failure, it is a finished job. It
        # keeps its record (identity and history) with the file entries pruned,
        # so "no roster + an emptied event" is the signature of a card that is
        # ready to shoot on — and reporting that in red, under the heading NOT
        # SAFE TO EMPTY, was simply wrong about what had happened.
        emptied = report["identity"].get("emptied") or []
        if emptied:
            report["already_empty"] = True
            report["emptied_last"] = emptied[-1]
        else:
            report["blockers"].append(
                "The card's ingest record lists no pictures, and it has no record "
                "of ever having been emptied.")
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


def nothing_to_do(report) -> bool:
    """True for a card SV has already emptied — neither ready nor blocked."""
    return bool(report.get("already_empty"))


def can_empty(report) -> bool:
    """True when every frame on the card has a verdict AND there are frames to
    act on. The fullness rule is deliberately not consulted here — it is advice,
    this is the gate.

    An already-emptied card is excluded explicitly: it has a record and no
    blockers, so it satisfied the old test and would have offered to save
    keepers it no longer holds. Done is not the same as ready."""
    return (bool(report.get("roster")) and not report.get("blockers")
            and not report.get("already_empty"))


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
    if report.get("already_empty"):
        last = report.get("emptied_last") or {}
        out.append(f"This card is empty. SpeciesViewer emptied it on "
                   f"{str(last.get('when', '?'))[:10]}, deleting "
                   f"{last.get('deleted', '?')} picture(s). Nothing to do — it is "
                   f"ready to shoot on again.")
        return out
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
        src = Path(row["path"])
        if row["verdict"] == KEEPER:
            target_dir = base / day
        elif row["verdict"] == CONDEMNED and include_condemned:
            target_dir = base / "condemned" / day
        else:
            continue
        pairs.append((src, target_dir / name))
        # The whole stem group, not just the RAW. .xmp and .dop travel by
        # themselves (copy_known_files carries them); the renders do not, and
        # without them the card restores your work but not your pictures.
        for render in _renders_beside(src):
            pairs.append((render, target_dir / render.name))
    return pairs


def _renders_beside(raw: Path) -> list[Path]:
    """'N5040004_DxO.jpg', 'N5040004_DxO-small.jpg' — the JPGs belonging to one
    RAW. Matched as the stem exactly, or the stem followed by '_', so that
    N504000 can never swallow N5040004's renders."""
    out = []
    try:
        for sib in raw.parent.iterdir():
            if sib.suffix.lower() not in RENDER_EXTS:
                continue
            if sib.stem == raw.stem or sib.stem.startswith(raw.stem + "_"):
                out.append(sib)
    except OSError:
        pass
    return sorted(out)


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

    # Pre-flight, at the one moment the byte total is exact. A keeper card that
    # cannot hold the job should say so before it is half written — and this is
    # also the ONLY place the size of the renders is a real question. It is a
    # constraint when it happens, never a preference set in advance.
    from utils.disk_space import check_free_space
    need = 0
    for src, _dest in pairs:
        try:
            need += src.stat().st_size
        except OSError:
            pass
    problem = check_free_space(keeper_root, need,
                               what=f"Saving {len(pairs)} file(s)")
    if problem:
        return {"error": problem, "copied": 0, "total": len(pairs)}

    from batch.sv0ReadSDCard import copy_known_files
    log.info(f"Consolidating {len(pairs)} file(s) to "
             f"{card_folder_name(trip_name, card_label)}…")
    summary = copy_known_files(pairs, logger=log, progress=progress,
                               should_cancel=should_cancel,
                               refresh_exts=REFRESH_EXTS)
    summary["planned"] = len(pairs)
    if not summary.get("error") and not summary.get("failed"):
        write_keeper_marker(keeper_root, trip_name, card_label, report, summary)
    return summary


def write_keeper_marker(keeper_root, trip_name, card_label, report, summary):
    """Say, on the keeper card, what this folder is — written LAST, from what
    actually landed.

    It records the date the METADATA was as of, which is the honest part: the
    RAWs on a keeper card are final, but species names, colours and renders keep
    changing for days afterwards. A restore should know that these sidecars are a
    snapshot and that the daily work backup, if there is one, is newer. Do not
    turn this into a claim about the pictures being current — it is a claim about
    when they were copied.
    """
    path = Path(keeper_root) / KEEPER_MARKER
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {"version": 1, "cards": []}
    entry = {
        "folder": card_folder_name(trip_name, card_label),
        "trip": trip_name,
        "written": datetime.now().isoformat(timespec="seconds"),
        "metadata_as_of": datetime.now().isoformat(timespec="seconds"),
        "files": int(summary.get("copied", 0)) + int(summary.get("skipped", 0)),
        "frames_on_card": len(report.get("files", [])),
        "card_id": (report.get("identity") or {}).get("card_id", ""),
        "note": ("RAWs here are final. The .xmp/.dop/.jpg are a snapshot of that "
                 "date — a later work backup, if one exists, is newer and should "
                 "be restored FIRST."),
    }
    existing["cards"] = [c for c in existing.get("cards", [])
                         if c.get("folder") != entry["folder"]] + [entry]
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"Could not write {KEEPER_MARKER}: {e}")


def stamp_cleared(card_root, trip_name, keeper_card, summary, report,
                  folder="") -> bool:
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

    # BEFORE the read, not inside the dict below: ensure_card_id writes the file
    # itself, so minting it after `raw` was loaded would have this function write
    # its stale copy straight back over the new id.
    cid = ensure_card_id(card_root)

    path = Path(card_root) / CARD_CACHE_NAME
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return False

    raw["cleared"] = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "card_id": cid,
        "trip": trip_name,
        "keeper_card": str(keeper_card),
        # The folder the copy actually went into, so a delete tomorrow looks in
        # the same place rather than re-deriving a name that may have drifted
        # (the volume label is not always readable, and the fallback is a date).
        "folder": folder,
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


# ── Emptying the card ─────────────────────────────────────────────────────────
# The plan said SV never deletes on the source card. That rule was there because
# the card holds the ONLY original — which a verified copy is precisely the thing
# that stops being true. Doing it here rather than by hand is also the safer
# option, and for the reason the whole feature exists: every card looks the same,
# and a tool that stops at "safe to format" and leaves the doing to you is
# inviting the wrong card to be wiped. What follows re-proves the case at the
# moment of deletion rather than trusting a report from five minutes ago.


def plan_delete(card_root, report, keeper_root, trip_name, card_label) -> dict:
    """What could be deleted from the card, and what could not — no deletion.

    A card file may go only when it is accounted for AND, where a copy was
    supposed to be made, that copy is verifiably there. The two are different
    questions and both are asked:

      - keepers and condemned frames were COPIED, so their file must be found on
        the keeper card at the right size. Anything else is a hole in the copy.
      - executed and presumed frames were DISCARDED on purpose. There is no copy
        and there should not be one; deleting them is carrying out the decision
        already made. They are counted separately so the confirmation can say how
        many pictures are about to exist nowhere.
      - anything not in the roster — the .dat files, System Volume Information,
        a stray from another camera — is never touched and is reported, so the
        survivors are visible rather than silent.
    """
    base = Path(keeper_root) / card_folder_name(trip_name, card_label)
    out = {"delete": [], "no_copy": [], "unverified": [], "unknown": [],
           "bytes": 0, "card_id": ""}
    index = read_card_index(card_root)
    if not index:
        out["error"] = "This card has no record of what was on it."
        return out
    out["card_id"] = card_id(index)
    roster = card_roster(index)

    by_rel = {r["rel"]: r for r in report.get("files", [])}
    for rel, entry in roster.items():
        row = by_rel.get(rel)
        on_card = Path(card_root) / rel
        if not on_card.exists() or row is None:
            continue                       # already gone, or not in this report
        verdict = row["verdict"]
        if verdict in (KEEPER, CONDEMNED):
            name = row.get("laptop_name") or row["name"]
            day = row.get("day") or "unknown-date"
            where = (base / day / name if verdict == KEEPER
                     else base / "condemned" / day / name)
            try:
                ok = where.exists() and where.stat().st_size == on_card.stat().st_size
            except OSError:
                ok = False
            (out["delete"] if ok else out["unverified"]).append(str(on_card))
            if ok:
                out["bytes"] += entry.get("size") or 0
        elif verdict in (EXECUTED, PRESUMED):
            out["delete"].append(str(on_card))
            out["no_copy"].append(str(on_card))
            out["bytes"] += entry.get("size") or 0
        else:
            out["unverified"].append(str(on_card))

    known = {str(Path(card_root) / rel).lower() for rel in roster}
    try:
        for f in Path(card_root).iterdir():
            if f.is_file() and not f.name.startswith(".") \
                    and str(f).lower() not in known:
                out["unknown"].append(str(f))
    except OSError:
        pass
    return out


def delete_from_card(card_root, plan, expect_card_id="", logger_=None) -> dict:
    """Carry out `plan`, then prune the card's record to match.

    ⚠ Refuses if the card in the slot is not the card the plan was made for.
    Cards are swapped between one step and the next — that is the whole reason
    they are hard to tell apart — and this is the one operation where being
    wrong cannot be undone.

    The card's own record SURVIVES, pruned. It is tempting to wipe it along with
    everything else (that is what deleting by hand does), but it carries the
    card's identity and its read history, and those are what make the NEXT empty
    safe: a card whose id is thrown away every cycle is a stranger every time.
    The entries for the deleted files go, so the card is not left claiming to
    hold pictures it no longer has — which is exactly the mess a manual delete
    leaves behind, and which would break the clear stamp on the next trip.
    """
    log = logger_ or logger
    out = {"deleted": 0, "failed": 0, "bytes": 0, "error": ""}
    if plan.get("error"):
        out["error"] = plan["error"]
        return out

    here = card_id(read_card_index(card_root))
    if expect_card_id and here != expect_card_id:
        out["error"] = ("This is not the card that was checked — its identity "
                        "does not match. Nothing has been deleted.")
        log.error(f"❌ {out['error']} (expected {expect_card_id}, found {here or 'none'})")
        return out

    root = Path(card_root)
    gone = set()
    for name in plan.get("delete", []):
        f = Path(name)
        try:
            size = f.stat().st_size
            f.unlink()
            gone.add(str(f.relative_to(root)).replace("\\", "/").lower())
            out["deleted"] += 1
            out["bytes"] += size
        except OSError as e:
            out["failed"] += 1
            log.warning(f"Could not delete {f.name} from the card: {e}")

    # Prune the record LAST, and only of what actually went.
    path = root / CARD_CACHE_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["files"] = {k: v for k, v in (raw.get("files") or {}).items()
                        if k not in gone}
        raw.pop("cleared", None)          # the stamp described a card that is now empty
        raw.setdefault("emptied", []).append({
            "when": datetime.now().isoformat(timespec="seconds"),
            "deleted": out["deleted"],
        })
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except (OSError, ValueError) as e:
        log.warning(f"Deleted the pictures but could not update the card's record: {e}")

    log.info(f"🧹 Deleted {out['deleted']} file(s) from {card_root} "
             f"({out['bytes'] / 1e9:.2f} GB freed).")
    return out
