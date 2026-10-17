"""The alias table (speciesnames.csv) and contested names (ch.6, 2026-10-17).

An old name that still means the same bird resolves through the alias table.
An old name that a split handed to ANOTHER species ("mangrovereiger": Butorides
striata before IOC 15.2, B. atricapilla after) resolves to nothing — the
species step asks rather than filing the picture under the wrong bird.
"""
import csv

import engine.projectlib  # noqa: F401  (import order: projectlib before websupport users)
from engine.speciesclassification import SpeciesClassification

HEADER = ["Level", "ScientificName", "DutchName", "EnglishName", "Up", "Leaves", "Sequence", "SubspeciesOf"]


def _write(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


def _load(tmp_path, aliases):
    _write(tmp_path / "speciescollection.csv", HEADER, [
        ["Level.CLASS", "aves", "vogels", "birds", "", "", "1", ""],
        ["Level.SPECIES", "butorides striata", "amerikaanse mangrovereiger", "striated heron", "aves", "", "1", ""],
        ["Level.SPECIES", "butorides atricapilla", "mangrovereiger", "green-backed heron", "aves", "", "2", ""],
        ["Level.SPECIES", "agelaioides badius", "grijze koevogel", "greyish baywing", "aves", "", "3", ""],
    ])
    _write(tmp_path / "speciesnames.csv", ["Name", "Language", "PointsTo", "Kind", "Source"], aliases)
    sc = SpeciesClassification(None)
    sc.readSpeciesCollection(str(tmp_path / "speciescollection.csv"))
    return sc


def test_old_name_of_the_same_bird_resolves(tmp_path):
    sc = _load(tmp_path, [["bruine koevogel", "nl", "agelaioides badius", "older Dutch name", "before IOC 15.2"]])
    assert sc.getKnownSpecies("bruine koevogel", False) == "agelaioides badius"
    assert sc.getKnownSpecies("grijze koevogel", False) == "agelaioides badius"
    assert not sc.contested


def test_name_a_split_gave_to_another_bird_is_not_used(tmp_path):
    sc = _load(tmp_path, [["mangrovereiger", "nl", "butorides striata", "older Dutch name", "before IOC 15.2"]])
    assert sc.contested == {"mangrovereiger"}
    assert sc.getKnownSpecies("mangrovereiger", False) == ""
    assert "mangrovereiger" not in sc.dutch_dict          # nor offered in the species editor
    # the new names and the scientific names still work
    assert sc.getKnownSpecies("amerikaanse mangrovereiger", False) == "butorides striata"
    assert sc.getKnownSpecies("butorides atricapilla", False) == "butorides atricapilla"
