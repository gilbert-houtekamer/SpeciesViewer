"""Deleting master pictures WITH a JPG (the colour-delete's opt-in), and putting
them back from the master trash.

Uses the tiny master of test_master_move_jpg:

    larus fuscus      p1 (rate 5, JPG + .mov, best of species/family/class)
                      p2 (rate 3, JPG)
    larus argentatus  p3 (rate 5, JPG only)
    sterna hirundo    p4 (rate 2, JPG only)

The invariants: a picture with a JPG still needs the explicit opt-in; a delete
moves the species rollups (picture + JPG counters, best-pick, JPGmap); and a
restore moves them BACK, so the page shows the photo again — including for a
single deleted JPG (Delete JPG), where the picture itself never went away.
"""
import json

import pytest

from db import master_mutate as mm
from test_master_move_jpg import _species, master  # noqa: F401  (fixture)


def _rollup(db, name):
    s = _species(db, name)
    return {k: s[k] for k in ("iPictureCollectionCount", "iPicturesWithJPGsCollection",
                              "iSpeciesCollectionCount", "BestUPN", "BestJPG",
                              "currentHighestRating", "JPGmap")}


def test_jpg_picture_still_refused_without_opt_in(master):
    root, db = master
    with pytest.raises(mm.MasterMutateError):
        mm.delete_picture(db, root, "p2")
    assert db.picture("p2") is not None


def test_jpg_picture_upns_ignores_thumbnails(master):
    _root, db = master
    assert db.jpg_picture_upns("larus fuscus") == {"p1", "p2"}
    assert db.jpg_picture_upns("LARUS ARGENTATUS") == {"p3"}
    assert db.jpg_picture_upns("nonexistent") == set()


def test_delete_then_restore_puts_rollups_back(master):
    root, db = master
    before = {n: _rollup(db, n) for n in ("larus fuscus", "laridae", "aves")}

    res = mm.delete_picture(db, root, "p2", allow_jpg=True)
    sp = _rollup(db, "larus fuscus")
    assert sp["iPictureCollectionCount"] == 1
    assert sp["iPicturesWithJPGsCollection"] == 1
    assert sp["JPGmap"] == ["p1.jpg"]
    assert _rollup(db, "laridae")["iPicturesWithJPGsCollection"] == 2
    assert not (root / "biota/aves/laridae/larus fuscus/p2.jpg").exists()

    back = mm.undo_delete(db, root, res["batch"])
    assert back["sci"] == "larus fuscus" and back["had_jpg"] is True
    assert (root / "biota/aves/laridae/larus fuscus/p2.jpg").exists()
    for n, was in before.items():
        assert _rollup(db, n) == was, n


def test_restore_of_the_best_picture_wins_its_species_back(master):
    root, db = master
    res = mm.delete_picture(db, root, "p1", allow_jpg=True)
    sp = _rollup(db, "larus fuscus")
    assert (sp["BestUPN"], sp["currentHighestRating"]) == ("p2", 3)
    assert sp["JPGmap"] == ["p2.jpg"]

    mm.undo_delete(db, root, res["batch"])
    sp = _rollup(db, "larus fuscus")
    assert (sp["BestUPN"], sp["BestJPG"], sp["currentHighestRating"]) == ("p1", "p1.jpg", 5)
    assert sp["JPGmap"] == ["p1.jpg", "p2.jpg"]
    assert sp["iPictureCollectionCount"] == 2
    assert sp["iPicturesWithJPGsCollection"] == 2


def test_restore_of_one_deleted_jpg_returns_it_to_the_page(master):
    root, db = master
    res = mm.delete_jpg(db, root, "p2", "p2.jpg")      # p2 keeps its .nef
    assert db.picture("p2") is not None
    assert _rollup(db, "larus fuscus")["JPGmap"] == ["p1.jpg"]
    assert _rollup(db, "larus fuscus")["iPicturesWithJPGsCollection"] == 1

    mm.undo_delete(db, root, res["batch"])
    sp = _rollup(db, "larus fuscus")
    assert sp["JPGmap"] == ["p1.jpg", "p2.jpg"]
    assert sp["iPicturesWithJPGsCollection"] == 2
    assert sp["iPictureCollectionCount"] == 2       # the picture never left


def test_restore_after_the_species_emptied_does_not_fail(master):
    root, db = master
    res = mm.delete_picture(db, root, "p3", allow_jpg=True)
    assert _species(db, "larus argentatus") is None   # the delete emptied it
    back = mm.undo_delete(db, root, res["batch"])
    assert back["sci"] == "larus argentatus"
    assert db.picture("p3") is not None               # Count Species does the rest


def test_restore_is_journalled_with_its_species_section(master):
    root, db = master
    res = mm.delete_picture(db, root, "p2", allow_jpg=True)
    mm.undo_delete(db, root, res["batch"])
    entries = [json.loads(line) for line in
               db._jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    restore = [e for e in entries if e.get("op") == "restore"][-1]
    assert "larus fuscus" in restore["species"]
    assert restore["species"]["larus fuscus"]["JPGmap"] == ["p1.jpg", "p2.jpg"]
