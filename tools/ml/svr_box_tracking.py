"""Experiment: MegaDetector once per pose — follow the animal through the burst.

ONE JOB: for a day whose animal boxes are all cached (MegaDetector on every
picture), pretend the detector only ran on the FIRST frame of each pose, and
follow the chosen animal box to the next frames by template matching (OpenCV,
on a reduced picture); when the match is weak the detector runs anyway. Then
compare with the real boxes:
  boxes     tracked box vs MegaDetector's (IoU >= 0.5)
  verdicts  engine.day_ai.decide on both: same verdict and same name per picture
  work      how many pictures still need the detector
On the AI… dialog's own measurements (sharp on the animal from the fine grid,
size from the box).

INVARIANT: read-only (pictures, sidecars, app caches).

  PYTHONPATH=speciesviewer python tools/ml/svr_box_tracking.py --folder "D:/.../2026-11-23"
"""
from __future__ import annotations

import argparse
import copy
import io
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "speciesviewer"))
sys.path.insert(0, str(HERE))

import svr_focus_check as fc  # noqa: E402
from engine import day_ai  # noqa: E402
from engine import redundancy as rd  # noqa: E402
from engine.ai_measure import AiCache, pick_animal, size_pct, long_side_px  # noqa: E402
from engine.picture_score import Cache, fine_in, image_bytes  # noqa: E402

RAW = (".orf", ".nef", ".cr3", ".arw", ".heic", ".jpg")
SIDE = 960           # template matching on the picture at this long side


def gray(src: Path):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes(src)))).convert("L")
    im.thumbnail((SIDE, SIDE))
    return np.asarray(im)


def follow(prev, cur, box, grow: float = 0.15, search: float = 1.0):
    """(new box, score) of `box` (fractions of prev) found again in cur."""
    H, W = prev.shape
    x0, y0, x1, y1 = box
    bw, bh = (x1 - x0) * W, (y1 - y0) * H
    gx, gy = max(6, bw * grow), max(6, bh * grow)
    tx0, ty0 = int(max(0, x0 * W - gx)), int(max(0, y0 * H - gy))
    tx1, ty1 = int(min(W, x1 * W + gx)), int(min(H, y1 * H + gy))
    tpl = prev[ty0:ty1, tx0:tx1]
    if tpl.shape[0] < 8 or tpl.shape[1] < 8 or cur.shape != prev.shape:
        return None, 0.0
    sx, sy = max(bw, 40) * search + gx, max(bh, 40) * search + gy
    rx0, ry0 = int(max(0, tx0 - sx)), int(max(0, ty0 - sy))
    rx1, ry1 = int(min(W, tx1 + sx)), int(min(H, ty1 + sy))
    region = cur[ry0:ry1, rx0:rx1]
    if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
        return None, 0.0
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    dx, dy = (rx0 + loc[0] - tx0) / W, (ry0 + loc[1] - ty0) / H
    nb = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
    nb = (max(0.0, nb[0]), max(0.0, nb[1]), min(1.0, nb[2]), min(1.0, nb[3]))
    return nb, float(score)


