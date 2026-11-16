"""Moving a master picture that HAS JPGs to another species (backlog 4.7, phase 0).

A tiny master in a temp folder:

    aves
    ├── laridae
    │   ├── larus fuscus      p1 (rate 5, JPG + .mov)   p2 (rate 3, JPG)
    │   └── larus argentatus  p3 (rate 5, JPG)
    └── sternidae
        └── sterna hirundo    p4 (rate 2, JPG)

p1 and p3 TIE at 5 on purpose: laridae's best-pick is p1, and an undo must win
it back rather than leave p3 there.

The invariants: every file moves (nothing trashed, nothing overwritten); both
species' JPG counters, best-picks and JPGmaps follow in the same write; and a
move followed by its undo leaves the master as it was.
"""
import csv
import json

from pathlib import Path

import pytest

from db import master_db as mdb
from db import master_mutate as mm
from engine.csv_columns import DATA_FILE_FIELDS, PICTURE_FIELDS, SPECIES_FIELDS

FUSCUS = "biota/aves/laridae/larus fuscus"
ARGENT = "biota/aves/laridae/larus argentatus"
STERNA = "biota/aves/sternidae/sterna hirundo"

# upn -> (species, folder, rate, {filename: ImageSize or ""})
PICTURES = {
    "p1": ("larus fuscus", FUSCUS, 5, {"p1.nef": "", "p1.xmp": "", "p1.jpg": "2000x1333",
                                      "p1-small.jpg": "600x400", "p1.mov": ""}),
    "p2": ("larus fuscus", FUSCUS, 3, {"p2.nef": "", "p2.jpg": "1200x800"}),
    "p3": ("larus argentatus", ARGENT, 5, {"p3.jpg": "1200x800"}),
    "p4": ("sterna hirundo", STERNA, 2, {"p4.jpg": "1200x800"}),
}


SUBJECTS = {"p1": "kleine mantelmeeuw, adult"}   # a keyword with a qualifier


def _species_rows():
    def sp(name, level, up, leaves, path, pics, jpgs, nspecies, best, rate, jpgmap=()):
        return {"ScientificName": name, "Level": level, "Up": up,
                "Leaves": str(list(leaves)), "TargetPath": path,
                "iPictureCollectionCount": pics, "iPicturesWithJPGsCollection": jpgs,
                "iSpeciesCollectionCount": nspecies,
                "BestUPN": best, "BestJPG": f"{best}.jpg" if best else "",
                "BestJPGPathName": PICTURES[best][1] if best else "",
                "currentHighestRating": rate, "JPGmap": str(list(jpgmap))}
    return [
        sp("aves", "Level.CLASS", "", ["laridae", "sternidae"], "biota/aves",
           5, 4, 3, "p1", 5),
        sp("laridae", "Level.FAMILY", "aves", ["larus fuscus", "larus argentatus"],
           "biota/aves/laridae", 4, 3, 2, "p1", 5),
        sp("sternidae", "Level.FAMILY", "aves", ["sterna hirundo"],
           "biota/aves/sternidae", 1, 1, 1, "p4", 2),
        sp("larus fuscus", "Level.SPECIES", "laridae", [], FUSCUS, 2, 2, 1, "p1", 5,
           ["p1.jpg", "p2.jpg"]),
        sp("larus argentatus", "Level.SPECIES", "laridae", [], ARGENT, 1, 1, 1, "p3", 5,
           ["p3.jpg"]),
        sp("sterna hirundo", "Level.SPECIES", "sternidae", [], STERNA, 1, 1, 1, "p4", 2,
           ["p4.jpg"]),
    ]


