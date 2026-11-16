"""Certainty (uncertain IDs, phase 2): only a CERTAIN picture counts as a species.

Option A (16 Nov): a species row's iSpeciesCollectionCount means "counts as a
species" (1 with a counting picture, else 0; the sum above). "Has a page" is
the picture count. Uses the tiny master of test_master_move_jpg:

    aves
    ├── laridae    larus fuscus (p1, p2)   larus argentatus (p3)
    └── sternidae  sterna hirundo (p4)
"""
import csv

from test_master_move_jpg import _species, master  # noqa: F401  (fixture)

from db import master_mutate as mm
from engine.species_count import certainty_of


def _set_certainty(db, upn, word):
    """What the master's sex/age/plumage/certainty dialog does, minus ExifTool."""
    name = db.picture(upn)["WetSoortNaam"]
    subject = f"{name}, {word}" if word else name
    assert db.apply_qualifiers(upn, subject=subject, malefemale="", ageintext="",
                               summerwinter="")
    return subject


def _counts(db):
    return {n: _species(db, n)["iSpeciesCollectionCount"]
            for n in ("aves", "laridae", "sternidae", "sterna hirundo")}


def test_certainty_words():
    assert certainty_of("sterna hirundo, probable") == "probable"
    assert certainty_of("['sterna hirundo', 'female', 'cf.']") == "probable"
    assert certainty_of("sterna hirundo, ?") == "possible"
    assert certainty_of("sterna hirundo, adult") == ""
    assert certainty_of("? meeuw") == ""            # a placeholder is no certainty word


def test_probable_only_picture_stops_counting_and_back(master):
    root, db = master
    assert _counts(db) == {"aves": 3, "laridae": 2, "sternidae": 1, "sterna hirundo": 1}
    _set_certainty(db, "p4", "probable")
    assert db.picture("p4")["certainty"] == "probable"
    assert _counts(db) == {"aves": 2, "laridae": 2, "sternidae": 0, "sterna hirundo": 0}
    # the picture stays where it is: certainty is not a move
    assert _species(db, "sterna hirundo")["iPictureCollectionCount"] == 1
    _set_certainty(db, "p4", "")
    assert _counts(db) == {"aves": 3, "laridae": 2, "sternidae": 1, "sterna hirundo": 1}


def test_probable_beside_a_certain_picture_changes_nothing(master):
    root, db = master
    _set_certainty(db, "p1", "possible")              # p2 is still a certain fuscus
    assert _species(db, "larus fuscus")["iSpeciesCollectionCount"] == 1
    assert _species(db, "laridae")["iSpeciesCollectionCount"] == 2


def test_probable_picture_moved_to_a_new_species_adds_none(master):
    root, db = master
    _set_certainty(db, "p4", "probable")
    mm.move_picture_to_species(db, root, "p4", {"sci": "rynchops niger"})
    assert _species(db, "rynchops niger")["iPictureCollectionCount"] == 1
    assert _species(db, "rynchops niger")["iSpeciesCollectionCount"] == 0
    assert _species(db, "rynchopidae")["iSpeciesCollectionCount"] == 0
    assert _species(db, "aves")["iSpeciesCollectionCount"] == 2


