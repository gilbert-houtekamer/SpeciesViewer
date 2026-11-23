"""EXPERIMENT — one day, ALL its pictures: is the bird sharp, how big is it, and
what is it? Bars from 0 to 100 %, per picture, sorted by pose.

ONE JOB: for every picture in a day folder
  box      — MegaDetector (ONNX) finds the animal;
  sharp    — sharpness INSIDE the box (the fine grid at the picture's own size,
             engine.picture_score), as a percentage of YOUR master pictures from
             the same camera: "sharper on the animal than X % of what you kept";
  size     — the animal's long side in pixels at the picture's own size, as a
             bar from 100 px (0 %) to 2500 px (100 %, log scale; GH 2026-11-23);
and per pose (engine.redundancy.poses) the best frame — sharp first, then size —
named by BioCLIP 2 (ONNX, within the eBird list of the region), but only when
that frame is any good: a pose with nothing sharp gets no name.
Writes a contact sheet (HTML + thumbnails with the box drawn) and a CSV to
app-data reports/. READ-ONLY for the pictures and the master. No PyTorch.

  %LOCALAPPDATA%\\SpeciesViewer\\ml\\Scripts\\python.exe tools/ml/svr_day_quality.py
      --folder "D:/CurrentPictures/2026/2026 Winter/2026-11-23" --region NL
"""
from __future__ import annotations

import argparse
import bisect
import csv
import html
import io
import json
import math
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "speciesviewer"))
sys.path.insert(0, str(HERE))

import onnx_models as om                                   # noqa: E402
import svr_bioclip_trip as bt                              # noqa: E402
from svr_onnx_export import ai_dir                         # noqa: E402

THUMB = 300
NAME_FROM = 15          # a pose's best frame below this sharpness % gets no name


def norm_model(s: str) -> str:
    return "".join((s or "").lower().split())


def size_pct(px: float) -> float:
    if px <= 100:
        return 0.0
    return min(1.0, math.log(px / 100) / math.log(25)) * 100


def exif(files: list[Path]) -> dict:
    """{file name: {Model, ImageWidth, ImageHeight, ISO}} in one ExifTool run."""
    from utils.exiftool_path import exe
    arg = Path(tempfile.gettempdir()) / "sv_dayq_args.txt"
    try:
        arg.write_text("\n".join(str(f) for f in files), encoding="utf-8")
        out = subprocess.run([exe(), "-j", "-n", "-charset", "filename=utf8", "-Model",
                              "-ImageWidth", "-ImageHeight", "-ISO", "-@", str(arg)],
                             capture_output=True, timeout=600).stdout
    finally:
        arg.unlink(missing_ok=True)
    return {Path(d["SourceFile"]).name: d for d in json.loads(out.decode("utf-8") or "[]")}


