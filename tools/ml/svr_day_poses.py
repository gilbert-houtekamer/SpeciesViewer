"""Experiment: a day's POSES as a contact sheet — and could they be joined?

ONE JOB: for one day folder (its boxes, sharpness and vectors cached by the AI…
dialog), show every pose in time order with its frames: the animal box, keep or
bin (engine.day_ai.decide with your master's yardstick), the AI's name per pose,
and whether the detector ran on the frame or the animal was FOLLOWED from the
frame before (as AI… does it now: within a pose).
Then poses are joined into SERIES — consecutive poses at most --join seconds
apart whose names agree (or one is "not sure") — and per joining time the sheet
says how many series there are, and how many detections AI… would need if the
following also went on across the poses of a series.

INVARIANT: read-only (pictures, sidecars, app caches). The sheet and its small
pictures go to app-data reports/day_poses_<day>/.

  PYTHONPATH=speciesviewer python tools/ml/svr_day_poses.py --folder "D:/.../2026-11-23" --open
"""
from __future__ import annotations

import argparse
import html
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "speciesviewer"))
sys.path.insert(0, str(HERE))

import svr_focus_check as fc  # noqa: E402
from engine import ai_measure, ai_yardstick, day_ai  # noqa: E402
from engine import redundancy as rd  # noqa: E402
from engine.ai_measure import FOLLOW_MIN, AiCache, follow, small_gray  # noqa: E402
from engine.picture_score import Cache, image_bytes  # noqa: E402
from utils.cache_paths import app_data_dir  # noqa: E402

RAW = (".orf", ".nef", ".cr3", ".arw", ".heic", ".hif", ".jpg")
THUMB = 300


def decode(src):
    from PIL import Image, ImageOps
    return ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes(src)))).convert("RGB")


def name_key(p):
    n = p.get("name")
    return n["sci"].lower() if n else None


def series(groups, join: float):
    """Consecutive poses joined: gap <= join seconds and names that agree."""
    out = []
    for g in groups:
        s = out[-1] if out else None
        if s is not None:
            last, first = s[-1][-1], g[0]
            gap = (first["time"] - last["time"]).total_seconds()
            a = next((name_key(x) for x in reversed([q for gg in s for q in gg]) if name_key(x)), None)
            b = name_key(g[0])
            if gap <= join and (a is None or b is None or a == b):
                s.append(g)
                continue
        out.append([g])
    return out


