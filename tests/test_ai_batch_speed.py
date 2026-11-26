"""AI… speed-ups (2026-11-26): names for many poses in one sum, and the master's
days per species in one pass — the same answers as one at a time."""
import sqlite3
import threading

import numpy as np


def _clip(n_names=500, dim=16, seed=1):
    from engine.ai_models import BioClip
    rng = np.random.default_rng(seed)
    clip = object.__new__(BioClip)
    v = rng.normal(size=(dim, n_names)).astype(np.float32)
    clip.vectors = v / np.linalg.norm(v, axis=0)
    clip.sci = [f"Genus{i} species{i}" for i in range(n_names)]
    clip.common = [f"bird {i}" for i in range(n_names)]
    clip.scale = 50.0
    clip._subsets, clip._mats, clip._sub_lock = {}, {}, threading.Lock()
    return clip, rng


def _one_at_a_time(clip, vec, k, allowed):
    """The committed BioClip.top before batching, as the reference."""
    idx = clip.subset(allowed)
    mat = clip.vectors if idx is None else clip.vectors[:, idx]
    logits = clip.scale * (vec @ mat)
    logits -= logits.max()
    p = np.exp(logits)
    p /= p.sum()
    best = np.argsort(-p)[:k]
    return [(clip.sci[int(j if idx is None else idx[j])], float(p[j])) for j in best]


def test_top_many_gives_the_same_names_as_one_at_a_time():
    clip, rng = _clip()
    vecs = [rng.normal(size=16).astype(np.float32) for _ in range(70)]   # > one chunk
    allowed = frozenset(s.lower() for s in clip.sci[::7])
    for allow in (None, allowed):
        many = clip.top_many(vecs, 3, allowed=allow)
        for vec, got in zip(vecs, many):
            want = _one_at_a_time(clip, vec, 3, allow)
            assert [s for s, _c, _p in got] == [s for s, _p in want]
            assert np.allclose([p for _s, _c, p in got], [p for _s, p in want], atol=1e-5)
    assert clip.top(vecs[0], 3, allowed) == clip.top_many([vecs[0]], 3, allowed)[0]


def test_days_per_species_one_pass_counts_subspecies_like_the_viewer(tmp_path):
    from db.master_db import MasterDB
    active = tmp_path / "metadata" / "active"
    active.mkdir(parents=True)
    (active / "speciescollection.csv").write_text(
        "ScientificName;SubspeciesOf\n"
        "motacilla flava;\n"
        "motacilla flava flavissima;motacilla flava\n", encoding="utf-8")
    con = sqlite3.connect(":memory:")
    con.execute('CREATE TABLE pictures ("WetSoortNaam" TEXT, "DateTimeOriginal" TEXT)')
    con.executemany("INSERT INTO pictures VALUES (?, ?)", [
        ("Motacilla flava", "2023:05:01 10:00:00"),
        ("motacilla flava ", "2023:05:01 11:00:00"),          # same day, odd spacing
        ("motacilla flava flavissima", "2023:05:02 09:00:00"),
        ("ardea cinerea", "2023:05:03 09:00:00"),
        ("ardea cinerea", ""),                                 # no date: not a day
    ])
    db = object.__new__(MasterDB)
    db.master_root = str(tmp_path)
    db._connect = lambda: con
    got = db.days_per_species(["Motacilla flava", "motacilla flava flavissima",
                               "ardea cinerea", "unknown sp"])
    assert got == {"motacilla flava": 2,               # its own day + the subspecies' day
                   "motacilla flava flavissima": 1,    # a subspecies: its own only
                   "ardea cinerea": 1, "unknown sp": 0}