def test_recount_counts_only_certain_pictures(master):
    root, db = master
    from engine import master_recount
    pics = root / "metadata" / "active" / "data_pictures.csv"
    with open(pics, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh, delimiter=";")
        header, rows = rd.fieldnames, list(rd)
    for r in rows:
        if r["UniquePictureName"] == "p4":
            r["certainty"] = "probable"
    with open(pics, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    _header, out, _changes = master_recount._recount(str(root))
    by = {r["ScientificName"]: r for r in out}
    assert by["sterna hirundo"]["iSpeciesCollectionCount"] == "0"
    assert by["sterna hirundo"]["iPictureCollectionCount"] == "1"   # still has its page
    assert by["sternidae"]["iSpeciesCollectionCount"] == "0"
    assert by["aves"]["iSpeciesCollectionCount"] == "2"


def test_freeze_recount_uses_counting_pictures():
    from batch.sv6Freeze import _recountSpeciesTotals

    def node(level, up, leaves=(), coll=0, pics=0, run=0):
        return {"Level": level, "Up": up, "Leaves": list(leaves),
                "iSpeciesCollectionCount": coll, "iSpeciesThisRunCount": 0,
                "iPictureCollectionCount": pics, "iPictureThisRunCount": run}
    day = {"biota": node("Level.BIOTA", "", ["fam"]),
           "fam": node("Level.FAMILY", "biota", ["a b", "c d", "e sp"]),
           "a b": node("Level.SPECIES", "fam", coll=1, pics=3),     # already counted
           "c d": node("Level.SPECIES", "fam", run=2),              # only probable today
           "e sp": node("Level.SPECIES", "fam", run=1)}             # a group
    assert _recountSpeciesTotals(day, {}, counting={"a b", "e sp"}) == (1, 0)
    day["c d"]["iSpeciesCollectionCount"] = 0
    assert _recountSpeciesTotals(day, {}, counting={"c d"}) == (2, 1)


def test_album_table_does_not_number_a_probable_only_species():
    from engine.album_species import species_table
    sc = {"biota": {"Level": "Level.BIOTA", "ScientificName": "biota", "Up": "",
                    "Leaves": ["fam"]},
          "fam": {"Level": "Level.FAMILY", "ScientificName": "fam", "Up": "biota",
                  "Leaves": ["a b", "c d"]},
          "a b": {"Level": "Level.SPECIES", "ScientificName": "a b", "Up": "fam", "Leaves": []},
          "c d": {"Level": "Level.SPECIES", "ScientificName": "c d", "Up": "fam", "Leaves": []}}
    from engine.speciesclassification import Level
    for k in ("a b", "c d"):
        sc[k]["Level"] = Level.SPECIES
    by_day = {"2026-11-01": [{"WetSoortNaam": "a b"},
                             {"WetSoortNaam": "c d", "certainty": "probable"}]}
    _days, rows = species_table(sc, by_day)
    assert {r["Scientific"]: r["Nr"] for r in rows} == {"a b": 1, "c d": ""}


# ── phase 3: the Uncertain review ─────────────────────────────────────────────

def test_is_uncertain_id():
    from engine.species_count import is_uncertain_id
    assert is_uncertain_id("oplurus sp.")
    assert is_uncertain_id("", "oplurus sp")
    assert is_uncertain_id("sterna hirundo, probable")
    assert is_uncertain_id("['sterna hirundo', 'female', '?']")
    assert is_uncertain_id("? phacelia")
    assert not is_uncertain_id("sterna hirundo, adult", "sterna hirundo")
    assert not is_uncertain_id("", "")


def test_master_lists_open_ids_with_their_family(master):
    root, db = master
    assert db.uncertain_pictures() == {}
    _set_certainty(db, "p4", "probable")
    mm.move_picture_to_species(db, root, "p3", {"sci": "larus sp"})
    assert db.uncertain_pictures() == {
        "p4": {"sci": "sterna hirundo", "family": "sternidae"},
        "p3": {"sci": "larus sp", "family": "laridae"}}
    _set_certainty(db, "p4", "")                       # resolved: it drops out
    assert set(db.uncertain_pictures()) == {"p3"}


def test_health_flags_a_wrong_count(master):
    root, db = master
    from db import master_health
    _set_certainty(db, "p4", "probable")
    rep = master_health.check(db, root)
    assert not any("count wrongly" in f.what for f in rep.findings)
    # a row that counts without a certain picture is a bug signal
    con = db._connect()
    with con:
        db._set_species_fields(con, "sterna hirundo", {"iSpeciesCollectionCount": 1})
    rep = master_health.check(db, root)
    assert any("1 species row(s) count wrongly" in f.what for f in rep.findings)
