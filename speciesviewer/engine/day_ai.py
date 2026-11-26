"""The AI's suggestion for ONE DAY of new pictures — what could go, and what each
pose is.

ONE JOB: from the measured pictures of a day (engine.ai_measure: animal box,
size, sharp on the animal, names; engine.picture_score: the pose hash) decide
per picture a verdict and per pose a name:
  verdict  "purple" with a reason — no animal found / out of focus / small /
           cut off by the edge / too many of the same — or "keep"; None for a picture that is yours
           already (a keeper: stars above the day's usual, Red, Yellow, a JPG —
           or decided: Blue, Purple), exactly the candidate rule of the master
           suggestion (engine.redundancy.is_candidate);
  name     the first name of the pose's best frame when it is at or above the
           threshold, else "not sure"; a pose where nothing has an animal is
           named only from its sharpest frame as a whole picture (plants, when
           chosen for the country: still_leaders, engine.ai_measure.plant_names);
           p["plant_pose"] says so.
Within a pose the frames are ranked: a whole animal before one cut off by the
frame's edge (GH 2026-11-24: "strafpunten"), then sharp on the animal, then
size. A cut-off frame goes when its pose has a whole one that is in focus.
Then, per species (a pose with a sure name), the day keeps no more than the
master's own rule allows (engine.redundancy.per_day_for): 12 for a species
your master has never had, down to 4 for one you photographed on 32 days or
more — your own keepers count — so common birds are thinned much harder.

INVARIANT: a view only — nothing is written here. Only candidates are ever
"purple"; your keepers count toward the frames kept of their pose.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from engine import redundancy as rd

logger = logging.getLogger("speciesviewer")

NO_ANIMAL, OUT_OF_FOCUS, SMALL, CUT_OFF, TOO_MANY, PLENTY = (
    "no animal found", "out of focus", "small", "cut off by the edge",
    "too many of the same", "plenty of this species")
EDGE = 0.005             # a box this close to the frame's edge = the animal is cut off
JOIN_GAP = 120           # s — following goes on into the next pose when it starts this soon


@dataclass
class DaySettings:
    threshold: int = 45      # % — a pose's name is used at or above this
    sharp_cut: int = 15      # % — below this, sharp on the animal = out of focus
    per_pose: int = 2        # frames kept per pose (your keepers count)
    small_px: int = 0        # px — smaller animals go when the pose has a bigger one (0 = off)
    use_master: bool = True  # fewer per day of a species your master has often
    gap: float = rd.Settings().gap
    change: float = rd.Settings().change


def cut_off(box, edge: float = EDGE) -> bool:
    """The animal box touches the frame's edge: part of the animal is outside."""
    if not box:
        return False
    x0, y0, x1, y1 = box
    return x0 <= edge or y0 <= edge or x1 >= 1 - edge or y1 >= 1 - edge


def going(p) -> bool:
    """Suggested to go, or already marked to go (Blue / Purple)."""
    return p.get("verdict") == "purple" or p.get("label") in ("Blue", "Purple")


def _rank(p) -> tuple:
    return (not cut_off(p.get("box")), p.get("sharp_pct") or 0.0, p.get("px") or 0)


def day_limit(master_days: int) -> int:
    """Pictures of one species kept today, given the days the master has it."""
    return rd.per_day_for(master_days + 1)


class Stopwatch:
    """Where the time of a run went, per part: seconds and how often. Parts
    timed on helper threads (decoding ahead) run AT THE SAME TIME as the rest,
    so they are not part of the total; "waiting for …" is what they cost."""

    def __init__(self):
        import threading
        self.secs: dict = {}
        self.count: Counter = Counter()
        self._lock = threading.Lock()

    def add(self, name: str, secs: float) -> None:
        with self._lock:
            self.secs[name] = self.secs.get(name, 0.0) + secs
            self.count[name] += 1

    def part(self, name: str):
        import time
        from contextlib import contextmanager

        @contextmanager
        def timed():
            t = time.perf_counter()
            try:
                yield
            finally:
                self.add(name, time.perf_counter() - t)
        return timed()

    def line(self) -> str:
        """"part 12.3 s (40×, 308 ms each); …" in the order first timed."""
        out = []
        for name, s in self.secs.items():
            n = self.count[name]
            out.append(f"{name} {s:.1f} s" + (f" ({n}×, {s / n * 1000:.0f} ms each)"
                                                 if n > 1 else ""))
        return "; ".join(out)


