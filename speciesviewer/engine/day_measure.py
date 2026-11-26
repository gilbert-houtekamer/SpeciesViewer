"""Measure a batch of pictures for the AI — the heavy part of AI…, on its own.

ONE JOB: for a list of picture dicts of ONE day (in time order) measure what
the AI needs: sharpness per picture (engine.picture_score), the camera's AF
point, the animal in each picture (detected, or followed from the frame before:
engine.day_ai.find_animals), and BioCLIP's vector of the frame each pose is
named from (or, with plants, the whole picture of a pose without an animal).
With names=True those vectors are also turned into names for the region.

Two callers: the culling grid's AI… window (names=True, then decide) and the
card read (names=False: the country is often not known yet while copying — the
tracklog pass comes after — and names are cheap later, from the stored vectors).

INVARIANT: read-only for the pictures. Everything lands in the app-data caches
(engine.picture_score.Cache, engine.ai_measure.AiCache), keyed on path + size +
mtime — so what the card read measured, AI… finds again without looking.
A batch may be part of a day (a long day handed over in pieces): poses cut at
the edge cost at most one extra detector run.
"""
from __future__ import annotations

import io
import logging
import time
from pathlib import Path

logger = logging.getLogger("speciesviewer")


def load_upright(src):
    """The upright RGB picture (the camera's embedded JPEG for a RAW)."""
    from PIL import Image, ImageOps
    from engine.picture_score import image_bytes
    data = image_bytes(src)
    return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")


def measure_day(pics: list[dict], region: str = "", master_root=None, *,
                names: bool = True, aggressive: bool = False,
                progress=None, stopped=lambda: False, clock=None,
                timing: list | None = None) -> dict:
    """Measure `pics` (dicts with src, time, …; sorted by time) in place.
    progress(what, i, n); stopped() → True stops early (returns what is done).
    `timing` gets [(step, seconds)]; `clock` (day_ai.Stopwatch) the detail.
    Returns the animal counts {"detected", "followed", "cached"}."""
    from engine import af_point, ai_measure, ai_models, ai_yardstick, day_ai
    from engine.picture_score import Cache, score_pictures
    progress = progress or (lambda what, i, n: None)
    clock = clock or day_ai.Stopwatch()
    timing = timing if timing is not None else []
    n = len(pics)
    t = [time.time()]

    def took(step):
        now = time.time()
        timing.append((step, now - t[0]))
        t[0] = now

    cache = Cache()
    try:
        score_pictures(pics, cache, cancelled=stopped,
                       progress=lambda i, k: progress("Measuring sharpness", i, k))
    finally:
        cache.close()
    if stopped():
        return {}
    took("sharpness")
    progress("Reading the cameras", 0, n)
    af = af_point.read([p["src"] for p in pics])
    took("cameras")
    progress("Getting the yardstick (your master pictures)", 0, n)
    yard = ai_yardstick.get(master_root)
    took("yardstick")
    aic = ai_measure.AiCache()
    count: dict = {}
    try:
        def measure_one(p, im, box):
            info = af_point.lookup_info(af, p["src"])
            try:
                p.update(ai_measure.measure(
                    p["src"], im if im is not None else (lambda s=p["src"]: load_upright(s)),
                    p, info.get("model", ""),
                    native=info.get("native") or 0, cache=aic,
                    region=region, master_root=master_root or None,
                    yardstick=yard, name=False, box=box))
            except Exception as e:
                logger.warning(f"Day AI: {p['src'].name}: {e}")
                p.update({"box": None, "names": [], "px": 0, "sharp_pct": 0.0})
        with clock.part("starting the detector"):
            ai_models.detector()
        count = day_ai.find_animals(
            pics, measure_one, load_upright, aic,
            progress=lambda i, k: progress("Looking for the animals", i, k),
            stopped=stopped, join=aggressive, clock=clock)
        if stopped():
            return count
        logger.info(f"Day AI: animals detected {count['detected']}, followed "
                    f"{count['followed']}, from earlier {count['cached']}")
        took("animals")
        with clock.part("starting the names (BioCLIP, eBird's list)" if names
                        else "starting BioCLIP"):
            if names:
                ai_measure.prepare_names(region)
            else:
                ai_models.bioclip()
        _name_animals(pics, aic, region, master_root, names, aggressive,
                      progress, stopped, clock)
        if stopped():
            return count
        if not names or ai_measure.still_groups(region):
            _name_plants(pics, aic, region, master_root, names, progress, stopped, clock)
    finally:
        aic.close()
    took("names" if names else "vectors")
    return count


