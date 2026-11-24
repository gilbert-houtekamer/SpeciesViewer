"""What the AI sees in ONE picture: where the animal is, how big, how sharp on it,
and what it might be.

ONE JOB: for a picture (its file + the upright image + its score from
engine.picture_score) return
  box        the most confident animal box (MegaDetector), fractions of the frame
  px         the animal's long side in pixels at the picture's own size
  size_pct   that as a bar: 100 px = 0 %, 2500 px = 100 % (log scale; GH)
  sharp_in   sharpness INSIDE the box (the fine grid at the picture's own size)
  sharp_pct  that as a percentile of your master pictures from the same camera
             (engine.ai_yardstick) — None while the yardstick is not there
  names      BioCLIP 2's top species, in YOUR words (engine.ai_names):
             [{"sci", "name", "prob", "how", "outside"}] — chosen among eBird's
             list for the picture's country (engine.ebird) when there is one; a
             weak bird name gives way to a confident non-bird from everything
             BioCLIP knows ("outside" = not on the bird list)
  among      what the names were chosen from, in words
No animal found = no box, sharp 0, size 0, and no names.

INVARIANT: read-only for the picture. The boxes and picture vectors are cached in
app-data (`scores/bird_boxes.sqlite`, keyed on path + size + mtime + model), so
a picture is looked at once; the names are recomputed from the vector, cheaply,
for whatever list of species applies.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path

import numpy as np

MIN_CONF = 0.2           # weaker "animal" boxes are not believed
DETECT_EDGE = 1280
NOT_SURE = 0.45          # below this the first name is a guess (GH: 45–50 % per day)
SIZE_FULL_PX = 2500      # GH 2026-11-23: frame-filling geese
_DET_MODEL = "MDV5A"     # the same model whether run through PyTorch or ONNX


def size_pct(px: float) -> float:
    if px <= 100:
        return 0.0
    return min(1.0, math.log(px / 100) / math.log(SIZE_FULL_PX / 100)) * 100


def animal_box(boxes, min_conf: float = MIN_CONF):
    best = max((b for b in boxes or [] if b.get("cat") == "animal"
                and b.get("conf", 0) >= min_conf),
               key=lambda b: b["conf"], default=None)
    return tuple(best["box"]) if best else None


def long_side_px(box, upright_size, native_long) -> int:
    """The box's long side in pixels of the picture's own size."""
    W, H = upright_size
    s = (native_long or max(W, H)) / max(W, H)
    x0, y0, x1, y1 = box
    return round(max((x1 - x0) * W, (y1 - y0) * H) * s)


def crop_image(im, box):
    """The upright image cut to the animal: square, 30 % room, at most 1024 px."""
    if box:
        W, H = im.size
        x0, y0, x1, y1 = box
        side = max(max((x1 - x0) * W, (y1 - y0) * H) * 1.3, 0.15 * max(W, H))
        cx, cy = (x0 + x1) / 2 * W, (y0 + y1) / 2 * H
        im = im.crop((int(max(0, cx - side / 2)), int(max(0, cy - side / 2)),
                      int(min(W, cx + side / 2)), int(min(H, cy + side / 2))))
    im = im.copy()
    im.thumbnail((1024, 1024))
    return im


class AiCache:
    """Boxes and picture vectors per file, valid while size + mtime + model match."""

    def __init__(self, path: Path | None = None):
        if path is None:
            from utils.cache_paths import app_data_dir
            path = app_data_dir() / "scores" / "bird_boxes.sqlite"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("create table if not exists boxes (path text primary key, "
                        "mtime integer, size integer, model text, boxes text)")
        self.db.execute("create table if not exists vectors (path text primary key, "
                        "mtime integer, size integer, model text, box text, vec blob)")
        self.db.execute("create table if not exists upright (path text primary key, "
                        "mtime integer, size integer, w integer, h integer)")

    def upright(self, p: Path):
        """(w, h) of the decoded upright picture, as measured before — or None."""
        m, s = self._stamp(p)
        with self.lock:
            r = self.db.execute("select mtime,size,w,h from upright where path=?",
                                (str(p),)).fetchone()
        return (r[2], r[3]) if r and r[0] == m and r[1] == s else None

    def put_upright(self, p: Path, wh) -> None:
        m, s = self._stamp(p)
        with self.lock:
            self.db.execute("insert or replace into upright values (?,?,?,?,?)",
                            (str(p), m, s, int(wh[0]), int(wh[1])))
            self.db.commit()

    @staticmethod
    def _stamp(p: Path):
        st = p.stat()
        return st.st_mtime_ns, st.st_size

    def boxes(self, p: Path):
        m, s = self._stamp(p)
        with self.lock:
            r = self.db.execute("select mtime,size,model,boxes from boxes where path=?",
                                (str(p),)).fetchone()
        if r and r[0] == m and r[1] == s and r[2] == _DET_MODEL:
            return json.loads(r[3])
        return None

    def put_boxes(self, p: Path, boxes: list) -> None:
        m, s = self._stamp(p)
        with self.lock:
            self.db.execute("insert or replace into boxes values (?,?,?,?,?)",
                            (str(p), m, s, _DET_MODEL, json.dumps(boxes)))
            self.db.commit()

    def vector(self, p: Path, box):
        from engine.ai_models import MODEL_VERSION
        m, s = self._stamp(p)
        with self.lock:
            r = self.db.execute("select mtime,size,model,box,vec from vectors where path=?",
                                (str(p),)).fetchone()
        if (r and r[0] == m and r[1] == s and r[2] == MODEL_VERSION
                and r[3] == json.dumps(list(box) if box else None)):
            return np.frombuffer(r[4], np.float32)
        return None

    def put_vector(self, p: Path, box, vec) -> None:
        from engine.ai_models import MODEL_VERSION
        m, s = self._stamp(p)
        with self.lock:
            self.db.execute("insert or replace into vectors values (?,?,?,?,?,?)",
                            (str(p), m, s, MODEL_VERSION,
                             json.dumps(list(box) if box else None),
                             np.ascontiguousarray(vec, np.float32).tobytes()))
            self.db.commit()

    def close(self) -> None:
        self.db.close()


def names_for(vec, region: str = "", master_root=None, k: int = 3) -> tuple[list, str]:
    """(names, among) for a picture vector: see the module docstring."""
    from engine import ai_models, ai_names, ebird
    clip = ai_models.bioclip()
    allowed = ebird.species(region) if region else None
    if allowed:
        top = clip.top(vec, k=k, allowed=allowed)
        among = f"eBird's list for {region} ({len(allowed)} bird species)"
        outside = False
        if not top or top[0][2] < NOT_SURE:
            wide = clip.top(vec, k=k)
            if wide and clip.group.get(wide[0][0], ("",))[0] != "aves" \
                    and wide[0][2] > (top[0][2] if top else 0):
                top, outside = wide, True
                among = "everything the AI knows (no bird on the list fits)"
    else:
        top = clip.top(vec, k=k)
        outside = False
        among = ("everything the AI knows (no country known)" if not region else
                 f"everything the AI knows (no eBird list for {region})")
    names = []
    for sci, common, prob in top:
        fam = clip.group.get(sci, ("", ""))[1]
        d = ai_names.display(sci, common, master_root, family=fam)
        d["prob"] = prob
        d["outside"] = outside
        names.append(d)
    return names, among


def measure(src: Path, im, score: dict | None, camera: str = "", *,
            native: int = 0, cache: AiCache | None = None, region: str = "",
            master_root=None, yardstick=None, k: int = 3) -> dict:
    """The AI's view of one picture (see the module docstring). `im` = the
    upright PIL image, or a function that returns it (then it is decoded only
    when the caches do not have what is needed); `native` = the picture's own long side (the sensor, from
    EXIF; else the decoded size); `region` = the picture's country (ISO code,
    for eBird's list); `master_root` = whose species list names it;
    `yardstick` = engine.ai_yardstick.Yardstick or None."""
    from engine import ai_models
    from engine.picture_score import fine_in
    own = cache is None
    cache = cache or AiCache()
    held = {}

    def picture():
        if "im" not in held:
            held["im"] = im() if callable(im) else im
            cache.put_upright(src, held["im"].size)
        return held["im"]

    try:
        found = cache.boxes(src)
        if found is None:
            small = picture().copy()
            small.thumbnail((DETECT_EDGE, DETECT_EDGE))
            found = ai_models.detector().boxes(small)
            cache.put_boxes(src, found)
        box = animal_box(found)
        out = {"box": box, "px": 0, "size_pct": 0.0, "sharp_in": None,
               "sharp_pct": 0.0 if box is None else None, "names": [], "among": ""}
        if box is None:
            return out
        size = cache.upright(src) or picture().size
        out["px"] = long_side_px(box, size, native or (score or {}).get("native"))
        out["size_pct"] = size_pct(out["px"])
        fine = (score or {}).get("fine")
        if fine is not None:
            out["sharp_in"] = float(fine_in(fine, box))
            if yardstick is not None:
                out["sharp_pct"] = yardstick.percentile(camera, out["sharp_in"])
        vec = cache.vector(src, box)
        if vec is None:
            vec = ai_models.bioclip().picture_vectors([crop_image(picture(), box)])[0]
            cache.put_vector(src, box, vec)
        out["names"], out["among"] = names_for(vec, region, master_root, k)
        return out
    finally:
        if own:
            cache.close()