def iou(a, b):
    if a is None or b is None:
        return 1.0 if a is None and b is None else 0.0
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--trash", default="")
    ap.add_argument("--min-score", type=float, nargs="+", default=[0.5, 0.6, 0.7, 0.8])
    a = ap.parse_args(argv)
    folder = Path(a.folder)
    side = fc.sidecars(folder)
    srcs = [p for p in folder.iterdir() if p.suffix.lower() in RAW]
    if a.trash:
        side.update(fc.sidecars_tree(Path(a.trash)))
        srcs += [p for p in Path(a.trash).rglob("*") if p.suffix.lower() in RAW]
    sc, bx = Cache(), AiCache()
    pics = []
    for src in srcs:
        s, b = sc.get(src), bx.boxes(src)
        if s is None or b is None:
            continue
        k = src.stem.lower()
        p = {"key": k, "upn": k, "src": src, "stars": 0, "label": "", "jpg": False,
             "bytes": src.stat().st_size,
             "time": fc.parse_time(side.get(k, {}).get("DateTimeOriginal", "")),
             "found": b, "upright": bx.upright(src)}
        p.update(s)
        pics.append(p)
    pics = [p for p in pics if p["time"]]
    pics.sort(key=lambda p: (p["time"], p["key"]))
    print(f"{folder.name}: {len(pics)} pictures with cached boxes + score", flush=True)

    def measured(p, box):
        q = {"box": box, "px": 0, "sharp_pct": 0.0 if box is None else 50.0}
        if box is not None:
            q["px"] = long_side_px(box, p["upright"] or (3200, 2400), p.get("native"))
            q["sharp_in"] = fine_in(p["fine"], box)
            q["sharp_pct"] = q["sharp_in"]     # same scale for both runs: ranks + cut agree
        return q

    # the truth: MegaDetector on every picture
    truth = copy.deepcopy(pics)
    for p in truth:
        p.update(measured(p, pick_animal(p["found"], p["fine"])))
    t0 = time.time()
    grays = {p["key"]: gray(p["src"]) for p in pics}
    print(f"  reduced pictures: {(time.time() - t0) / len(pics) * 1000:.0f} ms each "
          "(the decode is needed for the detector anyway)", flush=True)
    for p in day_ai.pose_leaders(truth):
        p["names"] = [{"sci": "x", "name": "x", "prob": 0.9, "how": "exact"}]
    ref = sorted(p["sharp_in"] for p in truth if p.get("box"))   # the day as its own yardstick
    s = day_ai.DaySettings()

    for min_score in a.min_score:
        tr = copy.deepcopy(pics)
        detected, t_match = 0, 0.0
        for g in rd.poses(tr, s.gap, s.change):
            prev = None                        # (key, box) of the frame before
            for p in g:
                box = None
                if prev is not None and prev[1] is not None:
                    t = time.time()
                    nb, score = follow(grays[prev[0]], grays[p["key"]], prev[1])
                    t_match += time.time() - t
                    if nb is not None and score >= min_score:
                        box = nb
                if box is None:
                    detected += 1
                    box = pick_animal(p["found"], p["fine"])
                p.update(measured(p, box))
                prev = (p["key"], box)
        # same names on both sides: the pose leaders of the truth
        for p, q in zip(tr, truth):
            p["names"] = q.get("names", [])
        import bisect
        for x in (truth, tr):
            for p in x:
                p["sharp_pct"] = 0.0 if p.get("box") is None else (
                    100.0 * bisect.bisect_left(ref, p["sharp_in"]) / len(ref))
            day_ai.decide(x, s)
        same_box = sum(iou(p["box"], q["box"]) >= 0.5 for p, q in zip(tr, truth))
        same_verdict = sum(p["verdict"] == q["verdict"] for p, q in zip(tr, truth))
        bad = [(q["key"][-8:], q["verdict"], p["verdict"]) for p, q in zip(tr, truth)
               if p["verdict"] != q["verdict"]]
        # per pose: how sharp (real measure) are the frames each run keeps?
        worse = 0
        for g_t, g_r in zip(rd.poses(truth, s.gap, s.change), rd.poses(tr, s.gap, s.change)):
            real = {q["key"]: q.get("sharp_in") or 0 for q in g_t}
            kt = sorted(real[q["key"]] for q in g_t if q["verdict"] == "keep")
            kr = sorted(real[q["key"]] for q in g_r if q["verdict"] == "keep")
            if len(kt) != len(kr) or (kt and kr and max(kr) < 0.9 * max(kt)):
                worse += 1
        print(f"     poses where tracking keeps fewer, or a clearly less sharp best frame: {worse}"
              f" of {len(rd.poses(truth, s.gap, s.change))}")
        print(f"  match >= {min_score}: detector on {detected}/{len(pics)} pictures · "
              f"same box {same_box}/{len(pics)} · same verdict {same_verdict}/{len(pics)}"
              f" · matching {t_match / max(1, len(pics) - detected) * 1000:.0f} ms each", flush=True)
        if bad:
            print(f"     differs (real -> tracked): {bad[:10]}")


if __name__ == "__main__":
    main()
