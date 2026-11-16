"""Group names ("Oplurus sp.") and the one counting rule — uncertain IDs, phase 1.

A group is filed like a species (under its family, with a place in the species
order) but never counts: not in a family's species number, not as a lifer,
not numbered in Count Species. See engine/species_count.py.
"""
import csv

import pytest

from engine.species_count import (counts_as_species, group_base, group_name,
                                  is_group)
from engine.speciesclassification import Level, SpeciesClassification
from engine.subspecies import fold_species_flags, species_names


# ── the rule ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,base", [
    ("oplurus sp", "oplurus"), ("Oplurus sp.", "oplurus"), ("oplurus spec.", "oplurus"),
    ("oplurus spp", "oplurus"), ("  libellulidae SP.  ", "libellulidae"),
    ("oplurus cuvieri", ""), ("? oplurus", ""), ("sp.", ""), ("oplurus sp. x", ""),
    ("", ""), (None, ""),
])
def test_group_base(name, base):
    assert group_base(name) == base
    assert is_group(name) == bool(base)


def test_counts_as_species():
    assert counts_as_species("oplurus cuvieri")
    assert counts_as_species("corvus corone cornix")      # a subspecies still counts
    assert not counts_as_species("oplurus sp")
    assert not counts_as_species("oplurus sp.")
    assert not counts_as_species("? phacelia")
    assert not counts_as_species("")
    assert not counts_as_species(None)


def test_stored_name_has_no_trailing_dot():
    # the name is also a folder name: Windows drops a trailing dot, macOS keeps it
    assert group_name("Oplurus") == "oplurus sp"
    assert not group_name("oplurus").endswith(".")


def test_fold_skips_groups_and_placeholders():
    children = [("oplurus cuvieri", 1, 0), ("oplurus sp", 1, 0),
                ("oplurus quadrimaculatus", 0, 1), ("? oplurus", 0, 1)]
    assert fold_species_flags({}, children) == (1, 1)


def test_species_names_drops_groups():
    assert species_names({}, {"oplurus cuvieri", "oplurus sp", "? x"}) == {"oplurus cuvieri"}


# ── filing a group in the species list ────────────────────────────────────────

COLLECTION = [
    # Level, ScientificName, DutchName, EnglishName, Up, Sequence
    ("Level.BIOTA", "biota", "", "", "", ""),
    ("Level.KINGDOM", "animalia", "", "", "biota", ""),
    ("Level.PHYLUM", "chordata", "", "", "animalia", ""),
    ("Level.CLASS", "squamata", "", "", "chordata", ""),
    ("Level.ORDER", "iguania", "", "", "squamata", ""),
    ("Level.FAMILY", "opluridae", "", "", "iguania", "1"),
    ("Level.SPECIES", "oplurus cuvieri", "cuviers leguaan", "", "opluridae", "10"),
    ("Level.SPECIES", "oplurus quadrimaculatus", "", "", "opluridae", "11"),
    ("Level.SPECIES", "chalarodon madagascariensis", "", "", "opluridae", "20"),
]


@pytest.fixture
def sc(tmp_path):
    path = tmp_path / "speciescollection.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Level", "ScientificName", "DutchName", "EnglishName", "Up", "Leaves",
                    "Sequence"])
        for lvl, name, nl, en, up, seq in COLLECTION:
            w.writerow([lvl, name, nl, en, up, "[]", seq])
    s = SpeciesClassification(None)
    s.readSpeciesCollection(str(path))
    s._gbif_offline = True          # a test never asks GBIF
    return s


def test_genus_group_files_under_the_family_of_a_relative(sc):
    assert sc.getSpecies("Oplurus sp.") == "oplurus sp"
    row = sc.sc_dict["oplurus sp"]
    assert row["Level"] == Level.SPECIES and row["Up"] == "opluridae"
    assert "oplurus sp" in sc.sc_dict["opluridae"]["Leaves"]
    assert row["DutchName"] == row["EnglishName"] == "oplurus sp."
    assert "oplurus sp" in sc.learned               # it gets written to the list
    # after its relatives, before the next genus
    assert float(row["Sequence"]) == pytest.approx(15.5)


def test_family_group_files_under_the_family_itself(sc):
    assert sc.getSpecies("opluridae sp") == "opluridae sp"
    assert sc.sc_dict["opluridae sp"]["Up"] == "opluridae"
    assert float(sc.sc_dict["opluridae sp"]["Sequence"]) == 21


def test_a_group_is_found_again_by_its_label_and_spellings(sc):
    sc.getSpecies("oplurus sp.")
    n = len(sc.sc_dict)
    for typed in ("oplurus sp", "Oplurus sp.", "oplurus spec."):
        assert sc.getSpecies(typed) == "oplurus sp"
    assert len(sc.sc_dict) == n                    # no second row


def test_unknown_genus_offline_is_not_filed(sc):
    assert sc.getSpecies("phelsuma sp.") == ""
    assert "phelsuma sp" not in sc.sc_dict


def test_no_group_above_a_family(sc):
    assert sc.getSpecies("iguania sp.") == ""      # an order


def test_group_written_and_read_back(sc, tmp_path):
    sc.getSpecies("oplurus sp.")
    out = tmp_path / "out.csv"
    sc.writeSpeciesCollection(str(out))
    again = SpeciesClassification(None)
    again.readSpeciesCollection(str(out))
    assert again.sc_dict["oplurus sp"]["Up"] == "opluridae"
    assert again.getKnownSpecies("oplurus sp.", False) == "oplurus sp"   # by its label


# ── a trip-style rollup and the album table ───────────────────────────────────

def test_album_table_lists_a_group_without_a_number(sc):
    from engine.album_species import species_table
    sc.getSpecies("oplurus sp.")
    by_day = {"2026-11-01": [{"WetSoortNaam": "oplurus cuvieri"},
                             {"WetSoortNaam": "oplurus sp"}]}
    _days, rows = species_table(sc.sc_dict, by_day)
    nrs = {r["Scientific"]: r["Nr"] for r in rows}
    assert nrs == {"oplurus cuvieri": 1, "oplurus sp": ""}
