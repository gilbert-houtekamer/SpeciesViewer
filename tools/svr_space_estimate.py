"""What would the AI… suggestion free on the WHOLE master — and does it agree with you?

ONE JOB: apply the suggestion (engine.redundancy, its default settings per
species) to every species of the master and add up what would be marked Purple;
and test it against your own past choices. READ-ONLY: nothing in the master is
written. Measurements go to the shared score cache in app-data, so a later AI…
dialog on any species is instant; a second run of this tool only measures what
is new.

The agreement test: in every pose (a burst of near-identical frames) where you
made exactly ONE frame your best — a JPG, Red or Yellow — and the other frames
are candidates, does the score rank YOUR frame first? Chance alone would say
1 in the number of frames, so the tool prints both.

Usage (from the repository root):
  python tools/svr_space_estimate.py                 # the whole master (an hour or more the first time)
  python tools/svr_space_estimate.py --limit 50      # the 50 largest species only
"""
from __future__ import annotations

import argparse
import ast
import csv
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))

from engine import redundancy as rd                           # noqa: E402
from engine.picture_score import Cache, score_pictures         # noqa: E402

csv.field_size_limit(10 ** 9)


def read_master(master: Path):
    """{species: [row]} with Files as a list, and {date: star floor}."""
    by_sci, stars = defaultdict(list), defaultdict(Counter)
    with open(master / "metadata" / "active" / "data_pictures.csv",
              encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            try:
                r["Files"] = ast.literal_eval(r.get("Files") or "[]")
            except (ValueError, SyntaxError):
                r["Files"] = []
            sci = (r.get("WetSoortNaam") or "").strip().lower()
            if sci:
                by_sci[sci].append(r)
            t = rd._time(r.get("DateTimeOriginal"))
            if t:
                stars[t.date()][int(float(r.get("Rating") or 0) or 0)] += 1
    return by_sci, {d: rd.star_floor(c) for d, c in stars.items()}


def chosen(p) -> bool:
    """A frame you made the best of its burst."""
    return bool(p["jpg"]) or p["label"] in ("Red", "Yellow")


def agreement(groups):
    """(poses tested, your frame ranked first, sum of 1/frames = chance)."""
    n = hits = 0
    chance = 0.0
    for g in groups:
        mine = [p for p in g if chosen(p)]
        others = [p for p in g if rd.is_candidate(p)]
        if len(mine) != 1 or not others:
            continue
        frames = mine + others
        best = max(frames, key=lambda p: p.get("in_pose", p["sharp"]))
        n += 1
        hits += best is mine[0]
        chance += 1 / len(frames)
    return n, hits, chance


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", help="master folder (default: the configured one)")
    ap.add_argument("--limit", type=int, default=0, help="only the N largest species")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    t0 = time.time()
    master = a.master
    if not master:
        from utils.config import _config
        master = _config.get("master", "masterbiotadatabase", fallback="")
    master = Path(master.strip().strip('"'))

    by_sci, floors = read_master(master)
    order = sorted(by_sci, key=lambda s: -len(by_sci[s]))
    if a.limit:
        order = order[:a.limit]
    print(f"{len(order)} species, {sum(len(by_sci[s]) for s in order)} pictures "
          f"({time.time() - t0:.0f} s)", flush=True)

    from utils.cache_paths import app_data_dir
    out = app_data_dir() / "reports" / f"space_estimate_{datetime.now():%Y%m%d-%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    cache = Cache()
    tot = Counter()
    agree = [0, 0, 0.0]
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["species", "pictures", "days", "per day", "keepers", "candidates",
                    "purple", "GB all", "GB purple"])
        for i, sci in enumerate(order, 1):
            pics = rd.from_master_rows(by_sci[sci], master)
            rd.set_floors(pics, floors)
            st = rd.statistics(pics)
            purple = keep = 0
            gb_purple = 0
            if st["candidates"]:
                score_pictures(pics, cache)
                groups = rd.select(pics, rd.default_settings(pics))
                o = rd.outcome(pics, groups)
                purple, keep, gb_purple = o["purple"], o["keep"], o["purple_bytes"]
                n, h, c = agreement(groups)
                agree[0] += n
                agree[1] += h
                agree[2] += c
            tot["pictures"] += st["pictures"]
            tot["keepers"] += st["keepers"]
            tot["candidates"] += st["candidates"]
            tot["purple"] += purple
            tot["bytes"] += st["bytes"]
            tot["purple_bytes"] += gb_purple
            w.writerow([sci, st["pictures"], st["days"], rd.per_day_for(st["days"]),
                        st["keepers"], st["candidates"], purple,
                        round(st["bytes"] / 1e9, 2), round(gb_purple / 1e9, 2)])
            if i % 50 == 0 or i == len(order):
                fh.flush()
                el = time.time() - t0
                print(f"  {i}/{len(order)} species · {tot['pictures']} pictures · "
                      f"Purple {tot['purple']} = {tot['purple_bytes'] / 1e9:.0f} GB "
                      f"({el / 60:.0f} min)", flush=True)
    cache.close()

    n, h, c = agree
    print(f"\nAll {len(order)} species: {tot['pictures']} pictures, "
          f"{tot['bytes'] / 1e9:.0f} GB")
    print(f"  your keepers:  {tot['keepers']}")
    print(f"  candidates:    {tot['candidates']}")
    print(f"  suggest Purple: {tot['purple']} pictures = {tot['purple_bytes'] / 1e9:.0f} GB")
    if n:
        print(f"\nAgreement with your own choices: {n} bursts where you chose one frame;")
        print(f"  the score ranked YOUR frame first in {h} ({h / n:.0%}); "
              f"chance alone: {c / n:.0%}")
    print(f"Report: {out}")
    print(f"({(time.time() - t0) / 60:.0f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
