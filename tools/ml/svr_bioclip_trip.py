"""EXPERIMENT (step 2 of docs/plans/picture_score_plan.md's AI roadmap) — what would
BioCLIP 2 call the animals of a finished trip, next to the names YOU gave them?

ONE JOB: for every pose (a burst of near-identical frames, engine.redundancy.poses)
of the master's pictures from one country, take the sharpest frame, crop it to the
animal (MegaDetector box from tools/ml/svr_bird_box.py, when there is one), ask
BioCLIP 2 for its top 5 species — once over everything it knows, and once only
among the eBird list of that country (when an eBird key is set: [ebird] api_key
in the config) — and compare with your own name for the pose.
READ-ONLY for the master. Writes a CSV and a contact sheet (HTML + small crops)
to app-data reports/.

Runs in the separate experiment environment:
  %LOCALAPPDATA%\\SpeciesViewer\\ml\\Scripts\\python.exe tools/ml/svr_bioclip_trip.py
  ... --country "The Gambia" --region GM --limit 100
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import sqlite3
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

SV = str(Path(__file__).resolve().parents[2] / "speciesviewer")
MIN_CONF = 0.2
THUMB = 360


# ── eBird: the species list of a region (cached; the key never leaves the config) ──

def ebird_list(region: str, key: str, cache_dir: Path) -> list[str]:
    """Scientific names on eBird's list for `region` (e.g. GM). Cached per
    region, and the eBird taxonomy once, so a later run needs no internet."""
    cache_dir.mkdir(parents=True, exist_ok=True)

    def get(url, name):
        p = cache_dir / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        req = urllib.request.Request(url, headers={"X-eBirdApiToken": key})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        p.write_text(json.dumps(data), encoding="utf-8")
        return data

    codes = get(f"https://api.ebird.org/v2/product/spplist/{region}", f"spplist_{region}.json")
    tax = get("https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=json", "taxonomy.json")
    sci = {t["speciesCode"]: t["sciName"] for t in tax if t.get("category") == "species"}
    return sorted({sci[c] for c in codes if c in sci})


# ── pictures ────────────────────────────────────────────────────────────────

def load_boxes() -> dict:
    from utils.cache_paths import app_data_dir
    p = app_data_dir() / "scores" / "bird_boxes.sqlite"
    if not p.exists():
        return {}
    con = sqlite3.connect(str(p))
    return {path: json.loads(b) for path, b in con.execute("select path, boxes from boxes")}


def animal_box(boxes):
    best = max((b for b in boxes or [] if b["cat"] == "animal" and b["conf"] >= MIN_CONF),
               key=lambda b: b["conf"], default=None)
    return best["box"] if best else None


def crop(src: Path, box):
    """The upright picture, cut to the animal with some room around it."""
    from PIL import Image, ImageOps
    from engine.picture_score import image_bytes
    data = image_bytes(src)
    if not data:
        return None
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    if box:
        W, H = im.size
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        side = max(w * W, h * H) * 1.3                  # square, 30 % room
        side = max(side, 0.15 * max(W, H))
        cx, cy = (x0 + x1) / 2 * W, (y0 + y1) / 2 * H
        im = im.crop((int(max(0, cx - side / 2)), int(max(0, cy - side / 2)),
                      int(min(W, cx + side / 2)), int(min(H, cy + side / 2))))
    im.thumbnail((1024, 1024))
    return im


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--country", default="The Gambia")
    ap.add_argument("--region", default="GM", help="eBird region code")
    ap.add_argument("--limit", type=int, default=0, help="only the first N poses")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    t0 = time.time()
    from bioclip import Rank, TreeOfLifeClassifier
    import torch
    sys.path.insert(0, SV)
    from db.master_db import MasterDB
    from engine import redundancy as rd
    from engine.picture_score import Cache, score_pictures
    from utils.cache_paths import app_data_dir
    from utils.config import _config

    root = _config.get("master", "masterbiotadatabase", fallback="").strip().strip('"')
    db = MasterDB(root)
    db.ensure_fresh()
    rows = [r for day in db.pictures_in_daterange("0001-01-01", "9999-12-31").values()
            for r in day if (r.get("Country") or "").strip() == a.country]
    names = {r["UniquePictureName"]: ((r.get("WetSoortNaam") or "").strip(),
                                      (r.get("EngSoortNaam") or "").strip(),
                                      (r.get("NLSoortNaam") or "").strip()) for r in rows}
    db.close()
    pics = rd.from_master_rows(rows, root)
    cache = Cache()
    print(f"{len(pics)} pictures from {a.country}; measuring what is new…", flush=True)
    score_pictures(pics, cache)
    cache.close()
    groups = rd.poses(pics, rd.Settings().gap, rd.Settings().change)
    if a.limit:
        groups = groups[:a.limit]
    boxes = load_boxes()
    print(f"{len(groups)} poses ({time.time() - t0:.0f} s)", flush=True)

    out = app_data_dir() / "reports" / f"bioclip_{a.region}_{datetime.now():%Y%m%d-%H%M%S}"
    (out / "img").mkdir(parents=True, exist_ok=True)
    poses, images = [], []
    for i, g in enumerate(groups):
        rep = max(g, key=lambda p: p.get("sharp") or 0)
        mine = Counter(names[p["upn"]] for p in g).most_common(1)[0][0]
        box = animal_box(boxes.get(str(rep["src"])))
        im = crop(rep["src"], box)
        if im is None:
            continue
        th = im.copy()
        th.thumbnail((THUMB, THUMB))
        th.save(out / "img" / f"{i:05d}.jpg", quality=82)
        poses.append({"i": i, "upn": rep["upn"], "frames": len(g), "mine": mine,
                      "boxed": box is not None, "time": str(rep["time"])})
        images.append(im)
    print(f"{len(images)} crops ({time.time() - t0:.0f} s); BioCLIP 2 on "
          f"{torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'the CPU'}…",
          flush=True)

    clf = TreeOfLifeClassifier(device="cuda" if torch.cuda.is_available() else "cpu")

    def top5(ims):
        res = clf.predict(ims, Rank.SPECIES, k=5, batch_size=32)
        by = {}
        for r in res:
            by.setdefault(r["file_name"], []).append(
                (r["species"], r.get("common_name") or "", r["score"]))
        return [sorted(by.get(str(k), by.get(k, [])), key=lambda x: -x[2])
                for k in range(len(ims))]

    def keyed(ims):          # predict() names a PIL image by its index
        return top5(ims)

    open_res = keyed(images)
    key = _config.get("ebird", "api_key", fallback="").strip()
    list_res, n_list = None, 0
    if key:
        allowed = set(s.lower() for s in ebird_list(a.region, key, app_data_dir() / "ebird"))
        keep = [(" ".join(n[0][-2:]).lower() in allowed) for n in clf.txt_names]
        n_list = sum(keep)
        print(f"eBird list {a.region}: {len(allowed)} species, {n_list} known to BioCLIP",
              flush=True)
        clf.apply_filter(keep)
        list_res = keyed(images)
    else:
        print("No eBird key ([ebird] api_key) — only the open run.", flush=True)

    # ── compare ─────────────────────────────────────────────────────────────
    def same(got: str, sci: str) -> bool:
        """The same bird, also under an older genus name or without your
        subspecies: the species epithet decides (an estimate, not a taxonomy)."""
        g, s = got.split(), sci.split()
        return got == sci or (len(g) >= 2 and len(s) >= 2 and g[-1] == s[-1]
                              and (g[0] != s[0] or len(s) == 3)) \
            or (len(s) == 3 and g == s[:2])

    def score(res):
        n = top1 = top1_any = top5_ = genus = 0
        for p, r in zip(poses, res):
            sci = p["mine"][0].lower()
            if not sci or not r:
                continue
            n += 1
            got = [x[0].lower() for x in r]
            top1 += got[0] == sci
            top1_any += same(got[0], sci)
            top5_ += any(same(x, sci) for x in got)
            genus += got[0].split(" ")[0] == sci.split(" ")[0]
        return n, top1, top1_any, top5_, genus

    lines = []
    for label, res in (("Everything BioCLIP knows", open_res),
                       (f"Only the eBird list of {a.region}", list_res)):
        if res is None:
            continue
        n, t1, ta, t5, gn = score(res)
        lines.append(f"{label}: first = yours {t1 / max(1, n):.0%} ({t1}/{n}), also under "
                     f"another name {ta / max(1, n):.0%}, in the top 5 {t5 / max(1, n):.0%}, "
                     f"same genus first {gn / max(1, n):.0%}")
    for s in lines:
        print(s)

    with open(out / "bioclip.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["pose", "picture", "frames", "time", "boxed", "your species",
                    "your name", "open 1", "open %", "open 2", "open 3",
                    "list 1", "list %", "list 2", "list 3"])
        for k, p in enumerate(poses):
            o = open_res[k] + [("", "", 0)] * 3
            li = (list_res[k] if list_res else []) + [("", "", 0)] * 3
            w.writerow([p["i"], p["upn"], p["frames"], p["time"], p["boxed"],
                        p["mine"][0], p["mine"][1] or p["mine"][2],
                        o[0][0], f"{o[0][2]:.0%}", o[1][0], o[2][0],
                        li[0][0], f"{li[0][2]:.0%}" if li[0][0] else "", li[1][0], li[2][0]])

    def cell(res, sci):
        if not res:
            return "<td></td>"
        out_ = []
        for s, common, pr in res[:3]:
            hit = " class=hit" if s.lower() == sci.lower() else ""
            out_.append(f"<div{hit}>{html.escape(s)} <small>{html.escape(common)}</small>"
                        f" <b>{pr:.0%}</b></div>")
        return "<td>" + "".join(out_) + "</td>"

    rows_html = []
    for k, p in enumerate(poses):
        sci, en, nl = p["mine"]
        o1 = open_res[k][0][0].lower() if open_res[k] else ""
        cls = "ok" if o1 == sci.lower() else "miss"
        rows_html.append(
            f"<tr class={cls}><td><img src='img/{p['i']:05d}.jpg'></td>"
            f"<td><b>{html.escape(nl or en)}</b><br><i>{html.escape(sci)}</i><br>"
            f"<small>{html.escape(p['upn'])} · {p['frames']} frames"
            f"{'' if p['boxed'] else ' · no box'}</small></td>"
            + cell(open_res[k], sci) + (cell(list_res[k], sci) if list_res else "")
            + "</tr>")
    (out / "index.html").write_text(f"""<!doctype html><meta charset=utf-8>
<title>BioCLIP 2 — {html.escape(a.country)}</title>
<style>body{{font:14px system-ui;margin:16px;background:#fff;color:#222}}
td{{vertical-align:top;padding:6px;border-bottom:1px solid #ddd}} img{{max-width:{THUMB}px}}
.hit{{background:#d8f5d0}} tr.miss td:first-child{{outline:3px solid #e0a0a0}}
small{{color:#666}}</style>
<h2>BioCLIP 2 — {html.escape(a.country)}: {len(poses)} poses</h2>
<p>{'<br>'.join(html.escape(s) for s in lines)}</p>
<table><tr><th></th><th>Your name</th><th>BioCLIP, everything</th>
{'<th>BioCLIP, eBird list ' + a.region + '</th>' if list_res else ''}</tr>
{''.join(rows_html)}</table>""", encoding="utf-8")
    print(f"Sheet: {out / 'index.html'}\n({(time.time() - t0) / 60:.0f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
