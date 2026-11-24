"""The AI's suggestion for ONE DAY of new pictures — what could go, and what each
pose is.

ONE JOB: from the measured pictures of a day (engine.ai_measure: animal box,
size, sharp on the animal, names; engine.picture_score: the pose hash) decide
per picture a verdict and per pose a name:
  verdict  "purple" with a reason — no animal found / out of focus / small /
           too many of the same — or "keep"; None for a picture that is yours
           already (a keeper: stars above the day's usual, Red, Yellow, a JPG —
           or decided: Blue, Purple), exactly the candidate rule of the master
           suggestion (engine.redundancy.is_candidate);
  name     the first name of the pose's best frame when it is at or above the
           threshold, else "not sure"; no name when nothing in the pose has an
           animal.
Within a pose the frames are ranked sharp on the animal first, then size.
Then, per species (a pose with a sure name), the day keeps no more than the
master's own rule allows (engine.redundancy.per_day_for): 12 for a species
your master has never had, down to 4 for one you photographed on 32 days or
more — your own keepers count — so common birds are thinned much harder.

INVARIANT: a view only — nothing is written here. Only candidates are ever
"purple"; your keepers count toward the frames kept of their pose.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from engine import redundancy as rd

NO_ANIMAL, OUT_OF_FOCUS, SMALL, TOO_MANY, PLENTY = (
    "no animal found", "out of focus", "small", "too many of the same",
    "plenty of this species")


@dataclass
class DaySettings:
    threshold: int = 45      # % — a pose's name is used at or above this
    sharp_cut: int = 15      # % — below this, sharp on the animal = out of focus
    per_pose: int = 2        # frames kept per pose (your keepers count)
    small_px: int = 0        # px — smaller animals go when the pose has a bigger one (0 = off)
    use_master: bool = True  # fewer per day of a species your master has often
    gap: float = rd.Settings().gap
    change: float = rd.Settings().change


def _rank(p) -> tuple:
    return (p.get("sharp_pct") or 0.0, p.get("px") or 0)


def day_limit(master_days: int) -> int:
    """Pictures of one species kept today, given the days the master has it."""
    return rd.per_day_for(master_days + 1)


def decide(pics: list[dict], s: DaySettings,
           master_days: dict | None = None) -> list[list[dict]]:
    """Set p["verdict"], p["why"], p["pose"], p["name"] (dict | None) and
    p["sure"] on every picture (sorted by time); returns the poses.
    `master_days` = {lower-case scientific name: days in the master}."""
    counts = Counter(p.get("stars", 0) for p in pics)
    floor = rd.star_floor(counts)
    for p in pics:
        p["star_floor"] = floor
        p.setdefault("dhash", "0" * 16)
    groups = rd.poses(pics, s.gap, s.change)
    for gi, g in enumerate(groups):
        order = sorted(g, key=_rank, reverse=True)
        best = next((p for p in order if p.get("box")), None)
        name, sure = None, False
        if best is not None and best.get("names"):
            first = best["names"][0]
            sure = first["prob"] * 100 >= s.threshold
            name = first if sure else None
        biggest = max((p.get("px") or 0) for p in g)
        kept = sum(1 for p in g if rd.is_keeper(p))
        for p in order:
            p["pose"], p["name"], p["sure"] = gi, name, sure
            p["has_animal_pose"] = best is not None
            p["why"] = ""
            if not rd.is_candidate(p):
                p["verdict"] = None
                continue
            if not p.get("box"):
                p["verdict"], p["why"] = "purple", NO_ANIMAL
            elif (p.get("sharp_pct") is not None) and p["sharp_pct"] < s.sharp_cut:
                p["verdict"], p["why"] = "purple", OUT_OF_FOCUS
            elif s.small_px and (p.get("px") or 0) < s.small_px <= biggest:
                p["verdict"], p["why"] = "purple", SMALL
            elif kept < s.per_pose:
                p["verdict"] = "keep"
                kept += 1
            else:
                p["verdict"], p["why"] = "purple", TOO_MANY
    if s.use_master and master_days is not None:
        by_species: dict[str, list[dict]] = {}
        for p in pics:
            if p.get("name") is not None:
                by_species.setdefault(p["name"]["sci"].lower(), []).append(p)
        for sci, group in by_species.items():
            limit = day_limit(master_days.get(sci, 0))
            kept = sum(1 for p in group if rd.is_keeper(p))
            for p in sorted((p for p in group if p.get("verdict") == "keep"),
                            key=_rank, reverse=True):
                if kept < limit:
                    kept += 1
                else:
                    p["verdict"], p["why"] = "purple", PLENTY
    return groups


def summary(pics: list[dict], groups: list[list[dict]]) -> dict:
    """The day in numbers, and the species found: {name: {poses, pictures, best}}."""
    why = Counter(p["why"] for p in pics if p.get("verdict") == "purple")
    species: dict[str, dict] = {}
    not_sure = no_animal = 0
    for g in groups:
        p0 = g[0]
        if not p0.get("has_animal_pose"):
            no_animal += 1
            continue
        if p0.get("name") is None:
            not_sure += 1
            continue
        n = p0["name"]
        key = n.get("name") or n.get("sci")
        e = species.setdefault(key, {"sci": n.get("sci"), "how": n.get("how", ""),
                                     "poses": 0, "pictures": 0, "best": 0.0,
                                     "first": None, "keys": [], "kept": 0})
        e["poses"] += 1
        e["pictures"] += len(g)
        e["keys"] += [p.get("key") for p in g]
        e["kept"] += sum(1 for p in g if p.get("verdict") != "purple")
        e["best"] = max(e["best"], n["prob"])
        if e["first"] is None:
            e["first"] = g[0].get("key")
    return {
        "pictures": len(pics), "poses": len(groups),
        "purple": sum(1 for p in pics if p.get("verdict") == "purple"),
        "keep": sum(1 for p in pics if p.get("verdict") == "keep"),
        "yours": sum(1 for p in pics if p.get("verdict") is None),
        "bytes": sum(p.get("bytes", 0) for p in pics if p.get("verdict") == "purple"),
        "why": dict(why), "species": species,
        "not_sure_poses": not_sure, "no_animal_poses": no_animal,
    }