def _name_animals(pics, aic, region, master_root, names, aggressive,
                  progress, stopped, clock):
    """One name per pose: only the frame whose name the pose gets. One name per
    series: poses that show the same animal (followed from one into the next)
    share their best frame's name. names=False: only the vector is stored."""
    from engine import ai_measure, day_ai
    series = (day_ai.name_series(pics) if aggressive else
              [[p] for p in day_ai.pose_leaders(pics)])
    firsts = [x[0] for x in series]
    ahead = day_ai.Prefetch([p["src"] for p in firsts
                             if aic.vector(p["src"], p["box"]) is None], load_upright)
    try:
        for i, x in enumerate(series, 1):
            if stopped():
                return
            p = x[0]

            def picture(src=p["src"]):
                with clock.part("waiting for a decoded picture (names)"):
                    return ahead.get(src)
            part = ("first name" if i == 1 else
                    "a name" if aic.vector(p["src"], p["box"]) is None else
                    "a name, picture seen before")
            try:
                with clock.part(part):
                    if names:
                        p["names"], p["among"] = ai_measure.name_of(
                            p["src"], picture, p["box"], cache=aic, region=region,
                            master_root=master_root or None)
                    else:
                        ai_measure.vector_of(p["src"], picture, p["box"], cache=aic)
            except Exception as e:
                logger.warning(f"Day AI: naming {p['src'].name}: {e}")
            for q in x[1:]:
                q["names"], q["among"] = p.get("names", []), p.get("among", "")
            if i % 5 == 0 or i == len(series):
                progress("Naming each series" if names else "Looking closer at each animal",
                         i, len(series))
    finally:
        ahead.close()
    logger.info(f"Day AI: {len(series)} series {'named' if names else 'looked at'} "
                f"({sum(len(x) for x in series)} poses with an animal)")


def _name_plants(pics, aic, region, master_root, names, progress, stopped, clock):
    """Poses without an animal: the whole sharpest frame, named among the
    country's plants (only when BioCLIP agrees: ai_measure.plant_names)."""
    from engine import ai_measure, day_ai
    still = day_ai.still_leaders(pics)
    ahead = day_ai.Prefetch([p["src"] for p in still
                             if aic.vector(p["src"], None) is None], load_upright)
    named = 0
    try:
        for i, p in enumerate(still, 1):
            if stopped():
                return

            def picture(src=p["src"]):
                with clock.part("waiting for a decoded picture (plants)"):
                    return ahead.get(src)
            try:
                with clock.part("a plant name"):
                    if names:
                        p["names"], p["among"] = ai_measure.plant_name_of(
                            p["src"], picture, cache=aic, region=region,
                            master_root=master_root or None)
                        named += bool(p["names"])
                    else:
                        ai_measure.vector_of(p["src"], picture, None, cache=aic)
            except Exception as e:
                logger.warning(f"Day AI: plant name {p['src'].name}: {e}")
            if i % 5 == 0 or i == len(still):
                progress("Naming the plants" if names else "Looking at the scenery",
                         i, len(still))
    finally:
        ahead.close()
    logger.info(f"Day AI: {len(still)} poses without an animal"
                + (f", {named} named as a plant" if names else ""))


# ── a batch of copied files, for the card read ──────────────────────────────

def card_batch(files) -> list[dict]:
    """Picture dicts for files just copied into ONE day folder, one per picture
    the way the culling grid sees it: the RAW (or HEIC) when there is one, else
    the camera JPG; videos, sidecars and renders (_DxO …) left out. The capture
    time comes from the files (one batched ExifTool run), sorted by time."""
    from engine import redundancy as rd
    from utils.raw_extensions import is_jpeg, is_raw_or_heic
    best: dict[tuple, Path] = {}
    for f in map(Path, files):
        if not f.exists():
            continue
        rank = 0 if is_raw_or_heic(f) else 1 if is_jpeg(f) else None
        if rank is None or _is_render(f.name):
            continue
        k = (str(f.parent).lower(), f.stem.lower())
        if k not in best or rank < (0 if is_raw_or_heic(best[k]) else 1):
            best[k] = f
    srcs = list(best.values())
    times = capture_times(srcs)
    from datetime import datetime
    pics = [{"key": str(s), "upn": str(s), "src": s, "time": rd._time(times.get(_k(s))),
             "stars": 0, "label": "", "jpg": False, "bytes": s.stat().st_size}
            for s in srcs]
    pics.sort(key=lambda p: (p["time"] or datetime.min, p["key"]))
    return pics


def _is_render(name: str) -> bool:
    """A JPG made FROM a picture (PhotoLab "_DxO", …): its picture name is
    shorter than its file name (the rule of batch/sv0ReadSDCard._is_render)."""
    from engine.handling import deriveUniquePictureName
    p = Path(name)
    return (p.suffix.lower() in (".jpg", ".jpeg")
            and deriveUniquePictureName(name) != p.stem.lower())


def _k(p) -> str:
    import os
    return os.path.normcase(os.path.normpath(str(p)))


def capture_times(paths) -> dict[str, str]:
    """{normalised path: "YYYY:MM:DD HH:MM:SS"} — one batched ExifTool run."""
    import json
    from engine.exif_writer import run_exiftool_files_parallel
    if not paths:
        return {}
    out = {}
    for cp in run_exiftool_files_parallel(["-j", "-DateTimeOriginal", "-CreateDate"],
                                          [Path(p) for p in paths]):
        try:
            rows = json.loads(cp.stdout or "[]")
        except ValueError:
            continue
        for r in rows:
            t = str(r.get("DateTimeOriginal") or r.get("CreateDate") or "")
            if t:
                out[_k(r.get("SourceFile", ""))] = t
    return out
