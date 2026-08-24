"""Recover the crop of an old website JPG, and propose a better one from the RAW's
embedded full-size JPEG.

REPORT ONLY. Writes a review directory and a CSV. Touches nothing in the master.

WHY
---
Thousands of species pages still show an old render — often 800x533, sometimes an
UPSCALED 2000x1500 — made years ago in Capture NX or PhotoLab. The Nikons (D90
through D500) embed a FULL-SIZE JPEG in every NEF (D7100: 6000x4000), so a much
better picture is already sitting in the RAW, for free, no PhotoLab re-render.

The catch: the CROP IS NOT RECORDED ANYWHERE. Those pictures were cropped in an
external tool years ago. Lifting the embed without the crop just gives a bigger
UNCROPPED frame — not the picture on the page. So the crop has to be RECOVERED.

HOW THE RECOVERY WORKS
----------------------
A crop followed by a resize is a SIMILARITY transform: scale + translation, no shear
and no rotation. So SIFT features + RANSAC (cv2.estimateAffinePartial2D) pin it down,
and the rectangle falls out of the estimated transform. Verified on
2025 Gambia-N3104497: 249/262 inliers, and the recovered rectangle re-cut correlates
0.905 with the actual render.

TWO GUARDRAILS, BOTH LEARNED THE HARD WAY
-----------------------------------------
1. CORRELATION FLOOR. A wrongly-cropped bird published to the website is a silent,
   public failure. Below the floor we do not propose anything.

2. THE 110% RULE (the user's). Only propose a replacement when the crop taken from
   the embed is at least 1.1x the published image on the long side. PhotoLab renders
   to a 2000x1500 TARGET and will happily UPSCALE a small crop to reach it — so the
   published pixel count is not evidence of real detail, and a proposal that is
   merely the same size is not worth the churn.
   (Conservative on purpose: a real 1900px beats a fake upscaled 2000px, but we do
   not try to be clever about that here.)

3. WHAT THIS CANNOT SEE: RETOUCHING. The user removed branches in Capture NX. A
   re-render from the embed BRINGS THE BRANCH BACK, and correlation barely notices a
   cloned-out twig. That is why every proposal is written out as a side-by-side JPG
   for a human to look at. THE REVIEW DIRECTORY IS THE SAFETY MECHANISM, not a
   formality.

Usage:
    python tools/svr_crop_recover.py --limit 25            # first taste
    python tools/svr_crop_recover.py --limit 500 --out D:/croprev
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

CORRELATION_FLOOR = 0.75      # below this we do not trust the recovered rectangle
MIN_GAIN = 1.10               # the user's 110%-on-the-long-side rule
FULL_EMBED_FRACTION = 0.90    # an embed is "full size" at >=90% of the sensor's long edge
WEB_TARGET_LONG = 2000        # the modern render target; we never upscale past the crop


def _long(size: str) -> int:
    try:
        return max(int(x) for x in (size or "").lower().split("x"))
    except Exception:
        return 0


def candidates(con) -> list[dict]:
    """Master pictures that have a published JPG and a RAW. The real filter (is the
    embed full size? is the crop worth it?) needs the pixels, so it happens later —
    the published SIZE tells us nothing, because PhotoLab upscales to its target."""
    rows = con.execute(
        'SELECT p."UniquePictureName" u, p."WetSoortNaam" sci, p."CameraModel" cam,'
        '       p."BestJPG" bj, p."JPGSize" js, p."TargetPath" tp'
        '  FROM pictures p WHERE TRIM(COALESCE(p."BestJPG",\'\')) <> \'\'').fetchall()
    out = []
    for r in rows:
        files = {(f["ext"] or "").lower(): f["w"] for f in con.execute(
            'SELECT lower("WindowsExtension") ext, "WindowsFileName" w, "ImageSize" s'
            '  FROM files WHERE "UniquePictureName"=?', (r["u"],))}
        raw = files.get("nef") or files.get("orf")
        jpg = None
        for f in con.execute(
                'SELECT "WindowsFileName" w, "File:FileName" fn FROM files '
                ' WHERE "UniquePictureName"=?', (r["u"],)):
            if f["fn"] == r["bj"]:
                jpg = f["w"]
        if not raw or not jpg:
            continue
        if not os.path.isfile(raw) or not os.path.isfile(jpg):
            continue
        out.append({"upn": r["u"], "sci": r["sci"] or "", "cam": r["cam"] or "",
                    "raw": raw, "jpg": jpg, "published": r["js"] or "",
                    "target_path": r["tp"] or ""})
    return out


def _gray(im: Image.Image, width: int = 1000):
    a = cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2GRAY)
    s = width / a.shape[1]
    return cv2.resize(a, (int(a.shape[1] * s), int(a.shape[0] * s))), s


def recover_crop(render: Image.Image, embed: Image.Image):
    """Where does `render` sit inside `embed`? Returns (rect_px, correlation) with
    rect in EMBED pixels, or (None, 0.0) when it cannot be established."""
    r_small, r_scale = _gray(render)
    h_small, h_scale = _gray(embed)
    sift = cv2.SIFT_create()
    kp1, des1 = sift.detectAndCompute(r_small, None)
    kp2, des2 = sift.detectAndCompute(h_small, None)
    if des1 is None or des2 is None or len(kp1) < 10 or len(kp2) < 10:
        return None, 0.0
    good = [m for m, n in cv2.BFMatcher().knnMatch(des1, des2, k=2)
            if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        return None, 0.0
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    M, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                             ransacReprojThreshold=3)
    if M is None or inliers is None or int(inliers.sum()) < 10:
        return None, 0.0

    h, w = r_small.shape
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    m = cv2.transform(corners, M).reshape(-1, 2) / h_scale
    x0, y0 = float(m[:, 0].min()), float(m[:, 1].min())
    x1, y1 = float(m[:, 0].max()), float(m[:, 1].max())

    ew, eh = embed.size
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(float(ew), x1), min(float(eh), y1)
    if x1 - x0 < 50 or y1 - y0 < 50:
        return None, 0.0

    # Prove it: cut the rectangle, scale it to the render, correlate.
    cut = cv2.cvtColor(np.array(embed.convert("RGB")), cv2.COLOR_RGB2GRAY)
    cut = cut[int(y0):int(y1), int(x0):int(x1)]
    rend = cv2.cvtColor(np.array(render.convert("RGB")), cv2.COLOR_RGB2GRAY)
    if cut.size == 0:
        return None, 0.0
    cut = cv2.resize(cut, (rend.shape[1], rend.shape[0]))
    corr = float(cv2.matchTemplate(cut, rend, cv2.TM_CCOEFF_NORMED).max())
    return (x0, y0, x1, y1), corr


def side_by_side(current: Image.Image, suggested: Image.Image, caption: str) -> Image.Image:
    """Both at ACTUAL SIZE, side by side.

    Scaling them to a common height would hide the very thing being judged — an
    800x533 page image next to a 2000x1332 one LOOKS the same when both are squeezed
    to 700px tall. At native pixels the difference is the point: the proposal is
    simply bigger, and you can see whether that size is carrying real detail."""
    bar = 34
    w = current.width + suggested.width + 24
    h = max(current.height, suggested.height) + bar
    out = Image.new("RGB", (w, h), "black")
    out.paste(current, (0, bar))
    out.paste(suggested, (current.width + 24, bar))
    d = ImageDraw.Draw(out)
    d.text((6, 8), f"LEFT = on the website now (actual size)   |   "
                   f"RIGHT = proposed from the RAW (actual size)   |   {caption}",
           fill="white")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="crop_review")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--min-gain", type=float, default=MIN_GAIN)
    ap.add_argument("--floor", type=float, default=CORRELATION_FLOOR)
    ap.add_argument("--target", type=int, default=WEB_TARGET_LONG,
                    help="long side of the proposed JPG (default 2000 — today's web "
                         "convention, not a limit of the method. The crops often hold "
                         "2300-5500 real pixels, so a 3000 target is one re-run away; "
                         "we never upscale past what the crop actually contains)")
    ap.add_argument("--all", action="store_true",
                    help="walk the candidates in order instead of sampling across them")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))
    from db.master_db import db_path
    from gui.preview_extract import preview_jpeg_bytes

    con = sqlite3.connect(str(db_path()))
    con.row_factory = sqlite3.Row
    cands = candidates(con)
    print(f"master pictures with both a published JPG and a RAW: {len(cands):,}")

    # SAMPLE ACROSS THE LIBRARY, don't walk it from the front. The master is ordered
    # by taxonomy, so the first 25 candidates were 25 damselflies — a fine test of the
    # matcher and a useless test of the IDEA. Striding gives birds, mammals, insects
    # and plants, shot on different bodies, in one small batch.
    if not args.all and len(cands) > args.limit:
        stride = max(1, len(cands) // (args.limit * 3))   # oversample: many get rejected
        cands = cands[::stride]
        print(f"sampling every {stride}th to span the whole library "
              f"({len(cands):,} to try)")
    print()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    proposed = examined = 0

    for c in cands:
        if proposed >= args.limit:
            break
        examined += 1
        try:
            data = preview_jpeg_bytes(Path(c["raw"]))
            if not data:
                continue
            embed = Image.open(BytesIO(data))
            render = Image.open(c["jpg"])
        except Exception:
            continue

        # Is the embed full-size? (The OM-1's is only 3200px, i.e. 61% of its sensor —
        # cropping THAT is worse than what is already published. Nikon only.)
        rawsize = con.execute(
            'SELECT "ImageSize" s FROM files WHERE "UniquePictureName"=? '
            '  AND lower("WindowsExtension") IN ("nef","orf")', (c["upn"],)).fetchone()
        sensor_long = _long(rawsize["s"] if rawsize else "")
        if not sensor_long or max(embed.size) < sensor_long * FULL_EMBED_FRACTION:
            continue                       # no full-size render hiding in this RAW

        rect, corr = recover_crop(render, embed)
        if rect is None or corr < args.floor:
            continue
        x0, y0, x1, y1 = rect
        crop_long = max(x1 - x0, y1 - y0)
        gain = crop_long / max(1, max(render.size))
        if gain < args.min_gain:
            continue                       # the 110% rule — not worth the churn

        # Build the proposal: cut the crop, and NEVER upscale past what it holds.
        # (PhotoLab does upscale to reach its target — which is exactly why the
        # published pixel count is not evidence of detail. We refuse to do the same.)
        cut = embed.crop((int(x0), int(y0), int(x1), int(y1)))
        if max(cut.size) > args.target:
            s = args.target / max(cut.size)
            cut = cut.resize((int(cut.width * s), int(cut.height * s)),
                             Image.Resampling.LANCZOS)

        proposed += 1
        stem = f"{proposed:03d}_{c['sci'].replace(' ', '_')}_{c['upn']}"
        # Two different numbers, and conflating them would flatter the result:
        # DELIVERED is what the page actually gains (we cap output at the web target);
        # HEADROOM is how much real detail the crop still holds beyond that.
        delivered = max(cut.size) / max(1, max(render.size))
        cap = (f"{c['cam']}  now {render.size[0]}x{render.size[1]}  ->  "
               f"proposed {cut.size[0]}x{cut.size[1]}   "
               f"delivered x{delivered:.1f}  (crop holds {crop_long:.0f}px, "
               f"headroom x{gain:.1f})  corr {corr:.3f}")
        side_by_side(render.convert("RGB"), cut.convert("RGB"), cap).save(
            out_dir / f"{stem}__compare.jpg", quality=88)
        cut.convert("RGB").save(out_dir / f"{stem}__proposed.jpg", quality=92)

        rows.append({
            "Species": c["sci"], "UPN": c["upn"], "Camera": c["cam"],
            "PublishedSize": f"{render.size[0]}x{render.size[1]}",
            "ProposedSize": f"{cut.size[0]}x{cut.size[1]}",
            "DeliveredGain": f"{delivered:.2f}",
            "CropNativePx": f"{crop_long:.0f}",
            "HeadroomGain": f"{gain:.2f}",
            "Correlation": f"{corr:.3f}",
            "CropPercentOfFrame": f"{(x1-x0)/embed.size[0]*100:.1f}",
            "EmbedSize": f"{embed.size[0]}x{embed.size[1]}",
            "SensorLong": sensor_long,
            "PublishedFile": c["jpg"],
            "Raw": c["raw"],
        })
        print(f"  {proposed:3d}. {c['sci'][:26]:28} {cap}")

    with open(out_dir / "crop_recovery.csv", "w", newline="", encoding="utf-8") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
            w.writeheader()
            w.writerows(rows)

    print(f"\nexamined {examined:,} pictures, proposed {proposed}")
    print(f"wrote {out_dir}/  (one *__compare.jpg per picture + crop_recovery.csv)")
    print("REPORT ONLY - the master was not touched.")
    print("\nLOOK AT THE COMPARE IMAGES. Correlation cannot see a branch you cloned out "
          "in Capture NX; a re-render brings it back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
