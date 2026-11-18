"""Make JPG and Rescan on a MASTER picture (the Editor's buttons).

The invariants: never replace a file (<name>.jpg may be the camera's own JPG, so a
second bake is <name>-JPG-2.jpg); the new JPG joins the master and is pinned as
the picture's website JPG; a read-only master does nothing.
"""
from pathlib import Path

import pytest

from test_master_move_jpg import master, PICTURES, FUSCUS  # noqa: F401  (fixture)


class _Col:
    """Just enough of RatingEditorCollection to run its master Make JPG / Rescan."""
    def __init__(self, root, db, read_only=False):
        from gui.rating_provider import RatingEditorCollection as C
        self._c = C
        self._master_root = Path(root)
        self._master_db = db
        self._master_read_only = read_only
        self._master_keys = set(PICTURES)
        self._master_upn = {k: db.picture(k) for k in PICTURES}
        self._master_files = db.files_for_upns(list(PICTURES))
        self._crop_flag_cache, self._jpg_stale_cache = {}, {}
        self._view_read_only = False

    def __getattr__(self, name):
        attr = self._c.__dict__.get(name)
        if attr is None:                       # inherited from the base class
            attr = getattr(self._c, name)
        if hasattr(attr, "__get__"):           # method, property, staticmethod
            return attr.__get__(self, type(self))
        return attr                            # a class constant


@pytest.fixture
def col(master, monkeypatch):
    root, db = master
    import gui.preview_extract as pe
    import db.master_mutate as mm

    def fake_bake(raw, out, crop):
        Path(out).write_bytes(b"baked")
        return (1600, 1200)
    monkeypatch.setattr(pe, "bake_blog_jpg", fake_bake)
    monkeypatch.setattr(mm, "_read_exif_row", lambda p, r, rel, upn: {
        "UniquePictureName": upn, "File:FileName": p.name,
        "File:Directory": str(p.parent), "WindowsFileName": str(p),
        "WindowsExtension": p.suffix.lstrip("."), "ImageSize": "1600x1200"})
    return _Col(root, db)


def test_second_bake_gets_a_new_name_and_is_pinned(col):
    # p1 already has p1.jpg (tracked, 2000 wide): it must not be touched
    before = (col._master_root / FUSCUS / "p1.jpg").read_bytes()
    res = col._create_master_jpg("p1")
    assert res["status"] == "created"
    assert Path(res["jpg"]).name == "p1-JPG-2.jpg"
    assert res["sci"] == "larus fuscus"
    assert (col._master_root / FUSCUS / "p1.jpg").read_bytes() == before
    pic = col._master_db.picture("p1")
    assert "p1-JPG-2.jpg" in pic["Files"]
    assert pic["BestJPGPinned"] == "p1-JPG-2.jpg"       # smaller, but chosen
    assert pic["BestJPG"] == "p1-JPG-2.jpg"
    assert col._master_upn["p1"]["BestJPG"] == "p1-JPG-2.jpg"   # cards follow
    assert "p1-JPG-2.jpg" in col._master_files

    res2 = col._create_master_jpg("p1")
    assert Path(res2["jpg"]).name == "p1-JPG-3.jpg"


def test_read_only_master_makes_nothing(col):
    col._master_read_only = True
    assert col._create_master_jpg("p2")["status"] == "skipped"
    assert not (col._master_root / FUSCUS / "p2-JPG-2.jpg").exists()


def test_rescan_adds_a_photolab_export(col):
    (col._master_root / FUSCUS / "p2_DxO.jpg").write_bytes(b"export")
    res = col.rescan_master_picture("p2")
    assert res["added"] == ["p2_DxO.jpg"] and res["sci"] == "larus fuscus"
    assert "p2_DxO.jpg" in col._master_files
    assert col.rescan_master_picture("p2")["added"] == []
