"""EXPERIMENT — can the AI name TREES (plants) in pictures with no animal?

ONE JOB: for every pose (engine.redundancy.poses) of a trip folder, take the
sharpest frame and ask MegaDetector for an animal (cached). Poses WITHOUT an
animal: BioCLIP 2 looks at the WHOLE picture and names it two ways —
  plants   only among iNaturalist's plants of the country (engine.inat_lists "plants")
  open     everything BioCLIP knows (what kingdom does it think this is?)
— and the name YOU gave (sidecar dc:subject) is shown beside it when there is one.
Answers: how sure is it on trees vs on plain landscapes (the threshold), and
is it right where you named the plant.
READ-ONLY for pictures and sidecars; writes app-data
reports/plant_names_<region>_<time>.csv and .html (thumbnails, to judge by eye).

  python tools/ml/svr_plant_names.py --region MG --folder "D:/CurrentPictures/2026/2026 Madagascar"
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

SV = str(Path(__file__).resolve().parents[2] / "speciesviewer")
sys.path.insert(0, SV)
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--folder", required=True)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    from PIL import Image, ImageOps
    from engine import ai_measure as am, ai_models, inat_lists
    from engine import redundancy as rd
    from engine.picture_score import Cache, image_bytes, score_pictures
    from utils.cache_paths import app_data_dir
    from utils.config import _config
    import svr_bioclip_trip as bt
    import svr_inat_names as tn

    t0 = time.time()
    region = a.region.upper()
    folder = Path(a.folder)
    root = Path(_config.get("master", "masterbiotadatabase", fallback="").strip().strip('"'))
    by_name, _klass = tn.collection(root)
    mine = tn.folder_names(folder, by_name)
    pics = bt.folder_pictures(folder)
    print(f"{len(pics)} pictures; sharpness…", flush=True)
    cache = Cache()
    score_pictures(pics, cache)
    cache.close()
    groups = rd.poses(pics, rd.Settings().gap, rd.Settings().change)
    print(f"{len(groups)} poses ({time.time() - t0:.0f} s)", flush=True)

    inat_lists.fetch(region, "plants", progress=lambda s: print("  " + s, flush=True))
    plants = am._as_bioclip_inat(region, ["plants"])
    clip = ai_models.bioclip()
    kingdom = {s: str(t[0]).lower() for s, t in zip(clip.sci, clip.taxa) if t}
    print(f"plants to choose among: {len(plants)} ({time.time() - t0:.0f} s)", flush=True)

    aic = am.AiCache()
    rows, thumbs = [], {}
    n_animal = 0
    for i, g in enumerate(groups):
        rep = max(g, key=lambda p: p.get("sharp") or 0)
        src = Path(rep["src"])
        held = {}

        def picture():
            if "im" not in held:
                data = image_bytes(src)
                held["im"] = (ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
                              if data else None)
            return held["im"]
        boxes = aic.boxes(src)
        if boxes is None:
            if picture() is None:
                continue
            small = picture().copy()
            small.thumbnail((am.DETECT_EDGE, am.DETECT_EDGE))
            boxes = ai_models.detector().boxes(small)
            aic.put_boxes(src, boxes)
        if am.animal_box(boxes) is not None:
            n_animal += 1
            continue
        vec = aic.vector(src, None)
        if vec is None:
            if picture() is None:
                continue
            vec = clip.picture_vectors([am.crop_image(picture(), None)])[0]
            aic.put_vector(src, None, vec)
        pl = clip.top(vec, k=3, allowed=plants)
        op = clip.top(vec, k=1)
        yours = Counter(mine[p["upn"]] for p in g if mine.get(p["upn"])).most_common(1)
        row = {"pose": i, "picture": src.name, "day": src.parent.name, "frames": len(g),
               "yours": yours[0][0] if yours else "",
               "plant1": pl[0][0] if pl else "", "plant1_name": pl[0][1] if pl else "",
               "plant1_prob": round(pl[0][2], 3) if pl else 0,
               "plant2": pl[1][0] if len(pl) > 1 else "", "plant3": pl[2][0] if len(pl) > 2 else "",
               "open1": op[0][0] if op else "", "open1_prob": round(op[0][2], 3) if op else 0,
               "open1_kingdom": kingdom.get(op[0][0], "") if op else "",
               "kept": "1" if am.plant_names(vec, region, None, groups=["plants"])[0] else ""}
        rows.append(row)
        if picture() is not None:
            t = picture().copy()
            t.thumbnail((360, 360))
            b = io.BytesIO()
            t.save(b, "JPEG", quality=75)
            thumbs[i] = base64.b64encode(b.getvalue()).decode()
        if len(rows) % 25 == 0:
            print(f"  {len(rows)} poses without an animal ({i + 1}/{len(groups)}, "
                  f"{time.time() - t0:.0f} s)", flush=True)
    aic.close()

    out = app_data_dir() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = out / f"plant_names_{region}_{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["pose"])
        w.writeheader()
        w.writerows(rows)
    page = out / f"plant_names_{region}_{stamp}.html"
    cells = []
    for r in sorted(rows, key=lambda r: -r["plant1_prob"]):
        img = thumbs.get(r["pose"])
        cells.append(
            "<div class=c>" + (f"<img src='data:image/jpeg;base64,{img}'>" if img else "")
            + f"<b>{'' if r['kept'] else '<s>'}{r['plant1_prob']:.0%} {html.escape(r['plant1'])}"
            + f"{'' if r['kept'] else '</s> (left out)'}</b><br>"
            + f"{html.escape(r['plant1_name'])}<br>"
            + f"<small>then {html.escape(r['plant2'])}, {html.escape(r['plant3'])}<br>"
            + f"open: {html.escape(r['open1'])} {r['open1_prob']:.0%} ({r['open1_kingdom']})<br>"
            + f"{html.escape(r['day'])} {html.escape(r['picture'])} · {r['frames']} frames"
            + (f"<br>yours: <b>{html.escape(r['yours'])}</b>" if r["yours"] else "")
            + "</small></div>")
    page.write_text("<html><meta charset=utf-8><style>body{font:13px sans-serif}"
                    ".c{display:inline-block;width:370px;vertical-align:top;margin:6px}"
                    "img{display:block;max-width:360px}</style>"
                    f"<h3>{len(rows)} poses without an animal — whole picture, "
                    f"{region} plants, most sure first</h3>" + "".join(cells) + "</html>",
                    encoding="utf-8")

    probs = sorted(r["plant1_prob"] for r in rows)
    king = Counter(r["open1_kingdom"] for r in rows)
    print(f"\nposes: {len(groups)}; with an animal {n_animal}; without {len(rows)}")
    for cut in (0.3, 0.45, 0.6, 0.8):
        print(f"  plant name at or above {cut:.0%}: {sum(p >= cut for p in probs)}")
    print(f"  open first name's kingdom: {dict(king)}")
    kept = [r for r in rows if r["kept"]]
    print(f"  named by SV's rule (open first name agrees, {am.PLANT_AGREE:.0%}+): {len(kept)}, "
          f"of which at or above 45 %: {sum(r['plant1_prob'] >= 0.45 for r in kept)}")
    named = [r for r in rows if r["yours"]]
    ok = sum(r["yours"] in (r["plant1"].lower(),) for r in named)
    ok3 = sum(r["yours"] in (r["plant1"].lower(), r["plant2"].lower(), r["plant3"].lower())
              for r in named)
    print(f"  with your name: {len(named)}; plant first right {ok}, in top 3 {ok3}")
    for r in named:
        print(f"    {r['picture']:28s} yours {r['yours']:30s} AI {r['plant1']} {r['plant1_prob']:.0%}")
    print(f"\n{path}\n{page}\n({time.time() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
