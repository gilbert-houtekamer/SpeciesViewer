"""The Master Species Viewer's collection may PLACE master pictures on the map.

The gate (location_targets) opens for master pictures only when the master is
writable here; a placed coordinate shows in the in-memory row at once and is
movable (MANUAL); a read-only master gives the map nothing to write.
"""
from pathlib import Path

from test_master_move_jpg import master, PICTURES, FUSCUS  # noqa: F401  (fixture)
from test_master_make_jpg import _Col


def test_targets_are_the_master_sidecar_and_jpgs(master):
    root, db = master
    col = _Col(root, db)
    assert col.allow_geotag
    targets = sorted(Path(t).name for t in col.location_targets("p1"))
    assert targets == ["p1-small.jpg", "p1.jpg", "p1.xmp"]
    # a RAW with no sidecar gets its <stem>.xmp (exiftool creates it)
    assert sorted(Path(t).name for t in col.location_targets("p2")) == ["p2.jpg", "p2.xmp"]


def test_read_only_master_has_nothing_to_write(master):
    root, db = master
    col = _Col(root, db, read_only=True)
    assert not col.allow_geotag
    assert col.location_targets("p1") == []
    assert col.gps_is_derived("p1") is False


def test_placed_coordinate_shows_and_is_movable(master):
    root, db = master
    col = _Col(root, db)
    assert col.gps_cached("p2") is None and not col.gps_is_derived("p2")
    col.note_gps_written("p2", 51.5, 4.25)
    lat, lon = col.gps_cached("p2")
    assert abs(lat - 51.5) < 1e-3 and abs(lon - 4.25) < 1e-3
    assert col.gps_is_derived("p2")


def test_place_name_fills_but_keeps_a_typed_location(master):
    root, db = master
    col = _Col(root, db)
    col._master_upn["p2"]["Location"] = "Oesterdam"
    col.note_location_written("p2", {"Location": "Tholen", "Country": "Nederland"},
                              protect=("Location",))
    assert col._master_upn["p2"]["Location"] == "Oesterdam"
    assert col._master_upn["p2"]["Country"] == "Nederland"
