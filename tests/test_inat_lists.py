"""iNaturalist country lists and country packs: saved files only (no internet in tests)."""
import json
import time
import zipfile

from engine import ai_measure, ai_models, country_pack, ebird, inat_lists


def _sandbox(tmp_path, monkeypatch):
    lists, birds = tmp_path / "species_lists", tmp_path / "ebird"
    lists.mkdir()
    birds.mkdir()
    monkeypatch.setattr(inat_lists, "folder", lambda: lists)
    monkeypatch.setattr(country_pack, "_ebird_dir", lambda: birds)
    monkeypatch.setattr(inat_lists, "_memo", {})

    def no_internet(*a, **k):
        raise AssertionError("asked iNaturalist")
    monkeypatch.setattr(inat_lists, "_fetch", no_internet)
    return lists, birds


def _save(lists, name, species, fetched=None):
    (lists / name).write_text(json.dumps({"fetched": fetched or time.time(), "place": 7783,
                                          "species": species}), encoding="utf-8")


def test_few_observations_left_out(tmp_path, monkeypatch):
    lists, _ = _sandbox(tmp_path, monkeypatch)
    _save(lists, "inat_MG_mammals.json", {"Lemur catta": [1262, "Ring-tailed Lemur"],
                                          "Panthera leo": [1, "Lion"]})
    assert inat_lists.species("MG", "mammals") == {"lemur catta"}
    assert inat_lists.species("MG", "mammals", min_records=1) == {"lemur catta", "panthera leo"}
    assert inat_lists.english_names("MG", ["mammals"])["lemur catta"] == "Ring-tailed Lemur"


def test_no_saved_list_and_no_internet_is_none(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)

    def offline(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(inat_lists, "_fetch", offline)
    assert inat_lists.species("MG", "reptiles") is None


def test_chosen_groups_default_mammals_and_per_country(monkeypatch):
    from utils.config import _config
    monkeypatch.setattr(_config, "get", lambda s, k, fallback="": {
        ("ai", "groups_mg"): "mammals, reptiles, nonsense"}.get((s, k.lower()), fallback))
    assert inat_lists.chosen_groups("NL") == ["mammals"]
    assert inat_lists.chosen_groups("MG") == ["mammals", "reptiles"]


def test_match_any_older_genus_by_english_name_or_epithet():
    clip = ai_models.BioClip.__new__(ai_models.BioClip)
    clip.sci = ["Furcifer pardalis", "Chamaeleo lateralis", "Anolis lateralis",
                "Phelsuma lineata", "Lygodactylus lineata"]
    clip.common = ["Panther Chameleon", "Carpet Chameleon", "", "Lined Day Gecko", ""]
    clip.group = {s: ("reptilia", "") for s in clip.sci}
    clip.group["Anolis lateralis"] = ("insecta", "")
    got = clip.match_any({"furcifer pardalis", "furcifer lateralis", "phelsuma lineatus"},
                         {"furcifer lateralis": "Carpet Chameleon"})
    # by English name; "lineatus" has no such epithet in BioCLIP → not matched
    assert got == {"furcifer pardalis", "chamaeleo lateralis"}


def test_inat_groups_only_add_to_ebird(monkeypatch):
    monkeypatch.setattr(ai_measure, "_matched", {})
    monkeypatch.setattr(ebird, "species", lambda r: frozenset({"lophotibis cristata"}) if r == "MG" else None)
    monkeypatch.setattr(ai_measure, "_as_bioclip", lambda r, l: frozenset(l))
    monkeypatch.setattr(ai_measure, "_as_bioclip_inat",
                        lambda r, g: frozenset({"lemur catta"}) if g else frozenset())
    allowed, words = ai_measure._allowed("MG", ["mammals"])
    assert allowed == {"lophotibis cristata", "lemur catta"}
    assert "iNaturalist's mammals" in words
    assert ai_measure._allowed("MG", [])[0] == {"lophotibis cristata"}
    assert ai_measure._allowed("XX", ["mammals"])[0] is None    # no eBird list → everything


def test_country_pack_round_trip_keeps_newer_lists(tmp_path, monkeypatch):
    (tmp_path / "big").mkdir()
    lists, birds = _sandbox(tmp_path / "big", monkeypatch)
    _save(lists, "inat_MG_mammals.json", {"Lemur catta": [5, ""]}, fetched=1000)
    _save(lists, "inat_MG_reptiles.json", {"Oplurus cuvieri": [5, ""]}, fetched=1000)
    _save(lists, "inat_NL_mammals.json", {"Vulpes vulpes": [5, ""]})
    (lists / "inat_places.json").write_text(json.dumps({"MG": 7783, "NL": 7506}))
    (birds / "spplist_MG.json").write_text("[]")
    (birds / "taxonomy.json").write_text("[]")
    out, names = country_pack.export("mg", tmp_path / "transfer")
    assert sorted(names) == ["ebird/spplist_MG.json", "ebird/taxonomy.json",
                             "species_lists/inat_MG_mammals.json", "species_lists/inat_MG_reptiles.json"]
    assert "NL" not in json.loads(zipfile.ZipFile(out).read(country_pack.MANIFEST))["places"]

    (tmp_path / "small").mkdir()
    lists2, _ = _sandbox(tmp_path / "small", monkeypatch)
    _save(lists2, "inat_MG_reptiles.json", {"Oplurus cuvieri": [9, ""]}, fetched=2000)   # newer here
    res = country_pack.unpack(out)
    assert "species_lists/inat_MG_mammals.json" in res["taken"]
    assert res["kept"] == ["species_lists/inat_MG_reptiles.json"]
    assert json.loads((lists2 / "inat_MG_reptiles.json").read_text())["fetched"] == 2000
    assert json.loads((lists2 / "inat_places.json").read_text()) == {"MG": 7783}
