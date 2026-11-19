"""Freeze's free-space pre-flight: plans the exact copy, refuses before copying.

A tiny fake project (one genus, one species, two pictures) is enough: the plan
uses the same walk as the real copy, so what we check here is that the plan
lists the right files, counts only what is new, and that a short disk refuses
with a sentence while an ample one lets the copy proceed.
"""
import types

import pytest

import batch.sv6Freeze as sv6
from engine.specieslib import Level


def _project(tmp_path, master=True):
    src = tmp_path / "pictures"
    src.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (src / "a.orf").write_bytes(b"x" * 1000)
    (src / "a.jpg").write_bytes(b"x" * 300)
    (src / "b.orf").write_bytes(b"x" * 500)
    # b.orf is already in the repo at full size: an overwrite adds nothing
    target = repo / "biota" / "birds" / "robin"
    target.mkdir(parents=True)
    (target / "b.orf").write_bytes(b"x" * 500)

    species = {
        "biota": {"TargetPath": "biota", "Level": Level.BIOTA, "Leaves": ["birds"],
                  "iPictureThisRunCount": 2, "iPicturesWithJPGsThisRun": 1},
        "birds": {"TargetPath": "biota/birds", "Level": Level.FAMILY, "Leaves": ["robin"],
                  "iPictureThisRunCount": 2, "iPicturesWithJPGsThisRun": 1},
        "robin": {"TargetPath": "biota/birds/robin", "Level": Level.SPECIES, "Leaves": [],
                  "iPictureThisRunCount": 2, "iPicturesWithJPGsThisRun": 1},
    }
    files = {n: {"WindowsFileName": str(src / n), "File:FileName": n}
             for n in ("a.orf", "a.jpg", "b.orf")}
    upns = {
        "a": {"WetSoortNaam": "robin", "Files": ["a.orf", "a.jpg"],
              "TargetPath": "biota/birds/robin"},
        "b": {"WetSoortNaam": "robin", "Files": ["b.orf"],
              "TargetPath": "biota/birds/robin"},
    }
    return types.SimpleNamespace(
        sp=types.SimpleNamespace(species_dict=species),
        files_dict=files, UniquePictureName_dict=upns,
        getRepositoryPath=lambda: str(repo),
        isMasterRepository=lambda: master,
    )


def test_plan_touches_nothing(tmp_path):
    proj = _project(tmp_path)
    plan = []
    sv6.makeDirectories(proj, "biota", "New", copy_gate="pictures", planner=plan)
    assert sorted(p[1].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in plan) == \
        ["a.jpg", "a.orf", "b.orf"]
    assert not (tmp_path / "repo" / "biota" / "birds" / "robin" / "a.orf").exists()


def test_ample_disk_proceeds(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.disk_space.free_bytes", lambda _p: 10 << 30)
    assert sv6._check_room_for_copy(_project(tmp_path)) is None


def test_short_disk_refuses_counting_only_new_bytes(tmp_path, monkeypatch):
    seen = {}

    def fake_check(dest, needed, what="", **_kw):
        seen["needed"], seen["what"] = needed, what
        return "Not enough free space."
    monkeypatch.setattr("utils.disk_space.check_free_space", fake_check)
    msg = sv6._check_room_for_copy(_project(tmp_path))
    assert msg.startswith("Not enough free space.") and "Nothing was copied" in msg
    assert seen["needed"] == 1300            # a.orf + a.jpg; b.orf already there
    assert "3 file(s) into the master" in seen["what"]


def test_trip_mode_uses_jpg_gate(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr("utils.disk_space.check_free_space",
                        lambda dest, needed, what="", **_kw: seen.update(what=what))
    proj = _project(tmp_path, master=False)
    for sp in proj.sp.species_dict.values():
        sp["iPicturesWithJPGsThisRun"] = 0      # nothing developed this run
    assert sv6._check_room_for_copy(proj) is None
    assert "0 file(s) into the trip" in seen["what"]