def _write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@pytest.fixture
def master(tmp_path, monkeypatch):
    """A master on disk + its database. The background CSV writer and ExifTool
    are stubbed: the test checks the database, and a test must not need ExifTool."""
    root = tmp_path / "master"
    pics, files = [], []
    for upn, (sci, rel, rate, fl) in PICTURES.items():
        folder = root / rel
        folder.mkdir(parents=True, exist_ok=True)
        jpgs = {f: s for f, s in fl.items() if f.endswith(".jpg") and "-small" not in f}
        best = max(jpgs, key=lambda f: int(jpgs[f].split("x")[0])) if jpgs else ""
        subject = SUBJECTS.get(upn, sci)
        pics.append({"UniquePictureName": upn, "WetSoortNaam": sci, "subject": subject,
                     "TargetPath": rel, "Rate": rate, "Files": str(list(fl)),
                     "BestJPG": best, "JPGSize": jpgs.get(best, ""),
                     "BestJPGPathName": rel})
        for f, size in fl.items():
            (folder / f).write_bytes(f"{upn}:{f}".encode())
            files.append({"UniquePictureName": upn, "File:FileName": f,
                          "File:Directory": str(folder),
                          "WindowsFileName": str(folder / f),
                          "WindowsExtension": f.rsplit(".", 1)[1], "ImageSize": size,
                          "XMP:Subject": subject if f.endswith((".xmp", ".jpg")) else ""})
    active = root / "metadata" / "active"
    _write_csv(active / "data_pictures.csv", PICTURE_FIELDS, pics)
    _write_csv(active / "data_files.csv", DATA_FILE_FIELDS, files)
    _write_csv(active / "species.csv", SPECIES_FIELDS, _species_rows())
    # The FULL species list: everything above, plus a family and a species the
    # master has never had, and a placeholder that is no species at all.
    coll = [{k: r[k] for k in ("Level", "ScientificName", "Up")} for r in _species_rows()]
    coll += [{"Level": "Level.FAMILY", "ScientificName": "rynchopidae", "Up": "aves",
              "DutchName": "schaarbekken"},
             {"Level": "Level.SPECIES", "ScientificName": "rynchops niger", "Up": "rynchopidae",
              "DutchName": "amerikaanse schaarbek", "EnglishName": "black skimmer"},
             {"Level": "Level.SPECIES", "ScientificName": "? meeuw", "Up": "laridae"}]
    _write_csv(active / "speciescollection.csv",
               ["Level", "ScientificName", "DutchName", "EnglishName", "Up", "Leaves"], coll)

    class _NoWorker:
        def notify(self):
            pass
    monkeypatch.setattr(mdb.MasterCheckpointWorker, "get", classmethod(lambda cls, r: _NoWorker()))
    import engine.exif_writer
    writes = []
    monkeypatch.setattr(engine.exif_writer, "exiftool_write",
                        lambda path, args, **k: writes.append((Path(path).name, args[0])))

    db = mdb.MasterDB(root, cache_name="test_move_jpg")
    db.rebuild()
    db.writes = writes            # the keyword writes, for the tests to read
    yield root, db
    db.close()


