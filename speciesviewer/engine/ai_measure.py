"""What the AI sees in ONE picture: where the animal is, how big, how sharp on it,
and what it might be.

ONE JOB: for a picture (its file + the upright image + its score from
engine.picture_score) return
  box        the animal box (MegaDetector), fractions of the frame: of the animals
             found, the one that is SHARPEST (in a flock the most confident box
             is often a blurry bird in front, not the one you focused on); the
             most confident one when the picture has no score
  px         the animal's long side in pixels at the picture's own size
  size_pct   that as a bar: 100 px = 0 %, 2500 px = 100 % (log scale; GH)
  sharp_in   sharpness INSIDE the box (the fine grid at the picture's own size)
  sharp_pct  that as a percentile of your master pictures from the same camera
             (engine.ai_yardstick) — None while the yardstick is not there
  sharp_ratio  sharp_in against the sharpest part of the whole frame (~1: the
             sharpest part is ON the animal). Information only: it decides
             nothing (a flock or a phone scene can be low and still a keeper)
  names      BioCLIP 2's top species, in YOUR words (engine.ai_names):
             [{"sci", "name", "prob", "how", "outside"}] — chosen among eBird's
             list for the picture's country (engine.ebird) when there is one,
             plus iNaturalist's species of the chosen groups there (engine.inat_lists,
             mammals unless the config says more); a weak name gives way to a
             confident non-bird from everything BioCLIP knows ("outside" = not
             on the list)
  among      what the names were chosen from, in words
No animal found = no box, sharp 0, size 0, and no names. Plants and fungi
(when chosen for the country) are never names for an animal: a picture with
no animal is named as a WHOLE by plant_name_of — only when BioCLIP's own
first name, among everything it knows, is that same plant (a beach, a
village street or a missed bird is then left unnamed; GH 2026-11-25).

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
FOLLOW_SIDE = 960        # following the animal: on the picture at this long side
FOLLOW_MIN = 0.8         # match score below this: run the detector after all
ELSEWHERE = 0.3          # sharp_ratio below this: "the sharpest part is elsewhere"
DETECT_EDGE = 1280
NOT_SURE = 0.45          # below this the first name is a guess (GH: 45–50 % per day)
SIZE_FULL_PX = 2500      # GH 2026-11-23: frame-filling geese
PLANT_AGREE = 0.15       # a whole picture's plant name: the open first name must be it, this sure
STILL_GROUPS = ("plants", "fungi")   # named from the whole picture, never as an animal
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


def pick_animal(boxes, fine, min_conf: float = MIN_CONF):
    """The animal box to judge the picture on: the sharpest of the believed
    animal boxes (fine grid, engine.picture_score.fine_in); the most confident
    one when there is no fine grid. Ties go to the more confident box."""
    if fine is None:
        return animal_box(boxes, min_conf)
    from engine.picture_score import fine_in
    best, key = None, None
    for b in boxes or []:
        if b.get("cat") != "animal" or b.get("conf", 0) < min_conf:
            continue
        s = fine_in(fine, b["box"])
        k = (-1.0 if s is None else s, b["conf"])
        if key is None or k > key:
            best, key = b, k
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
        # Boxes FOLLOWED from the frame before (the day dialog), not detected:
        # kept apart, so the Editor and the yardstick only ever use detections.
        self.db.execute("create table if not exists followed (path text primary key, "
                        "mtime integer, size integer, box text)")

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

    def followed(self, p: Path):
        m, s = self._stamp(p)
        with self.lock:
            r = self.db.execute("select mtime,size,box from followed where path=?",
                                (str(p),)).fetchone()
        return tuple(json.loads(r[2])) if r and r[0] == m and r[1] == s else None

    def put_followed(self, p: Path, box) -> None:
        m, s = self._stamp(p)
        with self.lock:
            self.db.execute("insert or replace into followed values (?,?,?,?)",
                            (str(p), m, s, json.dumps(list(box))))
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


_matched: dict = {}


def _as_bioclip(region: str, listed: frozenset) -> frozenset:
    """eBird's list for a region in BioCLIP's own names (cached per list)."""
    from engine import ai_models, ebird
    key = (region, listed)
    if key not in _matched:
        _matched[key] = ai_models.bioclip().match_list(listed, ebird.taxonomy())
    return _matched[key]


def _as_bioclip_inat(region: str, groups) -> frozenset:
    """iNaturalist's species of those groups in a region, in BioCLIP's own names."""
    from engine import ai_models, inat_lists
    key = ("inat", region, tuple(groups))
    if key not in _matched:
        names: set = set()
        for g in groups:
            names |= inat_lists.species(region, g) or frozenset()
        _matched[key] = (ai_models.bioclip().match_any(
            names, inat_lists.english_names(region, groups)) if names else frozenset())
    return _matched[key]


def _allowed(region: str, groups=None) -> tuple[frozenset | None, str]:
    """(BioCLIP names to choose among, in words) for a region: eBird's birds
    plus iNaturalist's species of the chosen groups (engine.inat_lists.chosen_groups;
    `groups` overrides). None = everything: without eBird's list the birds
    would be missing, so iNaturalist's groups alone never narrow the choice."""
    from engine import ebird, inat_lists
    listed = ebird.species(region) if region else None
    if not listed:
        return None, ("everything the AI knows (no country known)" if not region else
                      f"everything the AI knows (no eBird list for {region})")
    allowed = _as_bioclip(region, listed)
    words = f"eBird's list for {region} ({len(listed)} bird species)"
    if groups is None:
        groups = inat_lists.chosen_groups(region)
    groups = [g for g in groups if g not in STILL_GROUPS]
    extra = _as_bioclip_inat(region, groups) if groups else frozenset()
    if extra:
        allowed = allowed | extra
        labels = ", ".join(inat_lists.GROUPS[g][0].lower() for g in groups)
        words += f" + iNaturalist's {labels} ({len(extra)} species)"
    return allowed, words


