"""A trip's species list is a copy of the master's from when the trip began. A
species the master learned since (AI… "names not in your list") must still be
known to the trip — taken from the master, never "a typo" (2024-06-25:
angolablauwfazantje, afrikaanse bisschopsooievaar)."""
from engine import speciesclassification as scm

HEADER = "Level;ScientificName;DutchName;EnglishName;Up;Leaves;Sequence;SubspeciesOf\n"
TOP = [
    "Level.BIOTA;biota;biota;biota;;['animalia'];;",
    "Level.KINGDOM;animalia;dieren;animals;biota;['chordata'];;",
    "Level.PHYLUM;chordata;;;animalia;['aves'];;",
    "Level.CLASS;aves;vogels;birds;chordata;['passeriformes'];;",
    "Level.ORDER;passeriformes;zangvogels;;aves;[];;",
]
MASTER = TOP[:-1] + [
    "Level.ORDER;passeriformes;zangvogels;;aves;['estrildidae'];;",
    "Level.FAMILY;estrildidae;prachtvinken;;passeriformes;['uraeginthus angolensis'];;",
    "Level.SPECIES;uraeginthus angolensis;angolablauwfazantje;blue waxbill;estrildidae;[];29561.0;",
]


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def test_a_name_the_master_learned_reaches_the_trip(tmp_path, monkeypatch):
    master_root = tmp_path / "naturedb"
    _write(master_root / "metadata" / "active" / "speciescollection.csv", MASTER)
    trip_list = tmp_path / "tripdb" / "metadata" / "active" / "speciescollection.csv"
    _write(trip_list, TOP)
    from utils.config import _config
    real_get = _config.get
    monkeypatch.setattr(_config, "get", lambda sec, key, **kw: str(master_root)
                        if (sec, key) == ("master", "masterbiotadatabase") else real_get(sec, key, **kw))
    scm._MASTER_SC.clear()

    sc = scm.SpeciesClassification(None)
    sc.readSpeciesCollection(str(trip_list), False)
    assert sc.getKnownSpecies("angolablauwfazantje", False) == "uraeginthus angolensis"
    assert sc.sc_dict["estrildidae"]["Up"] == "passeriformes"            # the family came along
    assert "estrildidae" in sc.sc_dict["passeriformes"]["Leaves"]
    assert sc.sc_dict["uraeginthus angolensis"]["Up"] == "estrildidae"
    assert {"estrildidae", "uraeginthus angolensis"} <= sc.learned     # the trip keeps them
    assert sc.getKnownSpecies("blue waxbill", False) == "uraeginthus angolensis"
    assert sc.getKnownSpecies("no such bird", False) == ""


def test_the_master_itself_does_not_ask_itself(tmp_path, monkeypatch):
    master_root = tmp_path / "naturedb"
    path = master_root / "metadata" / "active" / "speciescollection.csv"
    _write(path, TOP)
    from utils.config import _config
    real_get = _config.get
    monkeypatch.setattr(_config, "get", lambda sec, key, **kw: str(master_root)
                        if (sec, key) == ("master", "masterbiotadatabase") else real_get(sec, key, **kw))
    sc = scm.SpeciesClassification(None)
    sc.readSpeciesCollection(str(path), False)
    assert scm._master_collection(str(path)) is None
