"""engine.redundancy + engine.picture_score: who may be suggested, poses, the
rarity rule, and the sharpness measure itself."""
from datetime import datetime, timedelta

import numpy as np
import pytest

from engine import redundancy as rd
from engine.picture_score import score_image, hamming


def _pic(i, *, t, sharp, label="", stars=0, jpg=False, dhash="0" * 16, place="Merzouga"):
    return {"upn": f"p{i}", "src": None, "time": t, "label": label, "stars": stars,
            "jpg": jpg, "place": place, "bytes": 20_000_000, "sharp": sharp,
            "dhash": dhash}


def test_per_day_rule():
    assert [rd.per_day_for(d) for d in (1, 4, 9, 25, 67)] == [20, 12, 8, 5, 3]
    assert rd.per_day_for(0) == 20 and rd.per_day_for(10_000) == 3


def test_keepers_and_decided_are_never_suggested():
    t0 = datetime(2024, 10, 12, 9, 0, 0)
    pics = [_pic(0, t=t0, sharp=1, stars=1), _pic(1, t=t0, sharp=1, label="Red"),
            _pic(2, t=t0, sharp=1, jpg=True), _pic(3, t=t0, sharp=1, label="Blue"),
            _pic(4, t=t0, sharp=1, label="Purple")]
    pics += [_pic(10 + i, t=t0 + timedelta(seconds=i), sharp=1 + i, label="Green")
             for i in range(10)]
    rd.select(pics, rd.Settings(per_day=0))
    v = {p["upn"]: p["verdict"] for p in pics}
    assert [v[f"p{i}"] for i in range(3)] == [rd.KEEPER] * 3
    assert v["p3"] == v["p4"] == rd.DECIDED
    assert all(v[f"p{10 + i}"] == rd.PURPLE for i in range(10))
    st = rd.statistics(pics)
    assert (st["keepers"], st["decided"], st["candidates"]) == (3, 2, 10)


def test_one_pose_keeps_its_sharpest_up_to_per_pose():
    t0 = datetime(2024, 10, 12, 9, 0, 0)
    pics = [_pic(i, t=t0 + timedelta(seconds=i), sharp=s)
            for i, s in enumerate([5, 9, 7, 3, 8])]
    groups = rd.select(pics, rd.Settings(per_day=10, per_pose=2))
    assert len(groups) == 1
    kept = sorted(p["sharp"] for p in pics if p["verdict"] == rd.KEEP)
    assert kept == [8, 9]
    out = rd.outcome(pics, groups)
    assert (out["keep"], out["purple"]) == (2, 3)


def test_time_gap_and_look_start_a_new_pose():
    t0 = datetime(2024, 10, 12, 9, 0, 0)
    a = _pic(0, t=t0, sharp=1)
    b = _pic(1, t=t0 + timedelta(seconds=5), sharp=1)                  # same pose
    c = _pic(2, t=t0 + timedelta(seconds=100), sharp=1)                # gap
    d = _pic(3, t=t0 + timedelta(seconds=101), sharp=1, dhash="f" * 16)  # looks different
    assert [len(g) for g in rd.poses([a, b, c, d], 30, 0.25)] == [2, 1, 1]


def test_per_day_rows_and_soft_frames():
    t0 = datetime(2024, 10, 12, 9, 0, 0)
    day2 = t0 + timedelta(days=1)
    pics = [_pic(0, t=t0, sharp=10), _pic(1, t=t0 + timedelta(seconds=1), sharp=2),
            _pic(2, t=day2, sharp=4, place="Erg Chebbi")]
    rd.select(pics, rd.Settings(per_day=5, per_pose=2, soft=0.6))
    rows = rd.per_day_rows(pics)
    assert [(r["keep"], r["purple"]) for r in rows] == [(1, 1), (1, 0)]   # 2 < 0.6 × 10
    assert rows[1]["places"] == "Erg Chebbi"


def test_score_finds_the_sharp_subject():
    import cv2
    rng = np.random.default_rng(1)
    img = np.full((600, 800), 128, np.uint8)
    img[200:300, 300:400] = rng.integers(0, 255, (100, 100), dtype=np.uint8)  # "the bird"
    sharp = score_image(cv2.imencode(".jpg", img)[1].tobytes())
    soft = score_image(cv2.imencode(".jpg", cv2.GaussianBlur(img, (0, 0), 4))[1].tobytes())
    assert sharp["sharp"] > 5 * soft["sharp"]
    assert sharp["tiles"].shape == (12, 16)
    assert hamming(sharp["dhash"], sharp["dhash"]) == 0


def test_pixel_level_breaks_a_tie_within_a_pose():
    from engine.picture_score import FINE_GRID, GRID
    t0 = datetime(2024, 10, 12, 9, 0, 0)
    tiles = np.zeros((GRID[1], GRID[0]), np.float32)
    tiles[5, 7] = 100.0                                   # the subject, same in both
    fine_a = np.zeros((FINE_GRID[1], FINE_GRID[0]), np.float16)
    fine_b = fine_a.copy()
    fine_a[16, 22] = 50                                   # inside the subject box
    fine_b[16, 22] = 80                                   # sharper at the pixel level
    fine_b[0, 0] = 999                                    # sharp, but far from the subject
    a = _pic(0, t=t0, sharp=100.0)
    b = _pic(1, t=t0 + timedelta(seconds=1), sharp=100.0)
    a.update(tiles=tiles, fine=fine_a)
    b.update(tiles=tiles, fine=fine_b)
    rd.select([a, b], rd.Settings(per_day=10, per_pose=1, soft=0.0))
    assert (a["verdict"], b["verdict"]) == (rd.PURPLE, rd.KEEP)
    assert b["in_pose"] == pytest.approx(80 / 3, rel=0.01)   # top 3 tiles in the box


def test_score_keeps_a_fine_grid_at_its_own_size():
    import cv2
    from engine.picture_score import FINE_GRID, fine_in, subject_box
    rng = np.random.default_rng(2)
    img = np.full((1200, 1800), 128, np.uint8)
    img[500:700, 800:1000] = rng.integers(0, 255, (200, 200), dtype=np.uint8)
    sc = score_image(cv2.imencode(".jpg", img)[1].tobytes())
    assert sc["fine"].shape == (FINE_GRID[1], FINE_GRID[0]) and sc["native"] == 1800
    box = subject_box(sc["tiles"])
    assert box[0] <= 900 / 1800 <= box[2] and box[1] <= 600 / 1200 <= box[3]
    assert fine_in(sc["fine"], box) > 10 * fine_in(sc["fine"], (0, 0, 0.2, 0.2))