class Prefetch:
    """load(key) for keys in a known order, AHEAD on worker threads: while the
    detector works on one picture (graphics card), the next ones are decoded
    (processor). get(key) in the same order; at most `ahead` held at a time."""

    def __init__(self, keys, load, ahead: int = 2):
        from concurrent.futures import ThreadPoolExecutor
        self.keys, self.load, self.ahead = list(keys), load, ahead
        self.pool = ThreadPoolExecutor(max_workers=ahead) if keys else None
        self.futures: dict = {}
        self.next = 0
        self._fill()

    def _fill(self):
        while self.next < len(self.keys) and len(self.futures) < self.ahead + 1:
            k = self.keys[self.next]
            self.futures[k] = self.pool.submit(self.load, k)
            self.next += 1

    def get(self, key):
        f = self.futures.pop(key, None)
        out = f.result() if f is not None else self.load(key)
        self._fill()
        return out

    def close(self):
        if self.pool:
            for f in self.futures.values():
                f.cancel()
            self.pool.shutdown(wait=True)


def find_animals(pics: list[dict], measure_one, load, cache, s: DaySettings | None = None,
                 progress=None, stopped=lambda: False, join: bool = False,
                 clock: Stopwatch | None = None) -> dict:
    """The animal in every picture, in time order: the detector on the first
    frame, then the animal FOLLOWED to the next frames of its pose
    (ai_measure.follow) — with `join` also into the next pose when it starts
    within JOIN_GAP ("Group similar pictures aggressively"); the detector
    again where the match is weak. p["followed"] says which frames were followed: a
    pose whose first frame was followed shows the same animal as the pose
    before, so it can share that pose's name (name_series). measure_one(p, im, box) =
    ai_measure.measure for one picture (box None = detect); load(src) = the
    upright picture; cache = ai_measure.AiCache. A picture measured before
    (detected or followed) is taken from the cache, undecoded.
    `clock` (Stopwatch) gets where the time went.
    Returns {"detected", "followed", "cached"} counts."""
    from engine.ai_measure import small_gray
    s = s or DaySettings()
    clock = clock or Stopwatch()
    for p in pics:
        p.setdefault("dhash", "0" * 16)
    done = {"detected": 0, "followed": 0, "cached": 0}
    n, i = len(pics), 0
    groups = rd.poses(pics, s.gap, s.change)
    todo = [p["src"] for g in groups for p in g
            if cache.boxes(p["src"]) is None and cache.followed(p["src"]) is None]
    def both(src):                       # on the worker thread: decode + grey copy
        with clock.part("decoding (helpers, alongside)"):
            im = load(src)
        with clock.part("grey copy (helpers, alongside)"):
            return im, small_gray(im)
    ahead = Prefetch(todo, both)
    try:
        _find(groups, measure_one, ahead.get, cache, done, n, progress, stopped, join, clock)
    finally:
        ahead.close()
    return done


def _find(groups, measure_one, load, cache, done, n, progress, stopped, join, clock):
    from engine.ai_measure import FOLLOW_MIN, follow
    i = 0
    prev = None                              # (grey picture, box, time) of the frame before
    for g in groups:
        if prev is not None and (not join or prev[2] is None or g[0]["time"] is None or
                                 (g[0]["time"] - prev[2]).total_seconds() > JOIN_GAP):
            prev = None
        for p in g:
            if stopped():
                return
            i += 1
            src = p["src"]
            p["followed"] = False
            if cache.boxes(src) is not None:
                with clock.part("from earlier"):
                    measure_one(p, None, None)                 # detected before
                done["cached"] += 1
                prev = None
            elif cache.followed(src) is not None:
                with clock.part("from earlier"):
                    measure_one(p, None, cache.followed(src))  # followed before
                p["followed"] = True
                done["cached"] += 1
                prev = None
            else:
                try:
                    with clock.part("waiting for a decoded picture"):
                        im, cur = load(src)
                except Exception as e:
                    # One picture that cannot be decoded (a damaged preview in
                    # the RAW, 2023 augustus-P8030779.ORF) must not stop the
                    # day: it is looked at as a picture without an animal.
                    logger.warning(f"AI: could not read {Path(src).name} ({e}) — skipped")
                    p.update({"box": None, "names": [], "px": 0, "sharp_pct": 0.0})
                    prev = None
                    if progress and (i % 5 == 0 or i == n):
                        progress(i, n)
                    continue
                with clock.part("following"):
                    box, score = follow(prev[0], cur, prev[1]) if prev else (None, 0.0)
                if box is not None and score >= FOLLOW_MIN:
                    with clock.part("measuring a followed frame"):
                        measure_one(p, im, box)
                    cache.put_followed(src, box)
                    p["followed"] = True
                    done["followed"] += 1
                else:
                    with clock.part("detector"):
                        measure_one(p, im, None)
                    done["detected"] += 1
                prev = (cur, p.get("box"), p.get("time"))
            if progress and (i % 5 == 0 or i == n):
                progress(i, n)


