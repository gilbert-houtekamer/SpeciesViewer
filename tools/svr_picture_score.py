"""EXPERIMENT — score one species' master pictures and suggest which are
redundant, as a contact sheet you judge with your own eye. READ-ONLY: nothing
in the master is written; scores are cached under app-data.

The measuring is engine.picture_score, the choosing engine.redundancy (the same
code as the Species Viewer's suggestion dialog); this tool adds the camera's AF
point and the contact sheet. Plan: docs/plans/picture_score_plan.md.

The idea (see the session notes, Nov 2026):

  1. SUBJECT SHARPNESS, not image sharpness. The frame is cut into tiles and
     each tile's detail (Laplacian energy, after a light blur so sensor noise
     does not count as detail) is measured. The score is the mean of the
     sharpest few tiles — where the bird is, if it is sharp. A frame with a
     razor-sharp bird on 10% of the area and soft bokeh elsewhere scores high;
     a frame with everything a bit soft scores low. `isolation` = sharpest tiles
     / median tile: high means a sharp subject on a soft background.
  2. POSES. Frames shot in one go that LOOK alike (a perceptual hash of the
     whole frame) form one pose group. A new pose starts after a time gap or
     when the picture changes enough (the bird turned, flew, the framing moved).
  3. SELECTION per species per DAY (an encounter). Candidates are pictures
     without stars that are Green or have no label; everything else (stars,
     Red, Yellow, a JPG) is your keeper and always stays. Per day: the sharpest
     frame of every pose, best poses first, then second-best frames, until the
     day has --per-day pictures besides your own. Within a pose a frame much softer than its best
     is never kept for its own sake. The rest are suggested PURPLE.

Formats: RAW via SpeciesViewer's preview path (embedded JPEG, else a decode),
camera JPG as is, HEIC / PNG / TIFF through Pillow.

Known limit: a sharp twig in front of a soft bird still scores as "sharp".
The OM System AF point turned out less consistent than the sharpest tiles; it
is drawn in the large view but does not count unless --af says so.

The sheet: click a picture (or Tab + Enter) to see it large (1600 px, the frame
the score measured, written once beside the sheet in <sheet>_files/), with the
camera's AF point, the region searched around it and the sharpest tiles drawn on
top. Keys there: Left/Right, A (AF on/off), T (tiles on/off), Z (full size), Esc.

  python tools/svr_picture_score.py "desert sparrow"
  python tools/svr_picture_score.py "african savanna elephant" --per-day 15 --open
  python tools/svr_picture_score.py woestijnmus --per-pose 1 --cap 20
"""
from __future__ import annotations

import argparse
import base64
import html
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))

import cv2                      # noqa: E402
import numpy as np              # noqa: E402

from engine import redundancy as rd                     # noqa: E402
from engine.picture_score import (Cache, GRID, LONG_EDGE, TOP_SHARE,   # noqa: E402,F401
                                  image_bytes, score_pictures)


# ── the master's pictures of one species ─────────────────────────────────────