def detections(seq, grays, chain_poses: bool) -> int:
    """Detector runs when the animal is followed frame to frame, within a pose
    only, or on through all poses of a series."""
    n = 0
    for s in seq:
        prev = None
        for g in s:
            if not chain_poses:
                prev = None
            for p in g:
                box = None
                if prev is not None and prev[1] is not None:
                    nb, score = follow(prev[0], grays[p["key"]], prev[1])
                    if nb is not None and score >= FOLLOW_MIN:
                        box = nb
                if box is None:
                    n += 1
                    box = p.get("box")
                prev = (grays[p["key"]], box)
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--region", default="NL")
    ap.add_argument("--join", type=float, nargs="+", default=[30, 60, 120, 300])
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args(argv)
    t0 = time.time()
    folder = Path(a.folder)
    from utils.config import _config
    master = _config.get("master", "masterbiotadatabase", fallback="") or None
    side = fc.sidecars(folder)
    sc, aic = Cache(), AiCache()
    yard = ai_yardstick.get(master)
    pics = []
    for src in sorted(p for p in folder.iterdir() if p.suffix.lower() in RAW):
        s = sc.get(src)
        k = src.stem.lower()
        r = side.get(k, {})
        t = fc.parse_time(r.get("DateTimeOriginal", ""))
        if s is None or t is None:
            continue
        lab = str(r.get("Label", "") or "").strip().capitalize()
        p = {"key": k, "upn": k, "src": src, "time": t, "stars": int(r.get("Rating") or 0),
             "label": lab, "jpg": False, "bytes": src.stat().st_size}
        p.update(s)
        pics.append(p)
    pics.sort(key=lambda p: (p["time"], p["key"]))
    print(f"{folder.name}: {len(pics)} pictures; decoding for the sheet…", flush=True)
    out = app_data_dir() / "reports" / f"day_poses_{folder.name}"
    (out / "t").mkdir(parents=True, exist_ok=True)
    grays, ims = {}, {}

    def one(p):
        im = decode(p["src"])
        return p["key"], im, small_gray(im)
    with ThreadPoolExecutor(4) as pool:
        for k, im, g in pool.map(one, pics):
            grays[k], ims[k] = g, im

    # the measurement as AI… makes it, from the caches (detections where cached,
    # else followed boxes, else the detector now)
    how = {}
    for p in pics:
        found = aic.boxes(p["src"])
        box = None if found is not None else aic.followed(p["src"])
        how[p["key"]] = "followed" if box is not None else "detected"
        p.update(ai_measure.measure(p["src"], ims[p["key"]], p, "OM-1MarkII", cache=aic,
                                    yardstick=yard, name=False, box=box))
    for p in day_ai.pose_leaders(pics):
        p["names"], _ = ai_measure.name_of(p["src"], ims[p["key"]], p["box"], cache=aic,
                                           region=a.region, master_root=master)
    s = day_ai.DaySettings()
    groups = day_ai.decide(pics, s, None)

    # joining
    rows = []
    within = detections([[g] for g in groups], grays, False)
    for j in a.join:
        seq = series(groups, j)
        rows.append((j, len(seq), detections(seq, grays, True)))
    seq = series(groups, max(a.join))

    # small pictures with the box
    from PIL import ImageDraw
    for p in pics:
        im = ims[p["key"]].copy()
        im.thumbnail((THUMB * 2, THUMB * 2))
        if p.get("box"):
            W, H = im.size
            x0, y0, x1, y1 = p["box"]
            ImageDraw.Draw(im).rectangle([x0 * W, y0 * H, x1 * W, y1 * H],
                                         outline=(102, 187, 106), width=4)
        im.save(out / "t" / f"{p['key']}.jpg", quality=80)

    css = """body{font:13px system-ui;margin:16px;background:#f6f5f1;color:#222}
    h1{font-size:18px} table{border-collapse:collapse;margin:8px 0 16px}
    td,th{border:1px solid #ccc;padding:3px 10px;text-align:right}
    .ser{margin:14px 0;padding:6px 8px;border-left:6px solid #1565c0;background:#eef3fa}
    .ser h2{font-size:13px;margin:2px 0 6px;color:#1565c0}
    .pose{margin:6px 0;padding:6px;background:#fff;border-radius:6px}
    .pose h3{font-size:12px;margin:0 0 6px;color:#555;font-weight:600}
    .row{display:flex;flex-wrap:wrap;gap:6px} .c{width:%dpx;font-size:11px}
    .c img{width:%dpx;border:4px solid #ccc;display:block}
    .keep img{border-color:#2e7d32} .purple img{border-color:#8e24aa;opacity:.55}
    .mine img{border-color:#1565c0} .f{color:#c77700;font-weight:600}""" % (THUMB + 4, THUMB)
    h = [f"<!doctype html><meta charset=utf-8><title>Poses {folder.name}</title>"
         f"<style>{css}</style><h1>{html.escape(folder.parent.name)} · {folder.name}: "
         f"{len(pics)} pictures in {len(groups)} poses</h1>",
         "<p>Green frame = AI… keeps it, purple = could go, blue = yours already. "
         "<span class=f>F</span> = the animal was followed from the frame before "
         "(no detector); D = the detector ran. Green box = the animal.</p>",
         "<table><tr><th>poses joined when at most … apart, same name</th><th>series</th>"
         "<th>detector runs</th></tr>"
         f"<tr><td>not joined (AI… today: following within a pose)</td><td>{len(groups)}</td>"
         f"<td>{within} of {len(pics)}</td></tr>"]
    for j, ns, nd in rows:
        h.append(f"<tr><td>{j:.0f} s, following on through the series</td><td>{ns}</td>"
                 f"<td>{nd} of {len(pics)}</td></tr>")
    h.append(f"</table><p>Below: the series at {max(a.join):.0f} s.</p>")
    gi = {id(g): i for i, g in enumerate(groups)}
    for si, sq in enumerate(seq, 1):
        frames = sum(len(g) for g in sq)
        names = {(g[0]["name"] or {}).get("name") for g in sq if g[0].get("name")}
        h.append(f"<div class=ser><h2>Series {si} · {sq[0][0]['time']:%H:%M:%S}–"
                 f"{sq[-1][-1]['time']:%H:%M:%S} · {len(sq)} poses, {frames} pictures · "
                 f"{html.escape(', '.join(sorted(n for n in names if n)) or 'no name')}</h2>")
        for g in sq:
            n = g[0].get("name")
            lead = next((p for p in g if p.get("names")), None)
            ai = (f"{html.escape(lead['names'][0].get('name') or '')} "
                  f"{lead['names'][0]['prob']:.0%}" if lead and lead.get("names") else "no animal")
            h.append(f"<div class=pose><h3>Pose {gi[id(g)] + 1} · {g[0]['time']:%H:%M:%S} · "
                     f"{len(g)} frames · {ai}{'' if n or not lead else ' (not sure)'}</h3><div class=row>")
            for p in g:
                cls = {"keep": "keep", "purple": "purple"}.get(p.get("verdict"), "mine")
                why = p.get("why") or ("keep" if p.get("verdict") == "keep" else "yours")
                f = ("<span class=f>F</span>" if how[p["key"]] == "followed" else "D")
                sp = p.get("sharp_pct")
                h.append(f"<div class='c {cls}'><img loading=lazy src='t/{p['key']}.jpg'>"
                         f"{f} · {p['key'][-8:]} · {p['time']:%H:%M:%S} · {why} · sharp "
                         f"{'' if sp is None else f'{sp:.0f} %'} · {p.get('px', 0)} px</div>")
            h.append("</div></div>")
        h.append("</div>")
    (out / "index.html").write_text("\n".join(h), encoding="utf-8")
    print("detections:", within, "within poses;", rows)
    print(f"Sheet: {out / 'index.html'} ({time.time() - t0:.0f} s)")
    if a.open:
        import webbrowser
        webbrowser.open((out / "index.html").as_uri())


if __name__ == "__main__":
    main()
