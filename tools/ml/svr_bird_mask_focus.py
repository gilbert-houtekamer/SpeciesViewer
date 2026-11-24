"""Experiment: sharpness on the BIRD'S OWN PIXELS (GrabCut mask) vs your culling.

ONE JOB: for one reviewed day folder, cut the animal out of its AI box with
OpenCV GrabCut (no extra model), measure sharpness on the bird only, and see
whether that tells your keepers from your discards better than the tile
measures (tools/ml/svr_focus_check.py). Motivation: a blurry cattle egret in
sharp grass scored 0.91 on the tile ratio — the 67-px tiles see the grass.

Measures (all on the same decoded preview picture_score uses):
  bird      mean Laplacian energy on the bird (mask eroded: no outline), ×1e4
  bird/fr   bird against the frame's sharpest part (fine grid top-3) — same scale
  bird/bg   bird against the background around it (box + 30 % ring, outside mask)
  outline   90th pct gradient across the bird's outline / bird–background contrast
            (acutance: a sharp edge is steep whatever the colours)
  ratio     the tile ratio of svr_focus_check (for comparison)
Results are cached in a JSON beside --out, so a re-run only scores.

INVARIANT: read-only for pictures, sidecars and app caches.

  PYTHONPATH=speciesviewer python tools/ml/svr_bird_mask_focus.py --folder "D:/.../2026-11-23"
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "speciesviewer"))
sys.path.insert(0, str(HERE))

import svr_focus_check as fc  # noqa: E402
from engine.ai_measure import AiCache, animal_box  # noqa: E402
from engine.picture_score import Cache, fine_in, frame_top, image_bytes  # noqa: E402

GC_SIDE = 400            # GrabCut runs on the crop scaled to this long side


def energy(gray):
    f = gray.astype(np.float32) / 255.0
    lap = cv2.Laplacian(cv2.GaussianBlur(f, (0, 0), 1.0), cv2.CV_32F, ksize=3)
    return lap * lap


def bird_mask(crop_bgr, inner):
    """GrabCut with the AI box (inner, in crop pixels) as the rectangle → bool mask."""
    h, w = crop_bgr.shape[:2]
    s = min(1.0, GC_SIDE / max(h, w))
    small = cv2.resize(crop_bgr, (max(1, int(w * s)), max(1, int(h * s))),
                       interpolation=cv2.INTER_AREA)
    x0, y0, x1, y1 = [int(v * s) for v in inner]
    rect = (max(1, x0), max(1, y0), max(2, x1 - x0), max(2, y1 - y0))
    rect = (rect[0], rect[1], min(rect[2], small.shape[1] - rect[0] - 1),
            min(rect[3], small.shape[0] - rect[1] - 1))
    m = np.zeros(small.shape[:2], np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(small, m, rect, bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
    fg = ((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD)).astype(np.uint8)
    return cv2.resize(fg, (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)


def measure_one(src: Path, box, fine) -> dict | None:
    from PIL import Image, ImageOps
    data = image_bytes(src)
    if not data:
        return None
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    W, H = im.size
    x0, y0, x1, y1 = box[0] * W, box[1] * H, box[2] * W, box[3] * H
    pad = 0.3 * max(x1 - x0, y1 - y0)
    cx0, cy0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
    cx1, cy1 = int(min(W, x1 + pad)), int(min(H, y1 + pad))
    crop = cv2.cvtColor(np.asarray(im.crop((cx0, cy0, cx1, cy1))), cv2.COLOR_RGB2BGR)
    inner = (x0 - cx0, y0 - cy0, x1 - cx0, y1 - cy0)
    mask = bird_mask(crop, inner)
    if mask.sum() < 50:
        return {"mask": 0}
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    e = energy(gray)
    k = max(3, int(0.03 * max(crop.shape[:2])) | 1)
    ker = np.ones((k, k), np.uint8)
    core = cv2.erode(mask.astype(np.uint8), ker).astype(bool)
    if core.sum() < 20:
        core = mask
    ring = ~cv2.dilate(mask.astype(np.uint8), ker).astype(bool)
    band = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & \
        ~cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.0)
    grad = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    contrast = abs(float(g[core].mean()) - float(g[ring].mean())) if ring.any() else 0.0
    bird = float(e[core].mean()) * 1e4
    bg = float(e[ring].mean()) * 1e4 if ring.any() else 0.0
    best = frame_top(fine) or 0.0
    return {"mask": int(mask.sum()), "share": float(mask.sum() / mask.size),
            "bird": bird, "bird/fr": bird / best if best else 0.0,
            "bird/bg": bird / bg if bg else 0.0,
            "outline": float(np.percentile(grad[band], 90)) / (contrast + 8.0) if band.any() else 0.0}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--out", default=str(HERE / "results" / "bird_mask_focus.json"))
    ap.add_argument("--gap", type=float, default=3.0)
    a = ap.parse_args(argv)
    folder = Path(a.folder)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    side = fc.sidecars(folder)
    sc, bx = Cache(), AiCache()
    rows, t0 = [], time.time()
    srcs = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".orf", ".nef", ".cr3", ".arw"))
    for i, src in enumerate(srcs, 1):
        k = src.stem.lower()
        s, b = sc.get(src), bx.boxes(src)
        box = animal_box(b) if b else None
        if s is None or box is None:
            continue
        if k not in done:
            done[k] = measure_one(src, box, s["fine"]) or {"mask": 0}
        r = dict(done[k])
        r.update(key=k, ratio=fine_in(s["fine"], box) / (frame_top(s["fine"]) or 1))
        r["label"] = str(side.get(k, {}).get("Label", "") or "").strip().capitalize()
        when = side.get(k, {}).get("DateTimeOriginal", "")
        r["time"] = datetime.strptime(when[:19], "%Y:%m:%d %H:%M:%S") if when else None
        rows.append(r)
        if i % 50 == 0:
            print(f"  {i}/{len(srcs)} · {time.time() - t0:.0f} s", flush=True)
            out.write_text(json.dumps(done), encoding="utf-8")
    out.write_text(json.dumps(done), encoding="utf-8")
    rows = [r for r in rows if r.get("mask")]
    keep = [r for r in rows if r["label"] not in fc.GOING]
    gone = [r for r in rows if r["label"] in fc.GOING]
    print(f"{len(rows)} pictures with a bird mask: you kept {len(keep)}, discarded {len(gone)}"
          f" · {time.time() - t0:.0f} s")
    rows.sort(key=lambda r: (r["time"] or datetime.min, r["key"]))
    pose, last = 0, None
    for r in rows:
        if last is None or r["time"] is None or (r["time"] - last).total_seconds() > a.gap:
            pose += 1
        r["pose"], last = pose, r["time"]
    ms = ("ratio", "bird", "bird/fr", "bird/bg", "outline")
    print(f"\n{'measure':8} {'AUC day':>8} {'pairs in pose':>14}")
    for m in ms:
        ok = n = 0
        for p in set(r["pose"] for r in rows):
            g = [r for r in rows if r["pose"] == p]
            for x in g:
                for y in g:
                    if x["label"] not in fc.GOING and y["label"] in fc.GOING:
                        n += 1
                        ok += (x[m] > y[m]) + 0.5 * (x[m] == y[m])
        print(f"{m:8} {fc.auc([r[m] for r in keep], [r[m] for r in gone]):8.2f} "
              f"{ok / n if n else float('nan'):14.2f}")
    print("\nyour examples:")
    for k in ("nb233399", "nb233519", "nb233567", "nb233351", "nb233576"):
        for r in rows:
            if r["key"].endswith(k):
                print(f"  {k} {r['label'] or '-':7} " + "  ".join(f"{m} {r[m]:.2f}" for m in ms))
    print("\nsafe cuts (pictures below · of which you kept):")
    for m in ("bird/fr", "bird/bg", "outline"):
        vals = sorted(r[m] for r in keep)
        for q in (0, 2, 5):
            cut = vals[q] if q < len(vals) else 0          # just above the q-th keeper
            lo = [r for r in rows if r[m] < cut + 1e-9]
            print(f"  {m:8} < {cut:6.3f}: {len(lo):3d} · kept {sum(r['label'] not in fc.GOING for r in lo)}")


if __name__ == "__main__":
    main()
