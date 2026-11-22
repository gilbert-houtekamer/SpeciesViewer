"""EXPERIMENT — score one species' master pictures and suggest which are
redundant, as a contact sheet you judge with your own eye. READ-ONLY: nothing
in the master is written; scores are cached under app-data.

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
The OM System AF point helps where the camera found the subject (--af).

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
import csv
import html
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))

import cv2                      # noqa: E402
import numpy as np              # noqa: E402

SCORE_VERSION = 3
LONG_EDGE = 1600                # analyse at this size (embedded previews are larger)
GRID = (16, 12)                 # tiles across × down
TOP_SHARE = 0.03                # "the subject" = the sharpest 3% of tiles (≥ 3)
csv.field_size_limit(10 ** 8)


# ── scoring one picture ──────────────────────────────────────────────────────

def score_image(jpeg: bytes) -> dict | None:
    buf = np.frombuffer(jpeg, np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape
    s = LONG_EDGE / max(h, w)
    if s < 1:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    f = img.astype(np.float32) / 255.0
    lap = cv2.Laplacian(cv2.GaussianBlur(f, (0, 0), 1.0), cv2.CV_32F, ksize=3)
    energy = lap * lap
    gx, gy = GRID
    th, tw = energy.shape[0] // gy, energy.shape[1] // gx
    grid = energy[:th * gy, :tw * gx].reshape(gy, th, gx, tw).mean(axis=(1, 3))
    tiles = np.sort(grid.ravel())
    k = max(3, int(len(tiles) * TOP_SHARE))
    top = float(tiles[-k:].mean())
    med = float(np.median(tiles))
    clipped = float(((img >= 250) | (img <= 5)).mean())
    # 8×8 difference hash of a BLURRED frame — the layout (where the bird is,
    # its shape), not the texture: a 16×16 hash of the sharp frame saw sand grains
    # and a 1-second camera twitch as a "new picture".
    coarse = cv2.GaussianBlur(img, (0, 0), max(img.shape) / 150)
    small = cv2.resize(coarse, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    dhash = int("".join("1" if b else "0" for b in bits), 2)
    return {"sharp": top * 1e4, "isolation": top / (med + 1e-9),
            "clipped": clipped, "dhash": f"{dhash:016x}",
            "tiles": (grid * 1e4).astype(np.float32)}     # rows x cols, upright frame


def hamming(a: str, b: str) -> float:
    return bin(int(a, 16) ^ int(b, 16)).count("1") / 64.0


# ── cache (app-data, never the master) ───────────────────────────────────────

class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()     # one connection, many scoring threads
        self.db.execute("create table if not exists score3 (path text primary key, "
                        "mtime integer, size integer, version integer, sharp real, "
                        "isolation real, clipped real, dhash text, tiles blob)")

    def get(self, p: Path):
        st = p.stat()
        with self.lock:
            r = self.db.execute("select mtime,size,version,sharp,isolation,clipped,dhash,"
                                "tiles from score3 where path=?", (str(p),)).fetchone()
        if r and r[0] == st.st_mtime_ns and r[1] == st.st_size and r[2] == SCORE_VERSION:
            d = dict(zip(("sharp", "isolation", "clipped", "dhash"), r[3:7]))
            d["tiles"] = np.frombuffer(r[7], np.float32).reshape(GRID[1], GRID[0])
            return d
        return None

    def put(self, p: Path, sc: dict):
        st = p.stat()
        with self.lock:
            self.db.execute("insert or replace into score3 values (?,?,?,?,?,?,?,?,?)",
                            (str(p), st.st_mtime_ns, st.st_size, SCORE_VERSION,
                             sc["sharp"], sc["isolation"], sc["clipped"], sc["dhash"],
                             np.ascontiguousarray(sc["tiles"], np.float32).tobytes()))
            self.db.commit()


# ── the master's pictures of one species ─────────────────────────────────────

def load_species(master: Path, name: str) -> list[dict]:
    from utils.raw_extensions import is_raw_or_heic, is_jpeg
    want = name.strip().lower()
    out = []
    with open(master / "metadata" / "active" / "data_pictures.csv",
              encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            if want not in {(r.get(c) or "").strip().lower()
                            for c in ("WetSoortNaam", "EngSoortNaam", "NLSoortNaam")}:
                continue
            folder = master / (r.get("TargetPath") or "")
            try:
                files = eval(r.get("Files") or "[]", {"__builtins__": {}})   # a list literal
            except Exception:
                files = []
            paths = [folder / f for f in files]
            src = next((p for p in paths if is_raw_or_heic(p) and p.exists()), None) \
                or next((p for p in paths if is_jpeg(p) and p.exists()), None)
            if src is None:
                continue
            try:
                t = datetime.strptime((r.get("DateTimeOriginal") or "")[:19], "%Y:%m:%d %H:%M:%S")
            except ValueError:
                t = None
            label = (r.get("Label") or "").strip()
            stars = int(float(r.get("Rating") or 0) or 0)
            out.append({
                "upn": r["UniquePictureName"], "src": src, "time": t,
                "label": label, "stars": stars,
                "jpg": bool((r.get("BestJPG") or "").strip()),
                "bytes": sum(p.stat().st_size for p in paths if p.exists()),
            })
    out.sort(key=lambda p: (p["time"] or datetime.min, p["upn"]))
    return out


def image_bytes(src: Path) -> bytes | None:
    """A JPEG of the picture, whatever the file: a camera JPG as it is, a HEIC
    (iPhone) / PNG / TIFF converted, a RAW through SpeciesViewer's own preview
    path (the camera's embedded JPEG, else a decode)."""
    from utils.raw_extensions import is_jpeg, is_raw
    try:
        if is_jpeg(src):
            return src.read_bytes()
        if not is_raw(src):
            import io
            import pillow_heif
            from PIL import Image, ImageOps
            pillow_heif.register_heif_opener()
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=90)
                return buf.getvalue()
        from gui.preview_extract import preview_jpeg_bytes
        return preview_jpeg_bytes(src)
    except Exception as e:
        print(f"  could not read {src.name}: {e}")
        return None


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