def names_for(vec, region: str = "", master_root=None, k: int = 3,
              groups=None) -> tuple[list, str]:
    """(names, among) for a picture vector: see the module docstring.
    `groups` = iNaturalist groups to add to eBird's birds (None = from the config)."""
    from engine import ai_models, ai_names
    clip = ai_models.bioclip()
    allowed, among = _allowed(region, groups)
    outside = False
    if allowed:
        top = clip.top(vec, k=k, allowed=allowed)
        if not top or top[0][2] < NOT_SURE:
            wide = clip.top(vec, k=k)
            if wide and clip.group.get(wide[0][0], ("",))[0] != "aves"                     and wide[0][0].lower() not in allowed                     and wide[0][2] > (top[0][2] if top else 0):
                top, outside = wide, True
                among = "everything the AI knows (nothing on the list fits)"
    else:
        top = clip.top(vec, k=k)
    return _shown(clip, top, master_root, outside), among


def _shown(clip, top, master_root, outside: bool = False) -> list[dict]:
    """BioCLIP's (sci, common, prob) in your words (engine.ai_names)."""
    from engine import ai_names
    names = []
    for sci, common, prob in top:
        fam = clip.group.get(sci, ("", ""))[1]
        d = ai_names.display(sci, common, master_root, family=fam)
        d["prob"] = prob
        d["outside"] = outside
        names.append(d)
    return names


def still_groups(region: str) -> list[str]:
    """The plant / fungus groups chosen for a country (none = no whole-picture names)."""
    from engine import inat_lists
    if not region:
        return []
    return [g for g in inat_lists.chosen_groups(region) if g in STILL_GROUPS]


