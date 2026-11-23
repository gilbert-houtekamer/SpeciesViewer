"""EXPERIMENT — do the ONNX models (tools/ml/onnx_models.py, no PyTorch) give the
same answers as the PyTorch run?

ONE JOB: two read-only comparisons.
  names — Gambia again (same poses, same crops, same eBird list) through ONNX;
          per pose: is the first name the same as in the PyTorch report
          (reports/bioclip_GM_…/bioclip.csv, the newest), and how far apart are
          the percentages; plus the score against your own names.
  boxes — MegaDetector through ONNX on a sample of master pictures, against the
          PyTorch boxes in the cache: found / not found, and the overlap (IoU).
Prints whether PyTorch was ever loaded (it must not be).

  %LOCALAPPDATA%\\SpeciesViewer\\ml\\Scripts\\python.exe tools/ml/svr_onnx_check.py
"""
from __future__ import annotations

import csv
import random
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "speciesviewer"))
sys.path.insert(0, str(HERE))

import onnx_models as om                                   # noqa: E402
import svr_bioclip_trip as bt                              # noqa: E402
from svr_onnx_export import ai_dir                         # noqa: E402


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def check_names(region="GM", country="The Gambia") -> None:
    from db.master_db import MasterDB
    from engine import redundancy as rd
    from engine.picture_score import Cache
    from utils.cache_paths import app_data_dir
    from utils.config import _config
    reports = sorted((app_data_dir() / "reports").glob(f"bioclip_{region}_*/bioclip.csv"))
    ref = {}
    for rep in reversed(reports):                  # the newest full run with a list
        rows = list(csv.DictReader(open(rep, encoding="utf-8-sig"), delimiter=";"))
        if len(rows) > 1000 and rows[0].get("list 1"):
            ref = {int(r["pose"]): r for r in rows}
            print(f"PyTorch reference: {rep.parent.name} ({len(ref)} poses)")
            break
    root = _config.get("master", "masterbiotadatabase", fallback="").strip().strip('"')
    db = MasterDB(root)
    db.ensure_fresh()
    rows = [r for day in db.pictures_in_daterange("0001-01-01", "9999-12-31").values()
            for r in day if (r.get("Country") or "").strip() == country]
    db.close()
    pics = rd.from_master_rows(rows, root)
    cache = Cache()
    for p in pics:
        p.update(cache.get(p["src"]) or {"sharp": 0, "dhash": "0" * 16})
    cache.close()
    groups = rd.poses(pics, rd.Settings().gap, rd.Settings().change)
    boxes = bt.load_boxes()
    t0 = time.time()
    idx, ims = [], []
    for i, g in enumerate(groups):
        if i not in ref:
            continue
        rep = max(g, key=lambda p: p.get("sharp") or 0)
        im = bt.crop(rep["src"], bt.animal_box(boxes.get(str(rep["src"]))))
        if im is not None:
            idx.append(i)
            ims.append(im)
    print(f"{len(ims)} crops ({time.time() - t0:.0f} s)", flush=True)
    t0 = time.time()
    clip = om.BioClip(ai_dir())
    vecs = clip.picture_vectors(ims)
    t_vec = time.time() - t0
    open_res = clip.top(vecs)
    key = _config.get("ebird", "api_key", fallback="").strip()
    allowed = {s.lower() for s in bt.ebird_list(region, key, app_data_dir() / "ebird")}
    clip.keep([" ".join(n[0][-2:]).lower() in allowed for n in clip.all_names])
    list_res = clip.top(vecs)
    print(f"BioCLIP 2 through ONNX ({om.providers()[0]}): {len(ims)} pictures in "
          f"{t_vec:.0f} s, both runs in {time.time() - t0:.0f} s")
    for name, res, col in (("everything", open_res, "open"), ("eBird list", list_res, "list")):
        same = 0
        diffs = []
        for i, r in zip(idx, res):
            ref_name = ref[i][f"{col} 1"]
            same += r[0][0].lower() == ref_name.lower()
            pct = ref[i][f"{col} %"].rstrip("%")
            if r[0][0].lower() == ref_name.lower() and pct:
                diffs.append(abs(r[0][2] * 100 - float(pct)))
        diffs.sort()
        print(f"  {name}: first name the same as PyTorch in {same}/{len(idx)} "
              f"({same / max(1, len(idx)):.1%}); percentage apart: median "
              f"{diffs[len(diffs) // 2] if diffs else 0:.1f}, 95% within "
              f"{diffs[int(len(diffs) * .95)] if diffs else 0:.1f} points")
    for name, res, col in (("everything", open_res, "open"), ("eBird list", list_res, "list")):
        n = mine = mine_ref = 0
        for i, r in zip(idx, res):
            sci = ref[i]["your species"].lower()
            if not sci:
                continue
            n += 1
            mine += r[0][0].lower() == sci
            mine_ref += ref[i][f"{col} 1"].lower() == sci
        print(f"  {name}: first = your name — ONNX {mine / max(1, n):.1%}, "
              f"PyTorch {mine_ref / max(1, n):.1%}")


def check_boxes(n=300) -> None:
    import io
    from PIL import Image, ImageOps
    from engine.picture_score import image_bytes
    cached = bt.load_boxes()
    paths = sorted(cached)
    random.Random(7).shuffle(paths)
    det = om.MegaDetector(ai_dir())
    found = Counter()
    ious = []
    t0 = time.time()
    done = 0
    for path in paths:
        if done >= n:
            break
        data = image_bytes(Path(path))
        if not data:
            continue
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        im.thumbnail((1280, 1280))
        a = bt.animal_box(cached[path])
        b = bt.animal_box(det.boxes(im))
        found[(a is not None, b is not None)] += 1
        if a and b:
            ious.append(iou(a, b))
        done += 1
    ious.sort()
    print(f"MegaDetector through ONNX ({om.providers()[0]}): {done} pictures in "
          f"{time.time() - t0:.0f} s")
    print(f"  animal found by both {found[(True, True)]}, by neither {found[(False, False)]}, "
          f"only PyTorch {found[(True, False)]}, only ONNX {found[(False, True)]}")
    if ious:
        print(f"  overlap of the two boxes: median {ious[len(ious) // 2]:.2f}, "
              f"{sum(x >= 0.8 for x in ious) / len(ious):.0%} at 0.8 or more")


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    check_boxes()
    check_names()
    print(f"\nPyTorch loaded: {'torch' in sys.modules}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