def score_all(pics, cache: Cache, views: Path | None = None, workers: int = 4):
    if views:
        views.mkdir(parents=True, exist_ok=True)

    def one(p):
        sc = cache.get(p["src"])
        if sc is None:
            data = image_bytes(p["src"])
            if data and views:                    # the sheet needs it; read once
                save_large(data, large_path(views, p))
            sc = score_image(data) if data else None
            if sc:
                cache.put(p["src"], sc)
        p.update(sc or {"sharp": 0.0, "isolation": 0.0, "clipped": 0.0, "dhash": "0" * 16,
                        "tiles": None})
        p["generic"] = p["sharp"]
        return p

    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for i, _ in enumerate(ex.map(one, pics), 1):
            if i % 25 == 0 or i == len(pics):
                print(f"  scored {i}/{len(pics)}  ({time.time() - t0:.0f} s)")


# ── where the camera focused (OM System / Olympus) ───────────────────────────

_BOX = re.compile(r"\((\d+),(\d+)\)-\((\d+),(\d+)\)")
_PCT = re.compile(r"\((\d+)%,(\d+)%\)")


def _upright(box, orientation):
    """AF coordinates are on the sensor (landscape); the preview is turned upright."""
    x0, y0, x1, y1 = box
    if orientation == 6:                     # turned 90 degrees clockwise to view
        return (1 - y1, x0, 1 - y0, x1)
    if orientation == 8:                     # turned 270 degrees clockwise
        return (y0, 1 - x1, y1, 1 - x0)
    if orientation == 3:
        return (1 - x1, 1 - y1, 1 - x0, 1 - y0)
    return box


def read_af(pics):
    """Add p["af_box"] (fractions of the upright frame) and p["af_found"] (the
    camera's subject detection found something) from ONE batched exiftool read.
    Only OM System / Olympus ORFs record a usable box; others get None."""
    import json
    from engine.exif_writer import run_exiftool_files_parallel
    for p in pics:
        p["af_box"], p["af_found"] = None, None
    raws = [p for p in pics if p["src"].suffix.lower() == ".orf"]
    if not raws:
        return
    by = {os.path.normcase(os.path.normpath(str(p["src"]))): p for p in raws}
    res = run_exiftool_files_parallel(
        ["-j", "-AFAreas", "-AFPointSelected", "-AISubjectTrackingMode", "-Orientation#"],
        [p["src"] for p in raws])
    for cp in res:
        try:
            rows = json.loads(cp.stdout or "[]")
        except ValueError:
            continue
        for r in rows:
            p = by.get(os.path.normcase(os.path.normpath(r.get("SourceFile", ""))))
            if p is None:
                continue
            # AFPointSelected is where the camera actually focused — it MOVES with
            # the tracked subject. AFAreas is only the target frame you set (on the
            # OM-1 the same centre box in every frame), so it is the fallback.
            box = None
            m = _PCT.search(str(r.get("AFPointSelected", "")))
            if m:
                x, y = int(m.group(1)) / 100, int(m.group(2)) / 100
                box = (x - 0.02, y - 0.02, x + 0.02, y + 0.02)
            else:
                boxes = [tuple(int(v) / 255 for v in b)
                         for b in _BOX.findall(str(r.get("AFAreas", "")))]
                if boxes:
                    box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                           max(b[2] for b in boxes), max(b[3] for b in boxes))
            if box:
                p["af_box"] = _upright(box, int(r.get("Orientation") or 1))
            track = str(r.get("AISubjectTrackingMode", ""))
            p["af_found"] = ("Object Found" in track) if track else None


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


# ── poses + selection ────────────────────────────────────────────────────────

def poses(pics, gap_s: float, change: float) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for p in pics:
        g = groups[-1] if groups else None
        if (g and p["time"] and g[-1]["time"]
                and (p["time"] - g[-1]["time"]).total_seconds() <= gap_s
                and hamming(p["dhash"], g[-1]["dhash"]) <= change):
            g.append(p)
        else:
            groups.append([p])
    return groups


