"""Experiment: which "sharp on the animal" measure matches YOUR culling of a day?

ONE JOB: for one day folder, take the AI's animal box (cached by the AI…
dialog) and the fine sharpness grid (cached by picture_score), compute a few
candidate focus measures, and compare them with your final colours (read from
the sidecars) and with what the AI… Accept set (app-data day_ai_accepts.json).

Measures (fine grid = 48×36 tiles at the picture's own preview size):
  now      top-3 tiles inside the box (what SV uses today)
  ratio    now / top-3 tiles of the whole frame   (~1: the sharpest part is ON the animal)
  mid      top-3 tiles inside the middle half of the box (less background)
  midratio mid / top-3 tiles of the whole frame
Reported: AUC "keeper above discard" over the day, and within-pose pair
accuracy (your keeper vs your discard of the same pose, by time gap).

INVARIANT: read-only — caches and sidecars are only read.

  PYTHONPATH=speciesviewer python tools/ml/svr_focus_check.py --folder "D:/.../2026-11-23"
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "speciesviewer"))

from engine.ai_measure import AiCache, animal_box  # noqa: E402
from engine.picture_score import Cache, fine_in  # noqa: E402
from utils.cache_paths import app_data_dir  # noqa: E402

GOING = ("Blue", "Purple")


def frame_top(fine, top=3):
    a = np.asarray(fine, np.float32).ravel()
    a.sort()
    return float(a[-top:].mean())


def middle(box, keep=0.5):
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * (1 - keep) / 2, (y1 - y0) * (1 - keep) / 2
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def auc(pos, neg):
    """P(a keeper scores above a discard); ties count half."""
    if not pos or not neg:
        return float("nan")
    p, n = np.array(pos), np.array(neg)
    gt = (p[:, None] > n[None, :]).sum() + 0.5 * (p[:, None] == n[None, :]).sum()
    return gt / (len(p) * len(n))


def sidecars(folder: Path) -> dict:
    out = subprocess.run(["exiftool", "-j", "-q", "-Label", "-Rating", "-DateTimeOriginal",
                          "-ext", "xmp", str(folder)], capture_output=True, text=True,
                         encoding="utf-8")
    rows = json.loads(out.stdout or "[]")
    return {Path(r["SourceFile"]).stem.lower(): r for r in rows}


def parse_time(s):
    """A sidecar date: with or without seconds, any time zone after it."""
    for fmt, n in (("%Y:%m:%d %H:%M:%S", 19), ("%Y:%m:%d %H:%M", 16)):
        try:
            return datetime.strptime((s or "")[:n], fmt)
        except ValueError:
            pass
    return None


def sidecars_tree(folder: Path) -> dict:
    """sidecars() for a folder and all folders below it (a day's trash)."""
    out = subprocess.run(["exiftool", "-r", "-j", "-q", "-Label", "-Rating", "-DateTimeOriginal",
                          "-ext", "xmp", str(folder)], capture_output=True, text=True,
                         encoding="utf-8")
    rows = json.loads(out.stdout or "[]")
    return {Path(r["SourceFile"]).stem.lower(): r for r in rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--gap", type=float, default=3.0, help="seconds between poses")
    a = ap.parse_args(argv)
    folder = Path(a.folder)
    accepts = json.loads((app_data_dir() / "scores" / "day_ai_accepts.json")
                         .read_text(encoding="utf-8"))
    ai_set = {}
    for e in accepts:
        if Path(e["folder"]) == folder:
            ai_set.update({k.lower(): v[1] for k, v in e["colours"].items()})
    side = sidecars(folder)
    sc, bx = Cache(), AiCache()
    rows = []
    for src in sorted(folder.iterdir()):
        if src.suffix.lower() not in (".orf", ".nef", ".jpg", ".cr3", ".arw"):
            continue
        k = src.stem.lower()
        s = sc.get(src)
        boxes = bx.boxes(src)
        r = side.get(k, {})
        lab = str(r.get("Label", "") or "").strip().capitalize()
        when = r.get("DateTimeOriginal", "")
        row = {"key": k, "label": lab, "ai": ai_set.get(k, ""),
               "time": parse_time(when),
               "box": animal_box(boxes) if boxes else None}
        if s is None or row["box"] is None:
            rows.append(row)
            continue
        f, b = s["fine"], row["box"]
        best = frame_top(f)
        row["now"] = fine_in(f, b)
        row["mid"] = fine_in(f, middle(b))
        row["ratio"] = row["now"] / best if best else 0
        row["midratio"] = row["mid"] / best if best else 0
        rows.append(row)

    measured = [r for r in rows if "now" in r]
    print(f"{folder.name}: {len(rows)} pictures, {len(measured)} with an animal + score")
    keep = [r for r in measured if r["label"] not in GOING]
    gone = [r for r in measured if r["label"] in GOING]
    print(f"your final colours: {len(keep)} stay, {len(gone)} Blue/Purple")
    agree = sum((r["ai"] == "Purple") == (r["label"] in GOING) for r in rows if r["ai"])
    print(f"AI Accept vs you: same verdict on {agree} of {sum(1 for r in rows if r['ai'])}")
    flips = [r for r in rows if r["ai"] == "Green" and r["label"] in GOING]
    back = [r for r in rows if r["ai"] == "Purple" and r["label"] not in GOING]
    print(f"  AI kept, you discarded: {len(flips)} · AI discarded, you kept: {len(back)}")

    # poses = time runs
    measured.sort(key=lambda r: (r["time"] or datetime.min, r["key"]))
    pose, last = 0, None
    for r in measured:
        if last is None or r["time"] is None or (r["time"] - last).total_seconds() > a.gap:
            pose += 1
        r["pose"], last = pose, r["time"]

    print(f"\n{'measure':9} {'AUC day':>8} {'pairs in pose':>14} {'n pairs':>8}"
          f" {'AUC (AI kept ones)':>19}")
    ai_kept = [r for r in measured if r["ai"] == "Green"]
    for m in ("now", "ratio", "mid", "midratio"):
        a_day = auc([r[m] for r in keep], [r[m] for r in gone])
        ok = n = 0
        for p in set(r["pose"] for r in measured):
            g = [r for r in measured if r["pose"] == p]
            for x in g:
                for y in g:
                    if x["label"] not in GOING and y["label"] in GOING:
                        n += 1
                        ok += (x[m] > y[m]) + 0.5 * (x[m] == y[m])
        a_kept = auc([r[m] for r in ai_kept if r["label"] not in GOING],
                     [r[m] for r in ai_kept if r["label"] in GOING])
        print(f"{m:9} {a_day:8.2f} {ok / n if n else float('nan'):14.2f} {n:8d} {a_kept:19.2f}")

    print("\nAI kept, you discarded (lowest ratio first):")
    for r in sorted((r for r in flips if "now" in r), key=lambda r: r["ratio"])[:25]:
        print(f"  {r['key']}  now {r['now']:7.0f}  ratio {r['ratio']:.2f}  "
              f"mid {r['mid']:7.0f}  midratio {r['midratio']:.2f}")
    print("\nfor reference, the ones you kept (ratio quartiles):",
          np.round(np.percentile([r["ratio"] for r in keep], [10, 25, 50, 75]), 2),
          " discarded:", np.round(np.percentile([r["ratio"] for r in gone], [10, 25, 50, 75]), 2))


if __name__ == "__main__":
    main()
