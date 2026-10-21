"""Re-filing master pictures by hand into a species the master has no picture of
yet: move the files (SV closed), Refresh Master Database, Recount (2026-10-21).

Before: Refresh repointed the files but skipped the species change (no existing
row to copy names from), so the pictures kept the old species and went black;
Recount never added the new species. A tiny master on disk, the real functions."""

import ast
import csv
import os
import shutil

import pytest

from engine import admin_rebuild, master_recount

FAMILY = "biota/animalia/chordata/aves/gruiformes/rallidae"
OLD, NEW = "rallus longirostris", "rallus obsoletus"


def _write(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in header})


def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


SPECIES_HDR = ["Level", "ScientificName", "DutchName", "EnglishName", "Up", "Leaves", "JPGmap",
               "currentHighestRating", "BestJPG", "BestUPN", "BestJPGPathName", "TargetPath",
               "iSpeciesThisRunCount", "iSpeciesCollectionCount", "iPictureThisRunCount",
               "iPictureCollectionCount", "iPicturesWithJPGsThisRun", "iPicturesWithJPGsCollection"]


@pytest.fixture
def master(tmp_path):
    root = tmp_path / "naturedb"
    active = root / "metadata" / "active"
    old_dir = root / FAMILY / OLD
    old_dir.mkdir(parents=True)
    files, pics = [], []
    for n, with_jpg in (("7599", True), ("7479", False)):
        names = [f"2016 november us-DS5_{n}.NEF", f"2016 november us-DS5_{n}.xmp"]
        if with_jpg:
            names.append(f"2016 november us-DS5_{n}.jpg")
        for f in names:
            (old_dir / f).write_bytes(b"x")
            files.append({"File:FileName": f, "WindowsFileName": (old_dir / f).as_posix(),
                          "File:Directory": old_dir.as_posix()})
        tp = f"{FAMILY}/{OLD}/"
        pics.append({"UniquePictureName": f"2016 november us-ds5_{n}", "WetSoortNaam": OLD,
                     "NLSoortNaam": "klapperral", "EngSoortNaam": "mangrove rail",
                     "TargetPath": tp, "BestJPGPathName": tp if with_jpg else "",
                     "BestJPG": names[-1] if with_jpg else "", "Rate": "48" if with_jpg else "5",
                     "Files": str(names)})
    _write(str(active / "data_files.csv"), ["File:FileName", "WindowsFileName", "File:Directory"], files)
    _write(str(active / "data_pictures.csv"),
           ["UniquePictureName", "WetSoortNaam", "NLSoortNaam", "EngSoortNaam", "TargetPath",
            "BestJPGPathName", "BestJPG", "Rate", "Files"], pics)
    tree = [("Level.BIOTA", "biota", "", "biota/"), ("Level.FAMILY", "rallidae", "biota", f"{FAMILY}/")]
    rows = [{"Level": l, "ScientificName": s, "Up": u, "TargetPath": t, "Leaves": "[]", "JPGmap": "[]"}
            for l, s, u, t in tree]
    rows[0]["Leaves"] = "['rallidae']"
    rows[1]["Leaves"] = f"['{OLD}']"
    rows.append({"Level": "Level.SPECIES", "ScientificName": OLD, "DutchName": "grijze ral",
                 "EnglishName": "mangrove rail", "Up": "rallidae", "Leaves": "[]",
                 "JPGmap": "['2016 november us-DS5_7599.jpg']", "TargetPath": f"{FAMILY}/{OLD}/",
                 "BestJPG": "2016 november us-DS5_7599.jpg", "BestUPN": "2016 november us-ds5_7599",
                 "BestJPGPathName": f"{FAMILY}/{OLD}/", "currentHighestRating": "48",
                 "iSpeciesCollectionCount": "1", "iPictureCollectionCount": "2",
                 "iPicturesWithJPGsCollection": "1"})
    _write(str(active / "species.csv"), SPECIES_HDR, rows)
    _write(str(active / "speciescollection.csv"),
           ["Level", "ScientificName", "DutchName", "EnglishName", "Up", "Leaves", "Sequence", "SubspeciesOf"],
           [{"Level": "Level.SPECIES", "ScientificName": NEW, "DutchName": "californische ral",
             "EnglishName": "ridgway's rail", "Up": "rallidae", "Leaves": "[]"},
            {"Level": "Level.SPECIES", "ScientificName": OLD, "DutchName": "grijze ral",
             "EnglishName": "mangrove rail", "Up": "rallidae", "Leaves": "[]"}])
    return root


def _move_all(root, to_folder):
    src, dst = root / FAMILY / OLD, root / FAMILY / to_folder
    dst.mkdir()
    for f in list(src.iterdir()):
        shutil.move(str(f), str(dst / f.name))


def test_refresh_refiles_into_a_species_new_to_the_master(master):
    _move_all(master, NEW)
    assert admin_rebuild.plan_rebuild(str(master))["species_change"] == 2
    r = admin_rebuild.execute_heal(str(master))
    assert r["ok"] and r["species_changed"] == 2 and r["species_unresolved"] == []
    pics = _read(str(master / "metadata" / "active" / "data_pictures.csv"))
    for p in pics:
        assert p["WetSoortNaam"] == NEW
        assert (p["NLSoortNaam"], p["EngSoortNaam"]) == ("californische ral", "ridgway's rail")
        assert p["TargetPath"].lower() == f"{FAMILY}/{NEW}/"
    best = next(p for p in pics if p["BestJPG"])
    assert best["BestJPGPathName"].lower() == f"{FAMILY}/{NEW}/"


def test_recount_adds_the_species_and_moves_its_jpgs(master):
    _move_all(master, NEW)
    admin_rebuild.plan_rebuild(str(master))
    admin_rebuild.execute_heal(str(master))
    master_recount.apply(str(master))
    sp = {r["ScientificName"]: r for r in _read(str(master / "metadata" / "active" / "species.csv"))}
    new, old, fam = sp[NEW], sp[OLD], sp["rallidae"]
    assert (new["Level"], new["Up"], new["DutchName"]) == ("Level.SPECIES", "rallidae", "californische ral")
    assert new["TargetPath"] == f"{FAMILY}/{NEW}/"
    assert NEW in ast.literal_eval(fam["Leaves"])
    assert ast.literal_eval(new["JPGmap"]) == ["2016 november us-DS5_7599.jpg"]
    assert ast.literal_eval(old["JPGmap"]) == []
    assert (new["iPictureCollectionCount"], new["iSpeciesCollectionCount"]) == ("2", "1")
    assert (old["iPictureCollectionCount"], old["iSpeciesCollectionCount"]) == ("0", "0")
    assert new["BestUPN"] == "2016 november us-ds5_7599" and old["BestUPN"] == ""
    assert fam["iPictureCollectionCount"] == "2"
    assert master_recount.plan(str(master))["changes"] == []     # a second run: nothing left


def test_a_folder_naming_no_species_is_refused_before_anything_is_written(master):
    _move_all(master, "rallus obsoletis")                     # typo
    admin_rebuild.plan_rebuild(str(master))
    active = master / "metadata" / "active"
    before = (active / "data_files.csv").read_bytes(), (active / "data_pictures.csv").read_bytes()
    r = admin_rebuild.execute_heal(str(master))
    assert not r["ok"] and "rallus obsoletis" in r["error"]
    assert ((active / "data_files.csv").read_bytes(), (active / "data_pictures.csv").read_bytes()) == before