def is_keeper(p) -> bool:
    """What you already chose to keep. A candidate is a picture WITHOUT stars
    that is Green or has no label (Green was the old 'not deleted' default)."""
    return p["jpg"] or p["stars"] >= 1 or p["label"] in ("Red", "Yellow")


def select(groups, per_day: int, cap: int, per_pose: int, soft: float):
    """Per DAY (an encounter), not per species: a species met on 19 days keeps
    more than one met once. Within a day: the sharpest frame of each pose, best
    poses first, then the second-best frames, until the day has `per_day`
    pictures ON TOP OF your own keepers (those always stay and do not use up
    the allowance). `cap` > 0 adds a limit for the whole species."""
    for g in groups:
        for p in g:
            p["verdict"] = "protected" if is_keeper(p) else "purple"
    by_day: dict = {}
    for g in groups:
        by_day.setdefault(g[0]["time"].date() if g[0]["time"] else None, []).append(g)
    total = 0
    for day_groups in by_day.values():
        kept = 0
        ranked = [sorted(g, key=lambda p: p["sharp"], reverse=True) for g in day_groups]
        ranked.sort(key=lambda g: g[0]["sharp"], reverse=True)
        for rnd in range(per_pose):
            for g in ranked:
                if kept >= per_day or (cap and total >= cap) or rnd >= len(g):
                    continue
                p, best = g[rnd], g[0]["sharp"] or 1e-9
                already = sum(1 for q in g if q["verdict"] != "purple")
                if p["verdict"] == "purple" and already < per_pose and p["sharp"] >= soft * best:
                    p["verdict"] = "keep"
                    kept += 1
                    total += 1


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
    n = {v: sum(1 for p in pics if p["verdict"] == v) for v in ("protected", "keep", "purple")}
    freed = sum(p["bytes"] for p in pics if p["verdict"] == "purple")
    words = {"protected": "already a keeper", "keep": "keep", "purple": "suggest purple"}
    css = """body{font:13px system-ui;margin:16px;background:#f6f5f1;color:#222}
    h1{font-size:18px} .pose{margin:10px 0;padding:6px;background:#fff;border-radius:6px}
    .pose h3{font-size:12px;margin:0 0 6px;color:#555;font-weight:600}
    .row{display:flex;flex-wrap:wrap;gap:6px} .c{width:264px;font-size:11px}
    .c button{all:unset;cursor:zoom-in;display:block}
    .c button:focus-visible img{outline:3px solid #f9a825;outline-offset:1px}
    .c img{width:260px;border:4px solid #ccc;display:block}
    .keep img{border-color:#2e7d32} .protected img{border-color:#1565c0}
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
             f"<span class=k style=background:#1565c0>already a keeper {n['protected']}</span>"
             f"<span class=k style=background:#2e7d32>keep {n['keep']}</span>"
             f"<span class=k style=background:#8e24aa>suggest purple {n['purple']}</span>"
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
    ap.add_argument("--per-day", type=int, default=10,
                    help="pictures kept per species per day, besides your own keepers (default 10)")
    ap.add_argument("--cap", type=int, default=0,
                    help="also a limit for the whole species (default: none)")
    ap.add_argument("--per-pose", type=int, default=2, help="frames kept per pose (default 2)")
    ap.add_argument("--gap", type=float, default=30, help="seconds that start a new pose (default 30)")
    ap.add_argument("--change", type=float, default=0.25,
                    help="how different a frame must look to start a new pose, 0..1 (default 0.25)")
    ap.add_argument("--soft", type=float, default=0.6,
                    help="a frame below this share of its pose's best is never kept (default 0.6)")
    ap.add_argument("--af", choices=("auto", "always", "off"), default="auto",
                    help="use the camera's AF area: auto = only where it found the "
                         "subject (default), always, or off")
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
    pics = load_species(master, a.species)
    if not pics:
        print(f"No master pictures found for “{a.species}”.")
        return 1
    print(f"{len(pics)} pictures of {a.species}.")
    from utils.cache_paths import app_data_dir
    cache = Cache(app_data_dir() / "scores" / "picture_scores.sqlite")
    out = app_data_dir() / "reports" / f"score_{re.sub(r'[^\w.-]', '_', a.species)}.html"
    views = out.parent / (out.stem + "_files")     # the large pictures, beside the sheet
    score_all(pics, cache, views)
    read_af(pics)
    apply_af(pics, a.af)
    groups = poses(pics, a.gap, a.change)
    select(groups, a.per_day, a.cap, a.per_pose, a.soft)
    print("Building the contact sheet …")
    make_views(pics, views)
    write_sheet(out, a.species, pics, groups, a)
    print(f"Sheet: {out}")
    if a.open:
        os.startfile(out) if sys.platform == "win32" else None
    return 0


if __name__ == "__main__":
    sys.exit(main())
