"""EXPERIMENT (step 1 of docs/plans/picture_score_plan.md's AI roadmap) — find the
animal in every master picture: a BOX, from MegaDetector.

ONE JOB: for each master picture, the boxes MegaDetector finds (animal / person /
vehicle) with their confidence, in fractions of the UPRIGHT frame, cached in
app-data (`scores/bird_boxes.sqlite`, keyed on path + size + mtime + model).
READ-ONLY for the master. Nothing in SpeciesViewer uses the boxes yet: the
comparison (tools/svr_box_agreement.py) reads them to test whether sharpness
INSIDE the box agrees with your own choices more often than the sharpest tiles.

Runs in the separate experiment environment (PyTorch + MegaDetector), not in
SpeciesViewer's own:
  %LOCALAPPDATA%\\SpeciesViewer\\ml\\Scripts\\python.exe tools/ml/svr_bird_box.py
  ... --limit 200          # a quick try on the first 200 pictures
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SV = str(Path(__file__).resolve().parents[2] / "speciesviewer")
# NOT on sys.path yet: SpeciesViewer's top-level package `utils` would shadow the
# `utils` package of MegaDetector's YOLOv5 code. main() loads the detector first,
# then puts SpeciesViewer's code on the path.

MODEL = "MDV5A"
DETECT_EDGE = 1280            # MegaDetector v5 works at 1280 px
csv.field_size_limit(10 ** 9)


def cache_path() -> Path:
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "scores" / "bird_boxes.sqlite"


class BoxCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("create table if not exists boxes (path text primary key, "
                        "mtime integer, size integer, model text, boxes text)")

    def has(self, p: Path) -> bool:
        st = p.stat()
        with self.lock:
            r = self.db.execute("select mtime,size,model from boxes where path=?",
                                (str(p),)).fetchone()
        return bool(r and r[0] == st.st_mtime_ns and r[1] == st.st_size and r[2] == MODEL)

    def put(self, p: Path, boxes: list):
        st = p.stat()
        with self.lock:
            self.db.execute("insert or replace into boxes values (?,?,?,?,?)",
                            (str(p), st.st_mtime_ns, st.st_size, MODEL, json.dumps(boxes)))
            self.db.commit()


def master_sources(master: Path) -> list[Path]:
    """The source file of every master picture (RAW/HEIC, else a JPG) — the same
    choice as engine.redundancy.from_master_rows, so the paths match the scores."""
    from utils.raw_extensions import is_jpeg, is_raw_or_heic
    out = []
    with open(master / "metadata" / "active" / "data_pictures.csv",
              encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            try:
                files = ast.literal_eval(r.get("Files") or "[]")
            except (ValueError, SyntaxError):
                continue
            folder = master / (r.get("TargetPath") or "")
            paths = [folder / f for f in files]
            src = next((p for p in paths if is_raw_or_heic(p) and p.exists()), None) \
                or next((p for p in paths if is_jpeg(p) and p.exists()), None)
            if src:
                out.append(src)
    return out


def upright_image(src: Path):
    """PIL image of the picture, upright (like the score's own decode), at
    DETECT_EDGE on its long side."""
    from PIL import Image, ImageOps
    from engine.picture_score import image_bytes
    data = image_bytes(src)
    if not data:
        return None
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((DETECT_EDGE, DETECT_EDGE))
    return im


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", help="master folder (default: the configured one)")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    t0 = time.time()
    from megadetector.detection.run_detector import load_detector
    import torch
    print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'}",
          flush=True)
    det = load_detector(MODEL)
    # The detector holds its own functions now; SpeciesViewer's `utils` may come.
    for m in [m for m in sys.modules if m == "utils" or m.startswith("utils.")]:
        del sys.modules[m]
    sys.path.insert(0, SV)

    master = a.master
    if not master:
        from utils.config import _config
        master = _config.get("master", "masterbiotadatabase", fallback="")
    master = Path(master.strip().strip('"'))

    cache = BoxCache(cache_path())
    srcs = master_sources(master)
    if a.limit:
        srcs = srcs[:a.limit]
    todo = [p for p in srcs if not cache.has(p)]
    print(f"{len(srcs)} pictures, {len(todo)} still to do ({time.time() - t0:.0f} s)", flush=True)

    # Decoding (CPU, threads) feeds the detector (GPU, this thread).
    done = 0
    with ThreadPoolExecutor(6) as ex:
        for src, im in zip(todo, ex.map(upright_image, todo)):
            boxes = []
            if im is not None:
                try:
                    res = det.generate_detections_one_image(im, str(src),
                                                            detection_threshold=0.1)
                    for d in res.get("detections") or []:
                        x, y, w, h = d["bbox"]
                        boxes.append({"cat": {"1": "animal", "2": "person", "3": "vehicle"}
                                      .get(str(d["category"]), str(d["category"])),
                                      "conf": round(float(d["conf"]), 3),
                                      "box": [round(x, 4), round(y, 4),
                                              round(x + w, 4), round(y + h, 4)]})
                except Exception as e:
                    print(f"  {src.name}: {e}", flush=True)
            cache.put(src, boxes)
            done += 1
            if done % 100 == 0 or done == len(todo):
                el = time.time() - t0
                print(f"  {done}/{len(todo)}  ({el / 60:.0f} min, "
                      f"{done / max(el, 1):.1f} pictures/s)", flush=True)
    print(f"done ({(time.time() - t0) / 60:.0f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