def plant_names(vec, region: str, master_root=None, k: int = 3,
                groups=None) -> tuple[list, str]:
    """(names, among) for the vector of a WHOLE picture with no animal: the
    first names among iNaturalist's plants (and fungi) of the country — only
    when BioCLIP's open first name (everything it knows) is that same species,
    at PLANT_AGREE or more. Else ([], ""): scenery, people, a missed animal.
    tools/ml/svr_plant_names.py: Madagascar 2026-11-07, baobabs/Moringa/Delonix
    kept; beach, village, street, birders, egret scene left out."""
    from engine import ai_models, inat_lists
    groups = still_groups(region) if groups is None else groups
    allowed = _as_bioclip_inat(region, groups) if groups else frozenset()
    if not allowed:
        return [], ""
    clip = ai_models.bioclip()
    top = clip.top(vec, k=k, allowed=allowed)
    wide = clip.top(vec, k=1)
    if not top or not wide or wide[0][0] != top[0][0] or wide[0][2] < PLANT_AGREE:
        return [], ""
    labels = ", ".join(inat_lists.GROUPS[g][0].lower() for g in groups)
    return (_shown(clip, top, master_root),
            f"iNaturalist's {labels} for {region} ({len(allowed)} species), the whole picture")


def plant_name_of(src: Path, im, *, cache: AiCache, region: str = "",
                  master_root=None, k: int = 3) -> tuple[list, str]:
    """plant_names for a picture: BioCLIP's vector of the whole picture
    (cached, as box None). `im` as in measure()."""
    from engine import ai_models
    vec = cache.vector(src, None)
    if vec is None:
        pic = im() if callable(im) else im
        vec = ai_models.bioclip().picture_vectors([crop_image(pic, None)])[0]
        cache.put_vector(src, None, vec)
    return plant_names(vec, region, master_root, k)


def part_names(im, region: str = "", master_root=None, k: int = 3) -> list[tuple[str, list]]:
    """[(what the names were chosen from, names)] for a PART of a picture that
    YOU framed (the large view's "AI: name a part"): `im` is that part, as it is,
    no room added. Three answers, because you know better than the AI what you
    pointed at: the country's list (as names_for), the country's plants and
    fungi WITHOUT plant_names' agreement test (you chose the part, so a tree is
    meant), and the first names among everything BioCLIP knows. A list that does
    not apply (no country, no plants chosen) is left out."""
    from engine import ai_models, inat_lists
    clip = ai_models.bioclip()
    pic = im.copy()
    pic.thumbnail((1024, 1024))
    vec = clip.picture_vectors([pic])[0]
    out = []
    if region:
        names, among = names_for(vec, region, master_root, k)
        if names and not names[0].get("outside"):
            out.append((among, names))
        groups = still_groups(region)
        allowed = _as_bioclip_inat(region, groups) if groups else frozenset()
        if allowed:
            labels = ", ".join(inat_lists.GROUPS[g][0].lower() for g in groups)
            out.append((f"iNaturalist's {labels} for {region} ({len(allowed)} species)",
                        _shown(clip, clip.top(vec, k=k, allowed=allowed), master_root)))
    out.append(("everything the AI knows",
                _shown(clip, clip.top(vec, k=k), master_root, outside=True)))
    return out


def prepare_names(region: str = "") -> None:
    """Everything names_for needs before the first name, loaded now: BioCLIP
    and its names, and the lists for the region matched to them (all kept
    for the next pictures). Lets a run time this apart from the names."""
    from engine import ai_models
    ai_models.bioclip()
    if region:
        _allowed(region)
        if still_groups(region):
            _as_bioclip_inat(region, still_groups(region))


def small_gray(im) -> np.ndarray:
    """The picture in grey at FOLLOW_SIDE, for follow()."""
    g = im.convert("L")
    g.thumbnail((FOLLOW_SIDE, FOLLOW_SIDE))
    return np.asarray(g)


