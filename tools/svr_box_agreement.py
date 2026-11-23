"""EXPERIMENT — does sharpness INSIDE the animal's box agree with you more often?

ONE JOB: for every burst where you made exactly one frame your best (a JPG, Red
or Yellow) and the other frames are candidates, rank the frames
  A  as the AI… suggestion does today (the pose's shared spot, pixel level),
  B  on each frame's own animal box (MegaDetector, tools/ml/svr_bird_box.py),
and count how often YOUR frame comes first, next to chance (1 / frames).
Also: how many pictures got an animal box at all. READ-ONLY; uses only the
caches in app-data (scores + boxes), so it needs no decode and runs in minutes.

  python tools/svr_box_agreement.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import redundancy as rd                      # noqa: E402
from engine.picture_score import Cache, fine_in           # noqa: E402
from svr_space_estimate import chosen, read_master        # noqa: E402

MIN_CONF = 0.2
PAD = 0.03          # grow the box a little: the bird's edge is often its sharpest part


def load_boxes() -> dict:
    from utils.cache_paths import app_data_dir
    p = app_data_dir() / "scores" / "bird_boxes.sqlite"
    if not p.exists():
        return {}
    con = sqlite3.connect(str(p))
    return {path: json.loads(b) for path, b in con.execute("select path, boxes from boxes")}


def animal_box(boxes):
    """The most confident animal box, padded, or None."""
    best = max((b for b in boxes or [] if b["cat"] == "animal" and b["conf"] >= MIN_CONF),
               key=lambda b: b["conf"], default=None)
    if best is None:
        return None
    x0, y0, x1, y1 = best["box"]
    return (max(0, x0 - PAD), max(0, y0 - PAD), min(1, x1 + PAD), min(1, y1 + PAD))


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    t0 = time.time()
    from utils.config import _config
    master = Path(_config.get("master", "masterbiotadatabase", fallback="").strip().strip('"'))
    by_sci, floors = read_master(master)
    boxes = load_boxes()
    cache = Cache()
    n = hit_a = hit_b = n_b = hit_a_b = 0
    chance = chance_b = 0.0
    pics_total = pics_boxed = pics_checked = 0
    for sci, rows in by_sci.items():
        pics = rd.from_master_rows(rows, master)
        rd.set_floors(pics, floors)
        for p in pics:
            sc = cache.get(p["src"])
            p.update(sc or {"sharp": 0.0, "dhash": "0" * 16, "tiles": None, "fine": None})
            b = boxes.get(str(p["src"]))
            if b is not None:
                pics_checked += 1
            p["box"] = animal_box(b)
            pics_boxed += p["box"] is not None
        pics_total += len(pics)
        if not any(rd.is_candidate(p) for p in pics):
            continue
        groups = rd.poses(pics, rd.Settings().gap, rd.Settings().change)
        for g in groups:
            mine = [p for p in g if chosen(p)]
            others = [p for p in g if rd.is_candidate(p)]
            if len(mine) != 1 or not others:
                continue
            frames = mine + others
            rd.pose_measure(frames)
            n += 1
            chance += 1 / len(frames)
            first_a = max(frames, key=lambda p: p["in_pose"]) is mine[0]
            hit_a += first_a
            # B only where EVERY frame of the burst has a box and a fine grid
            if all(p["box"] is not None and p.get("fine") is not None for p in frames):
                vb = {id(p): fine_in(p["fine"], p["box"]) for p in frames}
                n_b += 1
                chance_b += 1 / len(frames)
                hit_b += max(frames, key=lambda p: vb[id(p)]) is mine[0]
                hit_a_b += first_a
    cache.close()
    print(f"{pics_total} pictures; boxes looked for in {pics_checked}, "
          f"an animal found in {pics_boxed} ({pics_boxed / max(1, pics_checked):.0%})")
    print(f"\nBursts where you chose one frame: {n}")
    print(f"  A today's score ranks YOUR frame first: {hit_a / max(1, n):.0%}  "
          f"(chance {chance / max(1, n):.0%})")
    print(f"\nOf those, bursts where every frame has an animal box: {n_b}")
    print(f"  A today's score:        {hit_a_b / max(1, n_b):.0%}")
    print(f"  B inside the box:       {hit_b / max(1, n_b):.0%}")
    print(f"  chance:                 {chance_b / max(1, n_b):.0%}")
    print(f"({time.time() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
