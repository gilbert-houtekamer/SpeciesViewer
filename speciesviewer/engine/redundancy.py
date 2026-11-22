"""Redundant pictures — which candidates of ONE species could go, and the numbers.

ONE JOB: from the measured pictures of a species (engine.picture_score), say per
picture: your keeper, already decided by you, keep, or suggest Purple; and count
what that means. Pure functions — no files, no Qt, no writes.

The rules (docs/plans/picture_score_plan.md):
  - KEEPER  = stars, Red, Yellow or a JPG. Never suggested.
  - DECIDED = Blue or Purple: your verdict already; left alone, not counted.
  - CANDIDATE = no stars AND (Green or no colour). Only these can be suggested.
  - POSE = frames ≤ `gap` s apart whose layout hash differs ≤ `change`.
  - Per DAY (an encounter), on top of your keepers: the sharpest frame of every
    pose (best poses first), then second-best frames, up to `per_pose` per
    pose — your own keepers in a pose count towards that: you already chose
    its best — until the day has `per_day`. A frame below `soft` × its pose's best is
    never kept for its own sake. The rest of the candidates: suggest Purple.
  - How many per day depends on how often you saw the species: per_day_for(days).

INVARIANT: a keeper or a decided picture never gets the verdict "purple"; only
a candidate can.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from engine.picture_score import hamming

KEEPER, DECIDED, KEEP, PURPLE = "keeper", "decided", "keep", "purple"


@dataclass
class Settings:
    per_day: int = 10
    per_pose: int = 2
    gap: float = 30.0          # seconds that start a new pose
    change: float = 0.25       # how different a frame must look to start a new pose
    soft: float = 0.6          # below this share of the pose's best: never kept
    cap: int = 0               # optional limit for the whole species (0 = none)


def per_day_for(days_seen: int, *, base: float = 24.0, low: int = 3, high: int = 20) -> int:
    """Pictures kept per day besides your keepers: more for a species you met
    on few days. 1 day → 20, 4 → 12, 9 → 8, 25 → 5, 67 → 3."""
    if days_seen <= 0:
        return high
    return max(low, min(high, round(base / math.sqrt(days_seen))))


def default_settings(pics) -> Settings:
    return Settings(per_day=per_day_for(len(days_of(pics))))


# ── what a picture is to you ─────────────────────────────────────────────────

def is_keeper(p) -> bool:
    return bool(p["jpg"]) or p["stars"] >= 1 or p["label"] in ("Red", "Yellow")


def is_decided(p) -> bool:
    return not is_keeper(p) and p["label"] in ("Blue", "Purple")


def is_candidate(p) -> bool:
    return not is_keeper(p) and not is_decided(p)


# ── from the master ──────────────────────────────────────────────────────────

def _time(s) -> datetime | None:
    try:
        return datetime.strptime((s or "")[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _place(r) -> str:
    for k in ("Location", "City", "Country"):
        v = (r.get(k) or "").strip()
        if v:
            return v
    return ""


def from_master_rows(rows, master_root) -> list[dict]:
    """Picture dicts for scoring, from MasterDB picture rows (Files already a
    list). The source is the RAW/HEIC, else a JPG; a picture with neither on
    disk is left out. Sorted by time."""
    from utils.raw_extensions import is_jpeg, is_raw_or_heic
    root = Path(master_root)
    out = []
    for r in rows:
        folder = root / (r.get("TargetPath") or "")
        paths = [folder / f for f in (r.get("Files") or [])]
        sizes = {}
        for p in paths:
            try:
                sizes[p] = p.stat().st_size
            except OSError:
                pass
        src = next((p for p in paths if p in sizes and is_raw_or_heic(p)), None) \
            or next((p for p in paths if p in sizes and is_jpeg(p)), None)
        if src is None:
            continue
        out.append({
            "upn": r["UniquePictureName"], "src": src,
            "time": _time(r.get("DateTimeOriginal")),
            "label": (r.get("Label") or "").strip(),
            "stars": int(float(r.get("Rating") or 0) or 0),
            "jpg": bool((r.get("BestJPG") or "").strip()),
            "place": _place(r),
            "bytes": sum(sizes.values()),
        })
    out.sort(key=lambda p: (p["time"] or datetime.min, p["upn"]))
    return out


# ── poses + selection ────────────────────────────────────────────────────────

def poses(pics, gap: float, change: float) -> list[list[dict]]:
    """Consecutive frames (pics sorted by time) of one pose."""
    groups: list[list[dict]] = []
    for p in pics:
        g = groups[-1] if groups else None
        if (g and p["time"] and g[-1]["time"]
                and (p["time"] - g[-1]["time"]).total_seconds() <= gap
                and hamming(p["dhash"], g[-1]["dhash"]) <= change):
            g.append(p)
        else:
            groups.append([p])
    return groups


def day_of(p) -> date | None:
    return p["time"].date() if p["time"] else None


def days_of(pics) -> set:
    return {day_of(p) for p in pics if p["time"]}


def select(pics, s: Settings) -> list[list[dict]]:
    """Set p["verdict"] on every picture; returns the poses."""
    groups = poses(pics, s.gap, s.change)
    for p in pics:
        p["verdict"] = KEEPER if is_keeper(p) else DECIDED if is_decided(p) else PURPLE
    by_day: dict = {}
    for g in groups:
        by_day.setdefault(day_of(g[0]), []).append(g)
    total = 0
    for day_groups in by_day.values():
        kept = 0
        ranked = [sorted(g, key=lambda p: p["sharp"], reverse=True) for g in day_groups]
        ranked.sort(key=lambda g: g[0]["sharp"], reverse=True)
        for rnd in range(s.per_pose):
            for g in ranked:
                if kept >= s.per_day or (s.cap and total >= s.cap) or rnd >= len(g):
                    continue
                p, best = g[rnd], g[0]["sharp"] or 1e-9
                already = sum(1 for q in g if q["verdict"] in (KEEPER, KEEP))
                if p["verdict"] == PURPLE and already < s.per_pose and p["sharp"] >= s.soft * best:
                    p["verdict"] = KEEP
                    kept += 1
                    total += 1
    return groups


# ── the numbers ──────────────────────────────────────────────────────────────

def statistics(pics) -> dict:
    """What the species is in the master, before any suggestion."""
    days = days_of(pics)
    times = [p["time"] for p in pics if p["time"]]
    cand = [p for p in pics if is_candidate(p)]
    return {
        "pictures": len(pics),
        "days": len(days),
        "places": len({p["place"] for p in pics if p["place"]}),
        "first": min(times) if times else None,
        "last": max(times) if times else None,
        "stars": sum(1 for p in pics if p["stars"] >= 1),
        "red": sum(1 for p in pics if p["label"] == "Red"),
        "yellow": sum(1 for p in pics if p["label"] == "Yellow"),
        "jpg": sum(1 for p in pics if p["jpg"]),
        "keepers": sum(1 for p in pics if is_keeper(p)),
        "decided": sum(1 for p in pics if is_decided(p)),
        "candidates": len(cand),
        "candidate_bytes": sum(p["bytes"] for p in cand),
        "bytes": sum(p["bytes"] for p in pics),
    }


def outcome(pics, groups) -> dict:
    """What the suggestion would do (after select())."""
    purple = [p for p in pics if p.get("verdict") == PURPLE]
    return {
        "poses": len(groups),
        "keep": sum(1 for p in pics if p.get("verdict") == KEEP),
        "purple": len(purple),
        "purple_bytes": sum(p["bytes"] for p in purple),
    }


def per_day_rows(pics) -> list[dict]:
    """One row per day (after select()): day, places, pictures, keepers, kept,
    suggested Purple, bytes freed."""
    rows: dict = {}
    for p in pics:
        d = day_of(p)
        r = rows.setdefault(d, {"day": d, "places": set(), "pictures": 0, "keepers": 0,
                                "keep": 0, "purple": 0, "purple_bytes": 0})
        r["pictures"] += 1
        if p["place"]:
            r["places"].add(p["place"])
        v = p.get("verdict")
        if v == KEEPER:
            r["keepers"] += 1
        elif v == KEEP:
            r["keep"] += 1
        elif v == PURPLE:
            r["purple"] += 1
            r["purple_bytes"] += p["bytes"]
    out = sorted(rows.values(), key=lambda r: (r["day"] is None, r["day"] or date.min))
    for r in out:
        r["places"] = ", ".join(sorted(r["places"]))
    return out


def gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"