def pose_leaders(pics: list[dict], s: DaySettings | None = None) -> list[dict]:
    """Per pose, the frame whose name becomes the pose's name in decide(): the
    best-ranked frame with an animal. Only these need the AI's names — a day of
    363 pictures has ~126 poses (GH 2026-11-24: one name per pose)."""
    s = s or DaySettings()
    for p in pics:
        p.setdefault("dhash", "0" * 16)
    out = []
    for g in rd.poses(pics, s.gap, s.change):
        boxed = [p for p in g if p.get("box")]
        if boxed:
            out.append(max(boxed, key=_rank))
    return out


def still_leaders(pics: list[dict], s: DaySettings | None = None) -> list[dict]:
    """Per pose WITHOUT an animal in any frame, its sharpest frame: the one a
    plant name comes from (the whole picture)."""
    s = s or DaySettings()
    for p in pics:
        p.setdefault("dhash", "0" * 16)
    return [max(g, key=lambda p: p.get("sharp") or 0.0)
            for g in rd.poses(pics, s.gap, s.change)
            if not any(p.get("box") for p in g)]


def name_series(pics: list[dict], s: DaySettings | None = None) -> list[list[dict]]:
    """The frames to name, as series: each series = the leaders (pose_leaders)
    of consecutive poses showing the SAME animal — a pose joins the one before
    when its first frame was followed from it (find_animals). Only the first
    of each series needs the AI's names (its best-ranked leader first); the
    others get the same (GH 2026-11-24: Eempolder 126 poses ≈ 35 series)."""
    s = s or DaySettings()
    for p in pics:
        p.setdefault("dhash", "0" * 16)
    out: list[list[dict]] = []
    for g in rd.poses(pics, s.gap, s.change):
        boxed = [p for p in g if p.get("box")]
        if not boxed:
            continue
        lead = max(boxed, key=_rank)
        # followed = it has an animal, and so had the frame before (the pose before)
        if g[0].get("followed") and out:
            out[-1].append(lead)
        else:
            out.append([lead])
    return [sorted(x, key=_rank, reverse=True) for x in out]


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
        still = (None if best is not None else
                 next((p for p in g if p.get("names")), None))   # a plant: the whole picture
        name, sure = None, False
        named = best if best is not None else still
        if named is not None and named.get("names"):
            first = named["names"][0]
            sure = first["prob"] * 100 >= s.threshold
            name = first if sure else None
        biggest = max((p.get("px") or 0) for p in g)
        whole = any(p.get("box") and not cut_off(p["box"])
                    and (p.get("sharp_pct") is None or p["sharp_pct"] >= s.sharp_cut)
                    for p in g)
        kept = sum(1 for p in g if rd.is_keeper(p))
        for p in order:
            p["pose"], p["name"], p["sure"] = gi, name, sure
            p["has_animal_pose"] = best is not None
            p["plant_pose"] = still is not None
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
            elif whole and cut_off(p["box"]):
                p["verdict"], p["why"] = "purple", CUT_OFF
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
        if not p0.get("has_animal_pose") and not p0.get("plant_pose"):
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
        e["kept"] += sum(1 for p in g if not going(p))
        e["best"] = max(e["best"], n["prob"])
        if e["first"] is None:
            e["first"] = g[0].get("key")
    return {
        "pictures": len(pics), "poses": len(groups),
        "purple": sum(1 for p in pics if p.get("verdict") == "purple"),
        "keep": sum(1 for p in pics if p.get("verdict") == "keep"),
        "yours": sum(1 for p in pics if p.get("verdict") is None),
        "marked": sum(1 for p in pics if p.get("verdict") is None and going(p)),
        "marked_bytes": sum(p.get("bytes", 0) for p in pics
                            if p.get("verdict") is None and going(p)),
        "bytes": sum(p.get("bytes", 0) for p in pics if p.get("verdict") == "purple"),
        "why": dict(why), "species": species,
        "not_sure_poses": not_sure, "no_animal_poses": no_animal,
    }
