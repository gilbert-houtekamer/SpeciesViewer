"""EXPERIMENT — can BioCLIP tell an animal that faces you (eye visible) from one
that looks away, by asking it in words?

ONE JOB: for one day, score every picture's animal crop against "facing / eye
visible" and "turned away" sentences, and measure that score against your own
colours: within one sitting, is your Yellow/Red scored above your Blue/Purple?
Also writes a page with the highest and lowest scores, to judge by eye.
INVARIANT: report only — reads pictures and sidecars, writes into <work> only
(never the day folders, never SpeciesViewer's caches).

Two steps, two environments:
  1. crops, in SpeciesViewer's own Python (needs the AI cache with boxes, i.e.
     the day measured once with AI…):
       python tools/ml/svr_ai_eye_prompts.py crops <AI day folder> <work folder>
  2. score, in the experiment environment (PyTorch + open_clip):
       %LOCALAPPDATA%\\SpeciesViewer\\ml\\Scripts\\python.exe tools/ml/svr_ai_eye_prompts.py
           score <work folder> <your day folder> [--clip]
     --clip also scores with plain OpenAI CLIP (ViT-B/16, downloads ~600 MB
     once) as a yardstick: it knows everyday English better than BioCLIP.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

FACING = [
    "a photo of an animal looking at the camera",
    "a close-up of a bird with its eye clearly visible",
    "a bird with its head turned towards the camera",
    "a bird in profile with a sharp visible eye",
    "a mammal looking straight at the camera",
]
AWAY = [
    "a photo of an animal seen from behind",
    "a bird with its head turned away from the camera",
    "the back of a bird, its eye not visible",
    "a bird with its head hidden",
    "a mammal walking away from the camera",
]
CROP_PX = 448


# ── step 1: the crops (SpeciesViewer's Python) ──
def make_crops(day: Path, work: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "speciesviewer"))
    from engine import ai_measure, day_ai
    from engine.day_measure import load_upright
    work.mkdir(parents=True, exist_ok=True)
    aic = ai_measure.AiCache()
    todo = []
    for src in sorted(p for p in day.iterdir() if p.suffix.upper() == ".ORF"):
        box = aic.followed(src) or ai_measure.animal_box(aic.boxes(src))
        if box and not (work / f"{src.stem}.jpg").exists():
            todo.append((src, box))
    print(f"{len(todo)} crops to make")
    ahead = day_ai.Prefetch([s for s, _b in todo], load_upright)
    index = json.loads((work / "crops.json").read_text()) if (work / "crops.json").exists() else {}
    try:
        for i, (src, box) in enumerate(todo, 1):
            im = ai_measure.crop_image(ahead.get(src), box)
            im.thumbnail((CROP_PX, CROP_PX))
            im.save(work / f"{src.stem}.jpg", quality=90)
            index[src.stem] = list(box)
            if i % 100 == 0:
                print(f"  {i}/{len(todo)}")
    finally:
        ahead.close()
        aic.close()
    (work / "crops.json").write_text(json.dumps(index))
    print(f"{len(index)} crops in {work}")


# ── step 2: the score (experiment environment) ──
def read_xmp(x: Path):
    t = x.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'xmp:Label(?:="([^"]*)"|>([^<]*)<)', t)
    label = (m.group(1) or m.group(2)) if m else ""
    subj = re.search(r"<dc:subject>(.*?)</dc:subject>", t, re.S)
    kws = sorted(k.strip().lower() for k in re.findall(r"<rdf:li>([^<]*)</rdf:li>",
                                                         subj.group(1))) if subj else []
    d = re.search(r'exif:DateTimeOriginal(?:="|>)(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)', t)
    return label, kws, datetime.fromisoformat(d.group(1)) if d else None


def model_scores(name, stems, work):
    import open_clip
    import torch
    from PIL import Image
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if name == "bioclip":
        model, pre = open_clip.create_model_from_pretrained("hf-hub:imageomics/bioclip-2")
        tok = open_clip.get_tokenizer("hf-hub:imageomics/bioclip-2")
    else:
        model, _t, pre = open_clip.create_model_and_transforms("ViT-B-16", pretrained="openai")
        tok = open_clip.get_tokenizer("ViT-B-16")
    model = model.to(dev).eval()
    with torch.no_grad():
        def text(lines):
            v = model.encode_text(tok(lines).to(dev))
            v = v / v.norm(dim=-1, keepdim=True)
            v = v.mean(0)
            return v / v.norm()
        direction = text(FACING) - text(AWAY)
        out = {}
        for i in range(0, len(stems), 64):
            part = stems[i:i + 64]
            ims = torch.stack([pre(Image.open(work / f"{s}.jpg").convert("RGB")) for s in part]).to(dev)
            v = model.encode_image(ims)
            v = v / v.norm(dim=-1, keepdim=True)
            for s, x in zip(part, (v @ direction).tolist()):
                out[s] = x
    return out


def score(work: Path, his_day: Path, clip: bool) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    index = json.loads((work / "crops.json").read_text())
    his = {x.stem.upper(): read_xmp(x) for x in his_day.iterdir() if x.suffix.lower() == ".xmp"}
    stems = sorted(index)
    key = {s: s.split(" - ")[-1].upper() for s in stems}
    stems = [s for s in stems if key[s] in his]
    print(f"crops with your verdict: {len(stems)}")
    models = ["bioclip"] + (["clip"] if clip else [])
    saved = work / "scores.json"
    old = json.loads(saved.read_text()) if saved.exists() else {}
    scores = {m: old[m] if m in old and set(stems) <= set(old[m]) else model_scores(m, stems, work)
              for m in models}
    saved.write_text(json.dumps(scores))
    # the yardstick: the animal's size (box area), which you also prefer
    area = {s: (index[s][2] - index[s][0]) * (index[s][3] - index[s][1]) for s in stems}
    scores_all = dict(scores, size=area)

    # sittings: your same species, neighbours ≤ 120 s apart
    sit, prev = [], None
    for s in sorted(stems, key=lambda s: (his[key[s]][2] or datetime.min, s)):
        lab, kws, when = his[key[s]]
        if (prev and sit and prev[1] == kws and when and prev[2]
                and (when - prev[2]).total_seconds() <= 120):
            sit[-1].append(s)
        else:
            sit.append([s])
        prev = (lab, kws, when)
    BEST, GO = {"Yellow", "Red"}, {"Blue", "Purple"}
    import math
    rank = {m: {s: r / len(stems) for r, s in enumerate(sorted(stems, key=lambda s: v[s]))}
            for m, v in scores_all.items()}
    for m in models:          # facing added to size, as ranks (equal weight)
        scores_all[f"size+{m}"] = {s: rank["size"][s] + rank[m][s] for s in stems}
    for m in scores_all:
        sc = scores_all[m]
        won = lost = 0
        for g in sit:
            best = [sc[s] for s in g if his[key[s]][0] in BEST]
            go = [sc[s] for s in g if his[key[s]][0] in GO]
            for b in best:
                for x in go:
                    won += b > x
                    lost += b < x
        n = won + lost
        print(f"\n{m}: your Yellow/Red above your Blue/Purple in the same sitting: "
              f"{won}/{n} = {won / max(1, n):.1%}  (50 % = no better than chance)")
        if m not in models:
            continue
        by = defaultdict(list)
        for s in stems:
            by[his[key[s]][0] or "none"].append(sc[s])
        for lab, v in sorted(by.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
            print(f"  {lab:8} {len(v):5}  mean score {sum(v) / len(v):+.4f}")
    write_page(work, stems, scores, his, key)


def write_page(work, stems, scores, his, key):
    import html
    e = html.escape
    out = ["<!doctype html><meta charset=utf-8><title>Facing or away</title><style>"
           "body{font:14px system-ui,sans-serif;background:#1e1f1c;color:#ddd;margin:16px}"
           "figure{display:inline-block;margin:3px;width:180px;font-size:11px;color:#999}"
           "img{width:180px;height:180px;object-fit:cover;border-radius:4px}"
           "</style><h1>Facing or away — by the AI's own words</h1>"
           "<p>Each picture's animal as the AI cuts it out, with its score: high = "
           "'faces you, eye visible', low = 'turned away'. Your colour under each.</p>"]
    for m, sc in scores.items():
        ranked = sorted(stems, key=lambda s: -sc[s])
        for title, part in (("highest (should face you)", ranked[:48]),
                            ("lowest (should look away)", ranked[-48:][::-1])):
            out.append(f"<h2>{e(m)}: {title}</h2>")
            for s in part:
                lab = his[key[s]][0] or "—"
                out.append(f'<figure><img src="{e(s)}.jpg" loading=lazy>'
                           f"<figcaption>{sc[s]:+.3f} · {e(lab)} · {e(key[s])}</figcaption></figure>")
    (work / "facing.html").write_text("\n".join(out), encoding="utf-8")
    print(f"\npage: {work / 'facing.html'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="step", required=True)
    c = sub.add_parser("crops")
    c.add_argument("day", type=Path)
    c.add_argument("work", type=Path)
    s = sub.add_parser("score")
    s.add_argument("work", type=Path)
    s.add_argument("his_day", type=Path)
    s.add_argument("--clip", action="store_true")
    a = ap.parse_args()
    if a.step == "crops":
        make_crops(a.day, a.work)
    else:
        score(a.work, a.his_day, a.clip)