def follow(prev, cur, box, grow: float = 0.15, search: float = 1.0):
    """(box, score): the animal in `box` (fractions) of the frame before, found
    again in this frame by template matching (OpenCV, a few ms). A frame of the
    same pose seldom needs the detector again (tools/ml/svr_box_tracking.py:
    Eempolder 38 % fewer detections at FOLLOW_MIN, same verdict 356/363).
    (None, 0) when it cannot be tried."""
    import cv2
    if prev is None or cur is None or prev.shape != cur.shape or not box:
        return None, 0.0
    H, W = prev.shape
    x0, y0, x1, y1 = box
    bw, bh = (x1 - x0) * W, (y1 - y0) * H
    gx, gy = max(6, bw * grow), max(6, bh * grow)
    tx0, ty0 = int(max(0, x0 * W - gx)), int(max(0, y0 * H - gy))
    tx1, ty1 = int(min(W, x1 * W + gx)), int(min(H, y1 * H + gy))
    tpl = prev[ty0:ty1, tx0:tx1]
    if tpl.shape[0] < 8 or tpl.shape[1] < 8:
        return None, 0.0
    sx, sy = max(bw, 40) * search + gx, max(bh, 40) * search + gy
    rx0, ry0 = int(max(0, tx0 - sx)), int(max(0, ty0 - sy))
    rx1, ry1 = int(min(W, tx1 + sx)), int(min(H, ty1 + sy))
    region = cur[ry0:ry1, rx0:rx1]
    if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
        return None, 0.0
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    dx, dy = (rx0 + loc[0] - tx0) / W, (ry0 + loc[1] - ty0) / H
    nb = (max(0.0, x0 + dx), max(0.0, y0 + dy), min(1.0, x1 + dx), min(1.0, y1 + dy))
    return nb, float(score)


def name_of(src: Path, im, box, *, cache: AiCache, region: str = "",
            master_root=None, k: int = 3) -> tuple[list, str]:
    """(names, among) for the animal in `box`: BioCLIP's vector of the crop
    (cached), then the names for the region. `im` as in measure()."""
    from engine import ai_models
    vec = cache.vector(src, box)
    if vec is None:
        pic = im() if callable(im) else im
        vec = ai_models.bioclip().picture_vectors([crop_image(pic, box)])[0]
        cache.put_vector(src, box, vec)
    return names_for(vec, region, master_root, k)


def measure(src: Path, im, score: dict | None, camera: str = "", *,
            native: int = 0, cache: AiCache | None = None, region: str = "",
            master_root=None, yardstick=None, k: int = 3, name: bool = True,
            box=None) -> dict:
    """The AI's view of one picture (see the module docstring). `im` = the
    upright PIL image, or a function that returns it (then it is decoded only
    when the caches do not have what is needed); `native` = the picture's own long side (the sensor, from
    EXIF; else the decoded size); `region` = the picture's country (ISO code,
    for eBird's list); `master_root` = whose species list names it;
    `yardstick` = engine.ai_yardstick.Yardstick or None; `name` False = no
    species names (the day dialog names only each pose's best frame: name_of);
    `box` = the animal's box when already known (followed from the frame
    before) — then the detector does not run."""
    from engine import ai_models
    from engine.picture_score import fine_in, frame_top
    own = cache is None
    cache = cache or AiCache()
    held = {}

    def picture():
        if "im" not in held:
            held["im"] = im() if callable(im) else im
            cache.put_upright(src, held["im"].size)
        return held["im"]

    try:
        fine = (score or {}).get("fine")
        if box is None:
            found = cache.boxes(src)
            if found is None:
                small = picture().copy()
                small.thumbnail((DETECT_EDGE, DETECT_EDGE))
                found = ai_models.detector().boxes(small)
                cache.put_boxes(src, found)
            box = pick_animal(found, fine)
        out = {"box": box, "px": 0, "size_pct": 0.0, "sharp_in": None,
               "sharp_pct": 0.0 if box is None else None, "sharp_ratio": None,
               "names": [], "among": ""}
        if box is None:
            return out
        size = cache.upright(src) or picture().size
        out["px"] = long_side_px(box, size, native or (score or {}).get("native"))
        out["size_pct"] = size_pct(out["px"])
        if fine is not None:
            out["sharp_in"] = float(fine_in(fine, box))
            top = frame_top(fine)
            out["sharp_ratio"] = out["sharp_in"] / top if top else None
            if yardstick is not None:
                out["sharp_pct"] = yardstick.percentile(camera, out["sharp_in"])
        if name:
            out["names"], out["among"] = name_of(src, picture, box, cache=cache,
                                                 region=region, master_root=master_root, k=k)
        return out
    finally:
        if own:
            cache.close()