def _species(db, name):
    row = db._connect().execute(
        'SELECT * FROM species WHERE "ScientificName"=?', (name,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    for col in ("Leaves", "JPGmap"):
        d[col] = sorted(json.loads(d[col]) if d[col] else [])
    return d


def _state(db):
    con = db._connect()
    return ([dict(r) for r in con.execute('SELECT * FROM species ORDER BY "ScientificName"')],
            [dict(r) for r in con.execute('SELECT * FROM pictures ORDER BY "UniquePictureName"')],
            [dict(r) for r in con.execute('SELECT * FROM files ORDER BY "File:FileName"')])


def _disk(root):
    return sorted(str(p.relative_to(root)).replace("\\", "/")
                  for p in (root / "biota").rglob("*") if p.is_file())


def test_move_within_family_moves_files_and_rollups(master):
    root, db = master
    mm.move_picture_to_species(db, root, "p1", db.species_row("larus argentatus"))

    # every file moved — the .mov and the -small too — nothing left behind
    for f in PICTURES["p1"][3]:
        assert (root / ARGENT / f).exists(), f
        assert not (root / FUSCUS / f).exists(), f
    pic = db.picture("p1")
    assert pic["TargetPath"] == ARGENT and pic["BestJPGPathName"] == ARGENT
    assert pic["BestJPG"] == "p1.jpg"

    src, tgt = _species(db, "larus fuscus"), _species(db, "larus argentatus")
    assert src["iPicturesWithJPGsCollection"] == 1
    assert (src["BestUPN"], src["BestJPG"], src["currentHighestRating"]) == ("p2", "p2.jpg", 3)
    assert src["JPGmap"] == ["p2.jpg"]
    assert tgt["iPicturesWithJPGsCollection"] == 2
    assert tgt["BestUPN"] == "p3"            # a tie (5 = 5) keeps the incumbent
    assert tgt["JPGmap"] == ["p1.jpg", "p3.jpg"]   # never the -small thumbnail

    # the family and class still hold the picture: counts unchanged, folder updated
    for name in ("laridae", "aves"):
        a = _species(db, name)
        assert a["iPicturesWithJPGsCollection"] == {"laridae": 3, "aves": 4}[name]
        assert (a["BestUPN"], a["BestJPGPathName"]) == ("p1", ARGENT)


def test_move_across_families_and_undo_restores_everything(master):
    root, db = master
    before, disk_before = _state(db), _disk(root)

    r = mm.move_picture_to_species(db, root, "p1", db.species_row("sterna hirundo"))
    lar, ster = _species(db, "laridae"), _species(db, "sternidae")
    assert lar["iPicturesWithJPGsCollection"] == 2
    assert lar["BestUPN"] == "p3"            # recomputed without p1
    assert ster["iPicturesWithJPGsCollection"] == 2
    assert (ster["BestUPN"], ster["BestJPGPathName"], ster["currentHighestRating"]) \
        == ("p1", STERNA, 5)
    assert _species(db, "aves")["BestJPGPathName"] == STERNA

    mm.undo_move(db, root, r["batch"])
    assert _disk(root) == disk_before
    after = _state(db)
    assert after[1] == before[1]             # pictures
    assert after[2] == before[2]             # files
    assert after[0] == before[0]             # species — incl. laridae's tie won back


def test_move_that_empties_the_source_removes_it_and_undo_restores_it(master):
    root, db = master
    before = _state(db)
    r = mm.move_picture_to_species(db, root, "p3", db.species_row("larus fuscus"))
    assert _species(db, "larus argentatus") is None
    assert _species(db, "laridae")["Leaves"] == ["larus fuscus"]
    fus = _species(db, "larus fuscus")
    assert fus["iPicturesWithJPGsCollection"] == 3
    assert fus["BestUPN"] == "p1"            # tie: incumbent stays
    assert fus["JPGmap"] == ["p1.jpg", "p2.jpg", "p3.jpg"]

    mm.undo_move(db, root, r["batch"])
    assert _sorted_leaves(_state(db)[0]) == _sorted_leaves(before[0])
    assert _state(db)[1:] == before[1:]


def test_name_clash_refuses_before_anything_changes(master):
    root, db = master
    (root / STERNA / "p1.jpg").write_bytes(b"someone else")
    before, disk_before = _state(db), _disk(root)
    with pytest.raises(mm.MasterMutateError):
        mm.move_picture_to_species(db, root, "p1", db.species_row("sterna hirundo"))
    assert _state(db) == before
    assert _disk(root) == disk_before
    assert (root / STERNA / "p1.jpg").read_bytes() == b"someone else"


def test_keyword_keeps_qualifiers_and_undo_puts_it_back(master):
    root, db = master
    r = mm.move_picture_to_species(db, root, "p1", db.species_row("larus argentatus"))
    assert r["keyword_failed"] == []
    assert db.picture("p1")["subject"] == "larus argentatus, adult"
    written = dict(db.writes)
    # the sidecar and the JPGs carry the keyword; the RAW and the video don't
    assert set(written) == {"p1.xmp", "p1.jpg", "p1-small.jpg"}
    assert set(written.values()) == {"-XMP:Subject=larus argentatus, adult"}

    db.writes.clear()
    mm.undo_move(db, root, r["batch"])
    assert set(dict(db.writes).values()) == {"-XMP:Subject=kleine mantelmeeuw, adult"}
    assert db.picture("p1")["subject"] == "kleine mantelmeeuw, adult"


def test_new_to_master_species_and_family(master):
    root, db = master
    targets = {t["sci"] for t in mm.collection_species_targets(db, root)}
    assert targets == {"rynchops niger"}          # not the placeholder, not known ones

    before, disk_before = _state(db), _disk(root)
    target = next(t for t in mm.collection_species_targets(db, root))
    r = mm.move_picture_to_species(db, root, "p1", target)
    new_path = "biota/aves/rynchopidae/rynchops niger/"
    assert r["new_species"] == ["rynchopidae", "rynchops niger"]
    assert (root / new_path / "p1.jpg").exists()

    fam, sp = _species(db, "rynchopidae"), _species(db, "rynchops niger")
    assert fam["TargetPath"] == "biota/aves/rynchopidae/"
    assert fam["Leaves"] == ["rynchops niger"]
    assert "rynchopidae" in _species(db, "aves")["Leaves"]
    assert (sp["DutchName"], sp["TargetPath"]) == ("amerikaanse schaarbek", new_path)
    for row in (fam, sp):
        assert (row["iPictureCollectionCount"], row["iPicturesWithJPGsCollection"],
                row["iSpeciesCollectionCount"]) == (1, 1, 1)
        assert row["BestUPN"] == "p1"
    assert sp["JPGmap"] == ["p1.jpg"]
    assert db.picture("p1")["NLSoortNaam"] == "amerikaanse schaarbek"

    # and back: the new rows go again, nothing else changed
    mm.undo_move(db, root, r["batch"])
    assert _species(db, "rynchopidae") is None and _species(db, "rynchops niger") is None
    assert _state(db) == before
    assert _disk(root) == disk_before


def test_undo_goes_newest_first_and_refuses_a_stale_move(master):
    root, db = master
    first = mm.move_picture_to_species(db, root, "p1", db.species_row("larus argentatus"))
    second = mm.move_picture_to_species(db, root, "p1", db.species_row("sterna hirundo"))
    assert mm.last_move_batch(root)["batch"] == second["batch"]

    with pytest.raises(mm.MasterMutateError):     # the picture moved on since
        mm.undo_move(db, root, first["batch"])

    mm.undo_move(db, root, second["batch"])
    last = mm.last_move_batch(root)
    assert (last["batch"], last["to"]) == (first["batch"], "larus argentatus")
    mm.undo_move(db, root, first["batch"])
    assert mm.last_move_batch(root) is None
    assert db.picture("p1")["WetSoortNaam"] == "larus fuscus"


def test_the_csvs_hold_the_move(master):
    """The CSVs are the truth: write the journal out, rebuild the database from
    the CSVs alone, and it must hold exactly what the move produced — the new
    species rows included."""
    root, db = master
    target = next(t for t in mm.collection_species_targets(db, root))
    mm.move_picture_to_species(db, root, "p1", target)
    after_move = _state(db)
    assert db.checkpoint() >= 1
    db.rebuild()
    assert _state(db) == after_move


def test_a_group_to_a_new_species_moves_and_undoes_together(master):
    """Several selected pictures → one new species: the first move creates it,
    the second finds it known, and ONE undo (the group) puts both back."""
    root, db = master
    before, disk_before = _state(db), _disk(root)
    target = next(t for t in mm.collection_species_targets(db, root))
    for upn in ("p1", "p2"):
        mm.move_picture_to_species(db, root, upn, target, group="g1")
    assert _species(db, "rynchops niger")["iPictureCollectionCount"] == 2
    assert _species(db, "larus fuscus") is None          # emptied

    last = mm.last_move_batch(root)
    assert sorted(last["upns"]) == ["p1", "p2"] and len(last["batches"]) == 2
    for b in last["batches"]:                            # newest first
        mm.undo_move(db, root, b)
    assert mm.last_move_batch(root) is None
    assert _disk(root) == disk_before
    assert _state(db)[1:] == before[1:]                  # pictures + files exact
    # the species table too — larus fuscus came back from the snapshot after
    # the group emptied it; only the order of a Leaves list may differ (the
    # website sorts children by the species order, never by this list)
    assert _sorted_leaves(_state(db)[0]) == _sorted_leaves(before[0])


def _sorted_leaves(rows):
    return [{**r, "Leaves": sorted(json.loads(r["Leaves"] or "[]"))} for r in rows]
