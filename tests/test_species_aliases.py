"""Local names learned on a trip (engine/species_aliases.py, ch.6, 2026-10-26).

A guide's local name is kept as an alias of a scientific name — refused when it
would mean two species — and it travels: merging two tables is a union that
never drops or changes a row, so a name learned on the laptop reaches the master.
"""
import csv
import zipfile

import engine.projectlib  # noqa: F401  (import order: projectlib before websupport users)
from engine import trip_transfer
from engine.species_aliases import (add_alias, alias_problem, merge_alias_files,
                                    read_aliases)
from engine.speciesclassification import SpeciesClassification

HEADER = ["Level", "ScientificName", "DutchName", "EnglishName", "Up", "Leaves", "Sequence", "SubspeciesOf"]
FIELDS = ["Name", "Language", "PointsTo", "Kind", "Source"]


def _write(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


def _load(folder, aliases=()):
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / "speciescollection.csv", HEADER, [
        ["Level.CLASS", "aves", "vogels", "birds", "", "", "1", ""],
        ["Level.SPECIES", "coua cristata", "kuifcoua", "crested coua", "aves", "", "1", ""],
        ["Level.SPECIES", "vanga curvirostris", "haakbekvanga", "hook-billed vanga", "aves", "", "2", ""],
    ])
    _write(folder / "speciesnames.csv", FIELDS, list(aliases))
    sc = SpeciesClassification(None)
    sc.readSpeciesCollection(str(folder / "speciescollection.csv"))
    return sc


def test_a_kept_local_name_resolves_after_reload(tmp_path):
    sc = _load(tmp_path)
    assert alias_problem(sc, "tivoka", "coua cristata") == ""
    add_alias([str(tmp_path / "speciesnames.csv")], "Tivoka", "coua cristata", source="2026 Madagascar")
    again = SpeciesClassification(None)
    again.readSpeciesCollection(str(tmp_path / "speciescollection.csv"))
    assert again.getKnownSpecies("tivoka", False) == "coua cristata"


def test_a_name_that_already_means_another_species_is_refused(tmp_path):
    sc = _load(tmp_path, [["tivoka", "local", "coua cristata", "local name", ""]])
    assert alias_problem(sc, "tivoka", "vanga curvirostris")      # alias of the other bird
    assert alias_problem(sc, "kuifcoua", "vanga curvirostris")    # Dutch name of the other bird
    assert alias_problem(sc, "aves", "coua cristata")             # itself a taxon
    assert alias_problem(sc, "tivoka", "coua cristata") == ""     # the same pairing is fine


def test_adding_twice_writes_once_and_master_equal_to_local_is_written_once(tmp_path):
    _load(tmp_path)
    path = str(tmp_path / "speciesnames.csv")
    assert add_alias([path, path], "tivoka", "coua cristata") == [path]
    assert add_alias([path], "tivoka", "coua cristata") == []
    assert len(read_aliases(path)[1]) == 1


def test_merge_is_a_union_that_keeps_every_row(tmp_path):
    laptop = tmp_path / "laptop.csv"
    master = tmp_path / "master.csv"
    _write(laptop, FIELDS, [["tivoka", "local", "coua cristata", "local name", "trip"],
                            ["shared", "nl", "vanga curvirostris", "older Dutch name", ""]])
    _write(master, FIELDS, [["shared", "nl", "vanga curvirostris", "older Dutch name", ""],
                            ["only here", "en", "coua cristata", "renamed", ""]])
    assert merge_alias_files(str(laptop), str(master)) == 1
    names = [r["Name"] for r in read_aliases(str(master))[1]]
    assert names == ["shared", "only here", "tivoka"]
    assert merge_alias_files(str(laptop), str(master)) == 0       # idempotent


def test_merge_into_a_missing_table_creates_it(tmp_path):
    src = tmp_path / "src.csv"
    _write(src, FIELDS, [["tivoka", "local", "coua cristata", "local name", ""]])
    dest = tmp_path / "new" / "speciesnames.csv"
    assert merge_alias_files(str(src), str(dest)) == 1
    assert read_aliases(str(dest))[0] == FIELDS


def test_trip_zip_carries_the_table(tmp_path):
    zpath = tmp_path / "trip.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(trip_transfer.ALIASES_REL,
                    "Name;Language;PointsTo;Kind;Source\r\ntivoka;local;coua cristata;local name;trip\r\n")
    dest = tmp_path / "bigboy_trip"
    assert trip_transfer.merge_aliases(str(zpath), str(dest)) == 1
    assert trip_transfer.merge_aliases(str(zpath), str(dest)) == 0
    with zipfile.ZipFile(tmp_path / "old.zip", "w") as zf:
        zf.writestr("trip.ini", "")
    assert trip_transfer.merge_aliases(str(tmp_path / "old.zip"), str(dest)) is None