def find_species(master: Path, name: str) -> str:
    """Scientific name for a scientific, English or Dutch name ('' if unknown)."""
    import csv
    want = name.strip().lower()
    with open(master / "metadata" / "active" / "species.csv",
              encoding="utf-8-sig", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            if want in {(r.get(c) or "").strip().lower()
                        for c in ("ScientificName", "EnglishName", "DutchName")}:
                return (r.get("ScientificName") or "").strip()
    return ""


def load_species(master: Path, sci: str) -> list[dict]:
    from db.master_db import MasterDB
    db = MasterDB(master)
    db.ensure_fresh()
    pics = rd.from_master_rows(db.pictures_for_species_and_subspecies(sci), master)
    rd.set_floors(pics, rd.day_floors(db, rd.days_of(pics)))   # stars above the day's usual
    return pics


def _resized_jpeg(img, long_edge: int, quality: int) -> bytes:
    h, w = img.shape[:2]
    s = long_edge / max(h, w)
    if s < 1:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return enc.tobytes() if ok else b""


def make_thumb(data: bytes) -> str:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return ""
    return base64.b64encode(_resized_jpeg(img, 260, 80)).decode()


def large_path(views: Path, p) -> Path:
    return views / (re.sub(r"[^\w.-]", "_", p["upn"]) + ".jpg")


def save_large(data: bytes, dest: Path) -> bool:
    """The picture at the size the score measured it (LONG_EDGE), so the tile
    grid and the AF box drawn on it line up with what was scored."""
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    enc = _resized_jpeg(img, LONG_EDGE, 85)
    if not enc:
        return False
    dest.write_bytes(enc)
    return True


def large_is_fresh(dest: Path, src: Path) -> bool:
    try:
        return dest.stat().st_mtime >= src.stat().st_mtime
    except OSError:
        return False


def make_views(pics, views: Path, workers: int = 4):
    """A thumbnail (inline) and a large JPG (beside the sheet) for every picture.
    A large JPG already there and newer than the picture is reused, and the
    thumbnail is made from it — a second run decodes no RAW."""
    views.mkdir(parents=True, exist_ok=True)

    def one(p):
        dest = large_path(views, p)
        if not large_is_fresh(dest, p["src"]):
            data = image_bytes(p["src"])
            if not data or not save_large(data, dest):
                p["thumb"] = make_thumb(data) if data else ""
                return
        if not p.get("thumb"):
            p["thumb"] = make_thumb(dest.read_bytes())
        p["large"] = f"{views.name}/{dest.name}"

    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for i, _ in enumerate(ex.map(one, pics), 1):
            if i % 50 == 0 or i == len(pics):
                print(f"  pictures for the sheet {i}/{len(pics)}  ({time.time() - t0:.0f} s)")


def score_all(pics, cache: Cache, views: Path | None = None):
    """Measure (engine.picture_score); a picture decoded for it also gets its
    large view written, so the sheet never decodes a RAW twice."""
    if views:
        views.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    def progress(i, n):
        if i % 25 == 0 or i == n:
            print(f"  scored {i}/{n}  ({time.time() - t0:.0f} s)", flush=True)

    def decoded(p, data):
        if views:
            save_large(data, large_path(views, p))

    score_pictures(pics, cache, progress=progress,
                   on_decoded=decoded if views else None)
    for p in pics:
        p["generic"] = p["sharp"]


# ── where the camera focused (OM System / Olympus) ───────────────────────────

def read_af(pics):
    """p["af_box"] (fractions of the upright frame) and p["af_found"] — the
    camera's AF point, from ONE batched exiftool read (engine.af_point)."""
    from engine import af_point
    found = af_point.read([p["src"] for p in pics])
    for p in pics:
        p["af_box"], p["af_found"] = af_point.lookup(found, p["src"])


AF_PAD = 0.08                   # the AF score looks this far around the AF box


def _af_region(tiles, box, pad: float = AF_PAD):
    """(r0, r1, c0, c1) tile range searched around the AF box, or None."""
    rows, cols = tiles.shape
    x0, y0, x1, y1 = box
    c0, c1 = max(0, int((x0 - pad) * cols)), min(cols, int(np.ceil((x1 + pad) * cols)))
    r0, r1 = max(0, int((y0 - pad) * rows)), min(rows, int(np.ceil((y1 + pad) * rows)))
    return (r0, r1, c0, c1) if c1 > c0 and r1 > r0 else None


def af_sharp(tiles, box, pad: float = AF_PAD):
    """Sharpness of the sharpest tile around the AF box (padded: the bird is
    bigger than the AF point)."""
    if tiles is None or box is None:
        return None
    reg = _af_region(tiles, box, pad)
    if reg is None:
        return None
    r0, r1, c0, c1 = reg
    return float(tiles[r0:r1, c0:c1].max())


def overlay(p) -> dict:
    """What the large view draws, in fractions of the frame: the camera's AF box,
    the region the AF score searched, the tile it took there, and the sharpest
    tiles the generic score averaged."""
    out = {"af": None, "region": None, "aftile": None, "top": []}
    tiles = p.get("tiles")
    if tiles is None:
        return out
    rows, cols = tiles.shape
    k = max(3, int(tiles.size * TOP_SHARE))
    for i in np.argsort(tiles.ravel())[-k:]:
        out["top"].append([int(i % cols) / cols, int(i // cols) / rows, 1 / cols, 1 / rows])
    box = p.get("af_box")
    if box is not None:
        x0, y0, x1, y1 = (float(v) for v in box)
        out["af"] = [x0, y0, x1 - x0, y1 - y0]
        reg = _af_region(tiles, box)
        if reg:
            r0, r1, c0, c1 = reg
            out["region"] = [c0 / cols, r0 / rows, (c1 - c0) / cols, (r1 - r0) / rows]
            sub = tiles[r0:r1, c0:c1]
            r, c = np.unravel_index(int(np.argmax(sub)), sub.shape)
            out["aftile"] = [(c0 + c) / cols, (r0 + r) / rows, 1 / cols, 1 / rows]
    return out


def apply_af(pics, mode: str):
    """mode 'auto': use the AF score only where the camera says it FOUND the
    subject; 'always': wherever there is an AF box; 'off': generic only."""
    for p in pics:
        p["af"] = af_sharp(p.get("tiles"), p.get("af_box"))
        # An AF point in featureless sky missed the subject (logged before the
        # bird moved, or tracking slipped) — it says nothing about sharpness.
        missed = p["af"] is not None and p["af"] < 0.25 * (p["generic"] or 1e-9)
        use = p["af"] is not None and not missed and (
            mode == "always" or (mode == "auto" and p.get("af_found")))
        p["sharp"] = p["af"] if use else p["generic"]
        p["af_used"] = bool(use)


# ── contact sheet ────────────────────────────────────────────────────────────

def thumb_b64(p) -> str:
    if p.get("thumb"):
        return p["thumb"]
    data = image_bytes(p["src"])
    return make_thumb(data) if data else ""


def af_note(p, top) -> str:
    if p.get("af") is None:
        return ""
    found = {True: "bird found", False: "nothing found", None: ""}[p.get("af_found")]
    mark = " (used)" if p.get("af_used") else ""
    return (f"<br>AF {p['af'] / top:.0%} vs generic {p['generic'] / top:.0%}"
            f" · {found}{mark}")


def write_sheet(out: Path, name: str, pics, groups, args):
    import json
    top = max((max(p["generic"], p.get("af") or 0) for p in pics), default=1) or 1
    n = {v: sum(1 for p in pics if p["verdict"] == v)
         for v in (rd.KEEPER, rd.DECIDED, rd.KEEP, rd.PURPLE)}
    freed = sum(p["bytes"] for p in pics if p["verdict"] == rd.PURPLE)
    words = {rd.KEEPER: "your keeper", rd.DECIDED: "already Blue/Purple", rd.KEEP: "keep",
             rd.PURPLE: "suggest purple"}
    css = """body{font:13px system-ui;margin:16px;background:#f6f5f1;color:#222}
    h1{font-size:18px} .pose{margin:10px 0;padding:6px;background:#fff;border-radius:6px}
    .pose h3{font-size:12px;margin:0 0 6px;color:#555;font-weight:600}
    .row{display:flex;flex-wrap:wrap;gap:6px} .c{width:264px;font-size:11px}
    .c button{all:unset;cursor:zoom-in;display:block}
    .c button:focus-visible img{outline:3px solid #f9a825;outline-offset:1px}
    .c img{width:260px;border:4px solid #ccc;display:block}
    .keep img{border-color:#2e7d32} .keeper img{border-color:#1565c0} .decided img{border-color:#555;opacity:.4}
    .purple img{border-color:#8e24aa;opacity:.55} .bar{height:4px;background:#2e7d32}
    .k{display:inline-block;padding:1px 6px;border-radius:3px;color:#fff;margin-right:6px}
    #lb{position:fixed;inset:0;background:#111;color:#eee;display:none;flex-direction:column;z-index:9}
    #lb.on{display:flex} #lbbar{padding:8px 12px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
    #lbbar button{background:#333;color:#eee;border:1px solid #555;border-radius:4px;padding:4px 10px;font:inherit;cursor:pointer}
    #lbbar button.off{opacity:.45} #lbcap{flex:1;min-width:300px}
    #lbwrap{flex:1;overflow:hidden;display:flex;align-items:center;justify-content:center}
    #lbwrap.full{overflow:auto;display:block}
    #stage{position:relative;flex:none} #stage img{width:100%;height:100%;display:block}
    #stage svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
    .sw{display:inline-block;width:12px;height:12px;vertical-align:-2px;margin-right:4px}"""
    legend = ("<span><i class=sw style='border:2px solid #ffeb3b'></i>camera AF point</span>"
              "<span><i class=sw style='border:2px dashed #ffeb3b'></i>searched around it</span>"
              "<span><i class=sw style='border:2px solid #ff6d00'></i>its sharpest tile</span>"
              "<span><i class=sw style='border:2px solid #00e5ff'></i>sharpest tiles "
              "(generic score)</span>")
    parts = [f"<!doctype html><meta charset=utf-8><title>Score: {html.escape(name)}</title>"
             f"<style>{css}</style><h1>{html.escape(name)} — {len(pics)} pictures, "
             f"{len(groups)} poses</h1><p>"
             f"<span class=k style=background:#1565c0>your keepers {n[rd.KEEPER]}</span>"
             f"<span class=k style=background:#555>already Blue/Purple {n[rd.DECIDED]}</span>"
             f"<span class=k style=background:#2e7d32>keep {n[rd.KEEP]}</span>"
             f"<span class=k style=background:#8e24aa>suggest purple {n[rd.PURPLE]}</span>"
             f" — frees {freed / 1e9:.2f} GB. Settings: per day {args.per_day}, "
             f"species cap {args.cap or 'none'}, per pose "
             f"{args.per_pose}, new pose after {args.gap:.0f} s or {args.change:.0%} change, "
             f"candidates: no stars and Green or no label. AF: {args.af}.</p>"
             "<p>Bar = subject sharpness relative to the species' sharpest frame. "
             "iso = how much sharper the subject is than the rest of the frame. "
             "Click a picture (or Tab to it and press Enter) to see it large with the "
             "AF point and the tiles the score looked at.</p>"]
    data = []
    for g in groups:
        t0, t1 = g[0]["time"], g[-1]["time"]
        span = (f"{t0:%Y-%m-%d %H:%M:%S}" + (f" – {t1:%H:%M:%S}" if t1 and t1 != t0 else "")
                if t0 else "no date")
        parts.append(f"<div class=pose><h3>{span} · {len(g)} frame(s)</h3><div class=row>")
        for p in g:
            pct = p["sharp"] / top
            info = (f"{html.escape(p['src'].name)}<br>sharp {pct:.0%} · iso {p['isolation']:.1f}"
                    f"{af_note(p, top)}"
                    f" · {p['label'] or 'no label'}{' · ' + '★' * p['stars'] if p['stars'] else ''}"
                    f"{' · JPG' if p['jpg'] else ''}")
            i = len(data)
            d = {"src": p.get("large") or "", "verdict": p["verdict"],
                 "cap": f"<b>{words[p['verdict']]}</b> · {span} · {info.replace('<br>', ' · ')}"}
            d.update(overlay(p))
            data.append(d)
            img = f"<img alt='' src='data:image/jpeg;base64,{thumb_b64(p)}'>"
            if d["src"]:
                img = f"<button data-i={i} aria-label='Open large'>{img}</button>"
            parts.append(
                f"<div class='c {p['verdict']}'>{img}"
                f"<div class=bar style='width:{max(2, pct * 260):.0f}px'></div>{info}</div>")
        parts.append("</div></div>")
    parts.append(
        "<div id=lb role=dialog aria-modal=true><div id=lbbar>"
        "<button id=bprev title='Previous (Left arrow)'>◀</button>"
        "<button id=bnext title='Next (Right arrow)'>▶</button>"
        "<button id=baf title='AF point on/off (A)'>AF (A)</button>"
        "<button id=btiles title='Sharpest tiles on/off (T)'>Tiles (T)</button>"
        "<button id=bzoom title='Fit / full size (Z)'>Full size (Z)</button>"
        f"<span id=lbcap></span>{legend}"
        "<button id=bclose title='Close (Esc)'>Close (Esc)</button></div>"
        "<div id=lbwrap><div id=stage><img id=lbimg alt=''><svg id=lbsvg viewBox='0 0 1 1' "
        "preserveAspectRatio=none></svg></div></div></div>")
    js = """
const D = %s; let cur = -1, showAF = true, showT = true, full = false;
const $ = id => document.getElementById(id);
const lb = $('lb'), img = $('lbimg'), svg = $('lbsvg'), stage = $('stage'), wrap = $('lbwrap');
function rect(r, colour, dash, w) {
  return r ? `<rect x="${r[0]}" y="${r[1]}" width="${r[2]}" height="${r[3]}" fill="none"
    stroke="${colour}" stroke-width="${w}" ${dash ? 'stroke-dasharray="6 4"' : ''}
    vector-effect="non-scaling-stroke"/>` : '';
}
function draw() {
  const d = D[cur]; let s = '';
  if (showT) s += d.top.map(r => rect(r, '#00e5ff', false, 2)).join('');
  if (showAF) s += rect(d.region, '#ffeb3b', true, 2) + rect(d.aftile, '#ff6d00', false, 3)
                 + rect(d.af, '#ffeb3b', false, 3);
  svg.innerHTML = s;
  $('baf').classList.toggle('off', !showAF); $('btiles').classList.toggle('off', !showT);
  $('baf').disabled = !d.af;
}
function layout() {
  const w = img.naturalWidth, h = img.naturalHeight; if (!w) return;
  let s = 1;
  if (!full) s = Math.min(wrap.clientWidth / w, wrap.clientHeight / h);
  stage.style.width = (w * s) + 'px'; stage.style.height = (h * s) + 'px';
  wrap.classList.toggle('full', full);
  $('bzoom').textContent = full ? 'Fit (Z)' : 'Full size (Z)';
  if (full) {                       // centre on the AF point, else the sharpest tile
    const d = D[cur], r = d.af || d.top[d.top.length - 1];
    if (r) { wrap.scrollLeft = (r[0] + r[2] / 2) * w - wrap.clientWidth / 2;
             wrap.scrollTop = (r[1] + r[3] / 2) * h - wrap.clientHeight / 2; }
  }
}
function show(i) {
  if (i < 0 || i >= D.length || !D[i].src) return;
  cur = i; $('lbcap').innerHTML = D[i].cap + ` · ${i + 1}/${D.length}`;
  img.onload = layout; img.src = D[i].src; draw();
  lb.classList.add('on'); $('bclose').focus();
}
function step(k) { let i = cur + k; while (i >= 0 && i < D.length && !D[i].src) i += k; show(i); }
function close() {
  lb.classList.remove('on');
  const b = document.querySelector(`button[data-i="${cur}"]`);
  if (b) { b.focus(); b.scrollIntoView({block: 'center'}); }
}
document.querySelectorAll('button[data-i]').forEach(b => b.onclick = () => show(+b.dataset.i));
$('bprev').onclick = () => step(-1); $('bnext').onclick = () => step(1);
$('baf').onclick = () => { showAF = !showAF; draw(); };
$('btiles').onclick = () => { showT = !showT; draw(); };
$('bzoom').onclick = () => { full = !full; layout(); };
$('bclose').onclick = close;
addEventListener('resize', layout);
addEventListener('keydown', e => {
  if (!lb.classList.contains('on')) return;
  const k = e.key.toLowerCase();
  if (k === 'escape') close();
  else if (k === 'arrowright' || k === ' ') step(1);
  else if (k === 'arrowleft') step(-1);
  else if (k === 'a') { showAF = !showAF; draw(); }
  else if (k === 't') { showT = !showT; draw(); }
  else if (k === 'z') { full = !full; layout(); }
  else return;
  e.preventDefault();
});
""" % json.dumps(data)
    parts.append(f"<script>{js}</script>")
    out.write_text("".join(parts), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("species", help="scientific, English or Dutch name")
    ap.add_argument("--master", help="master folder (default: the configured one)")
    ap.add_argument("--per-day", type=int, default=0,
                    help="pictures kept per day besides your own keepers (default: by how "
                         "often you saw the species — 20 for one day, 3 for 67 days)")
    ap.add_argument("--cap", type=int, default=0,
                    help="also a limit for the whole species (default: none)")
    ap.add_argument("--per-pose", type=int, default=2, help="frames kept per pose (default 2)")
    ap.add_argument("--gap", type=float, default=30, help="seconds that start a new pose (default 30)")
    ap.add_argument("--change", type=float, default=0.25,
                    help="how different a frame must look to start a new pose, 0..1 (default 0.25)")
    ap.add_argument("--soft", type=float, default=0.6,
                    help="a frame below this share of its pose's best is never kept (default 0.6)")
    ap.add_argument("--af", choices=("auto", "always", "off"), default="off",
                    help="let the camera's AF area count in the score: off (default; it "
                         "is drawn in the large view anyway), auto = where it found the "
                         "subject, always")
    ap.add_argument("--open", action="store_true", help="open the sheet in the browser")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    master = a.master
    if not master:
        from utils.config import _config
        master = _config.get("master", "masterbiotadatabase", fallback="")
    master = Path(master.strip().strip('"'))
    sci = find_species(master, a.species)
    pics = load_species(master, sci) if sci else []
    if not pics:
        print(f"No master pictures found for “{a.species}”.")
        return 1
    print(f"{len(pics)} pictures of {a.species}.")
    from utils.cache_paths import app_data_dir
    cache = Cache()
    out = app_data_dir() / "reports" / f"score_{re.sub(r'[^\w.-]', '_', a.species)}.html"
    views = out.parent / (out.stem + "_files")     # the large pictures, beside the sheet
    score_all(pics, cache, views)
    read_af(pics)
    apply_af(pics, a.af)
    days = len(rd.days_of(pics))
    a.per_day = a.per_day or rd.per_day_for(days)
    print(f"Seen on {days} day(s): keeping up to {a.per_day} per day besides your keepers.")
    groups = rd.select(pics, rd.Settings(per_day=a.per_day, per_pose=a.per_pose, gap=a.gap,
                                         change=a.change, soft=a.soft, cap=a.cap))
    print("Building the contact sheet …")
    make_views(pics, views)
    write_sheet(out, a.species, pics, groups, a)
    print(f"Sheet: {out}")
    if a.open:
        os.startfile(out) if sys.platform == "win32" else None
    return 0


if __name__ == "__main__":
    sys.exit(main())
