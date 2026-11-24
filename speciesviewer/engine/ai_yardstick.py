"""The yardstick for "how sharp on the animal": your own master pictures.

ONE JOB: per camera, the sorted sharpness-inside-the-animal-box of the master's
pictures (the third you kept), so one picture's sharpness becomes a percentile:
"sharper on the bird than X % of what you kept from this camera". Built from
the caches only (scores + boxes in app-data; no picture is decoded), saved to
app-data `scores/yardstick.json`, rebuilt when older than a week.

INVARIANT: read-only for the master. A camera with fewer than MIN_PICTURES
measured master pictures has no yardstick (percentile None), rather than a
misleading one.
"""
from __future__ import annotations

import bisect
import json
import threading
import time
from pathlib import Path

MIN_PICTURES = 200
MAX_AGE_S = 7 * 24 * 3600
_lock = threading.Lock()
_loaded = None


def norm_camera(s: str) -> str:
    """'OM-1 Mark II' and 'OM-1MarkII' are the same camera."""
    return "".join((s or "").lower().split())


class Yardstick:
    def __init__(self, by_camera: dict[str, list[float]]):
        self.by_camera = {k: sorted(v) for k, v in by_camera.items()
                          if k and len(v) >= MIN_PICTURES}

    def percentile(self, camera: str, value: float):
        ref = self.by_camera.get(norm_camera(camera))
        if not ref or value is None:
            return None
        return 100.0 * bisect.bisect_left(ref, value) / len(ref)

    def cameras(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.by_camera.items()}


def _path() -> Path:
    from utils.cache_paths import app_data_dir
    return app_data_dir() / "scores" / "yardstick.json"


def build(master_root) -> Yardstick:
    """From the master's picture rows + the score and box caches."""
    from db.master_db import MasterDB
    from engine.ai_measure import AiCache, animal_box
    from engine.picture_score import Cache, fine_in
    from utils.raw_extensions import is_jpeg, is_raw_or_heic
    root = Path(master_root)
    db = MasterDB(root)
    try:
        db.ensure_fresh()
        rows = [r for d in db.pictures_in_daterange("0001-01-01", "9999-12-31").values()
                for r in d]
    finally:
        db.close()
    scores, boxes = Cache(), AiCache()
    by: dict[str, list[float]] = {}
    try:
        for r in rows:
            folder = root / (r.get("TargetPath") or "")
            files = r.get("Files") or []
            src = next((folder / f for f in files if is_raw_or_heic(Path(f))), None) \
                or next((folder / f for f in files if is_jpeg(Path(f))), None)
            if src is None:
                continue
            try:
                b = animal_box(boxes.boxes(src))
            except OSError:
                continue
            if b is None:
                continue
            sc = scores.get(src)
            if sc and sc.get("fine") is not None:
                by.setdefault(norm_camera(r.get("CameraModel")), []).append(
                    float(fine_in(sc["fine"], b)))
    finally:
        scores.close()
        boxes.close()
    return Yardstick(by)


def get(master_root) -> Yardstick | None:
    """The yardstick: from memory, else the saved file (when fresh), else built
    now (about half a minute; call from a worker thread). None without a master."""
    global _loaded
    if not master_root:
        return None
    with _lock:
        if _loaded is not None:
            return _loaded
        p = _path()
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if (d.get("master") == str(master_root)
                    and time.time() - d.get("built", 0) < MAX_AGE_S):
                _loaded = Yardstick(d["by_camera"])
                return _loaded
        except (OSError, ValueError, KeyError):
            pass
        y = build(master_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"master": str(master_root), "built": time.time(),
                                   "by_camera": y.by_camera}), encoding="utf-8")
        tmp.replace(p)
        _loaded = y
        return y