def master_reference(models: set[str]) -> dict[str, list[float]]:
    """{camera: sorted in-box sharpness of your master pictures} — the yardstick."""
    from db.master_db import MasterDB
    from engine import redundancy as rd
    from engine.picture_score import Cache, fine_in
    from utils.config import _config
    root = _config.get("master", "masterbiotadatabase", fallback="").strip().strip('"')
    db = MasterDB(root)
    db.ensure_fresh()
    rows = [r for d in db.pictures_in_daterange("0001-01-01", "9999-12-31").values()
            for r in d if norm_model(r.get("CameraModel")) in models]
    db.close()
    boxes = bt.load_boxes()
    cache = Cache()
    ref: dict[str, list[float]] = {}
    for r in rows:
        files = r.get("Files") or []
        folder = Path(root) / (r.get("TargetPath") or "")
        for f in files:
            p = folder / f
            b = bt.animal_box(boxes.get(str(p)))
            if b is None:
                continue
            sc = cache.get(p)
            if sc and sc.get("fine") is not None:
                ref.setdefault(norm_model(r.get("CameraModel")), []).append(
                    fine_in(sc["fine"], b))
            break
    cache.close()
    for v in ref.values():
        v.sort()
    return ref


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--folder", required=True)
    ap.add_argument("--region", default="NL")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    t0 = time.time()
    from PIL import Image, ImageDraw, ImageOps
    from engine import redundancy as rd
    from engine.picture_score import Cache, fine_in, image_bytes, score_pictures
    from utils.cache_paths import app_data_dir
    from utils.config import _config

    folder = Path(a.folder)
    pics = bt.folder_pictures(folder)
    meta = exif([p["src"] for p in pics])
    cache = Cache()
    score_pictures(pics, cache)
    cache.close()
    models = {norm_model(m.get("Model")) for m in meta.values()}
    ref = master_reference(models)
    print(f"{len(pics)} pictures; yardstick: "
          + ", ".join(f"{k} {len(v)} master pictures" for k, v in ref.items())
          + f" ({time.time() - t0:.0f} s)", flush=True)

    det = om.MegaDetector(ai_dir())
    out = app_data_dir() / "reports" / f"dayquality_{folder.name}_{datetime.now():%Y%m%d-%H%M%S}"
    (out / "img").mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(pics):
        data = image_bytes(p["src"])
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB") if data else None
        p["box"] = None
        p["sharp_pct"] = p["size_pct"] = 0.0
        p["px"] = 0
        if im is None:
            continue
        small = im.copy()
        small.thumbnail((1280, 1280))
        p["box"] = bt.animal_box(det.boxes(small))
        m = meta.get(p["src"].name, {})
        native = max(m.get("ImageWidth") or 0, m.get("ImageHeight") or 0) or max(im.size)
        scale = native / max(im.size)
        if p["box"]:
            x0, y0, x1, y1 = p["box"]
            p["px"] = round(max((x1 - x0) * im.size[0], (y1 - y0) * im.size[1]) * scale)
            p["size_pct"] = size_pct(p["px"])
            if p.get("fine") is not None:
                s = fine_in(p["fine"], p["box"])
                r = ref.get(norm_model(m.get("Model")), [])
                p["sharp_in"] = s
                p["sharp_pct"] = 100 * bisect.bisect_left(r, s) / len(r) if r else 0.0
        p["iso"] = m.get("ISO")
        th = im.copy()
        th.thumbnail((THUMB, THUMB))
        if p["box"]:
            x0, y0, x1, y1 = p["box"]
            W, H = th.size
            ImageDraw.Draw(th).rectangle([x0 * W, y0 * H, x1 * W, y1 * H],
                                         outline=(60, 220, 90), width=2)
        th.save(out / "img" / f"{p['src'].stem}.jpg", quality=80)
        p["im"] = im
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(pics)} ({time.time() - t0:.0f} s)", flush=True)

    groups = rd.poses(pics, rd.Settings().gap, rd.Settings().change)
    reps = []
    for g in groups:
        g.sort(key=lambda p: (-p["sharp_pct"], -p["px"]))
        best = g[0]
        if best["box"] and best["sharp_pct"] >= NAME_FROM and best.get("im") is not None:
            reps.append(best)
    clip = om.BioClip(ai_dir())
    key = _config.get("ebird", "api_key", fallback="").strip()
    allowed = {s.lower() for s in bt.ebird_list(a.region, key, app_data_dir() / "ebird")}
    clip.keep([" ".join(n[0][-2:]).lower() in allowed for n in clip.all_names])
    crops = [bt.crop_image(p["im"], p["box"]) for p in reps]
    names = clip.top(clip.picture_vectors(crops), k=3)
    for p, n in zip(reps, names):
        p["names"] = n
    print(f"{len(groups)} poses, {len(reps)} named ({time.time() - t0:.0f} s)", flush=True)

    with open(out / "dayquality.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["pose", "picture", "time", "ISO", "animal", "bird px", "size %",
                    "sharp in box", "sharp %", "species 1", "%", "species 2", "species 3"])
        for gi, g in enumerate(groups):
            for p in g:
                n = (p.get("names") or []) + [("", "", 0)] * 3
                w.writerow([gi, p["src"].name, p["time"], p.get("iso"), bool(p["box"]),
                            p["px"], round(p["size_pct"]), round(p.get("sharp_in", 0), 1),
                            round(p["sharp_pct"]), n[0][0],
                            round(n[0][2] * 100) if n[0][0] else "", n[1][0], n[2][0]])

    def bar(label, pct, colour):
        return (f"<div class=bar><span>{label}</span><i style='width:{pct:.0f}%;"
                f"background:{colour}'></i><b>{pct:.0f}</b></div>")

    blocks = []
    for gi, g in enumerate(groups):
        best = g[0]
        if best.get("names"):
            head = " · ".join(f"<b>{html.escape(c or s)}</b> <i>{html.escape(s)}</i> "
                              f"{pr:.0%}" for s, c, pr in best["names"])
            sp = best["names"][0][2] * 100
            head = bar("species", sp, "#c9a227") + head
        else:
            head = "<span class=none>no name — nothing sharp enough in this pose</span>"
        cards = []
        for p in g:
            found = f" · {p['px']} px" if p["box"] else " · no animal found"
            cards.append(
                f"<figure><img src='img/{html.escape(p['src'].stem)}.jpg'>"
                f"<figcaption>{html.escape(p['src'].stem)}"
                f"{found}"
                f"{bar('sharp', p['sharp_pct'], '#3fa34d')}{bar('size', p['size_pct'], '#3d7fc4')}"
                f"</figcaption></figure>")
        blocks.append(f"<section><h3>Pose {gi + 1} · {len(g)} frames · "
                      f"{html.escape(str(best['time'])[11:19])}</h3><p>{head}</p>"
                      f"<div class=row>{''.join(cards)}</div></section>")
    (out / "index.html").write_text(f"""<!doctype html><meta charset=utf-8>
<title>Day quality — {html.escape(folder.name)}</title>
<style>body{{font:13px system-ui;margin:16px;background:#fff;color:#222}}
section{{border-top:1px solid #ccc;padding:6px 0}} h3{{margin:4px 0;font-size:14px}}
.row{{display:flex;flex-wrap:wrap;gap:8px}} figure{{margin:0;width:{THUMB}px}}
img{{max-width:{THUMB}px;display:block}} figcaption{{font-size:11px;color:#555}}
.bar{{display:flex;align-items:center;gap:4px;font-size:11px}}
.bar span{{width:46px}} .bar i{{display:block;height:7px;border-radius:2px}}
.bar b{{font-weight:normal;color:#333}} .none{{color:#a33}}</style>
<h2>{html.escape(folder.name)}: {len(pics)} pictures, {len(groups)} poses</h2>
<p>sharp = sharpness inside the green box, as % of your master pictures from the same
camera · size = the animal's long side, 100 px = 0 %, 2500 px = 100 % · species =
BioCLIP 2 within the eBird list {html.escape(a.region)}, only for a pose whose best
frame is at least {NAME_FROM} % sharp. Frames sorted best first.</p>
{''.join(blocks)}""", encoding="utf-8")
    print(f"Sheet: {out / 'index.html'}\n({(time.time() - t0) / 60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
