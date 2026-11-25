"""Settings ▸ AI species lists: the per-country choice of groups. The config is
never written (persist_config stubbed); no internet (only saved lists read)."""
import json
import time

import pytest

from engine import ebird, inat_lists


@pytest.fixture
def ai_config(monkeypatch, tmp_path):
    import utils.config as uc
    monkeypatch.setattr(uc, "persist_config", lambda: None)
    before = dict(uc._config.items("ai")) if uc._config.has_section("ai") else None
    if uc._config.has_section("ai"):
        uc._config.remove_section("ai")
    lists, birds = tmp_path / "species_lists", tmp_path / "ebird"
    lists.mkdir()
    birds.mkdir()
    monkeypatch.setattr(inat_lists, "folder", lambda: lists)
    monkeypatch.setattr(ebird, "_dir", lambda: birds)
    yield uc._config, lists, birds
    uc._config.remove_section("ai")
    if before is not None:
        uc._config.add_section("ai")
        for k, v in before.items():
            uc._config.set("ai", k, v)


def test_set_remove_and_fallback(ai_config):
    cfg, _, _ = ai_config
    assert inat_lists.countries_set() == {"": ["mammals"]}
    inat_lists.set_groups("nl", ["dragonflies", "mammals", "nonsense"])
    assert cfg.get("ai", "groups_nl") == "mammals, dragonflies"      # GROUPS order
    assert inat_lists.chosen_groups("NL") == ["mammals", "dragonflies"]
    inat_lists.set_groups("MG", [])                                   # none ≠ the others'
    assert inat_lists.chosen_groups("MG") == []
    inat_lists.set_groups("", ["mammals", "reptiles"])
    assert inat_lists.chosen_groups("TZ") == ["mammals", "reptiles"]
    assert set(inat_lists.countries_set()) == {"", "NL", "MG"}
    inat_lists.remove_country("MG")
    assert inat_lists.chosen_groups("MG") == ["mammals", "reptiles"]


def test_saved_counts_without_internet(ai_config):
    _, lists, birds = ai_config
    (lists / "inat_MG_plants.json").write_text(json.dumps(
        {"fetched": time.time(), "place": 7783,
         "species": {"Adansonia rubrostipa": [50, ""], "Ficus x": [1, ""]}}))
    (birds / "spplist_MG.json").write_text(json.dumps(["a", "b", "c"]))
    assert inat_lists.saved("MG", "plants")[0] == 1          # seen 3+ times only
    assert inat_lists.saved("MG", "fungi") is None
    assert ebird.saved("MG")[0] == 3 and ebird.saved("NL") is None


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])     # kept: a dropped one crashes


def test_window_shows_the_countries(app, ai_config):
    from gui import ai_countries_dialog as acd
    _, lists, birds = ai_config
    (birds / "spplist_MG.json").write_text(json.dumps(["a"] * 284))
    inat_lists.set_groups("MG", ["mammals", "plants"])
    d = acd.AiCountriesDialog()
    t = d._table
    rows = [t.item(i, 0).text() for i in range(t.rowCount())]
    assert rows[-1] == acd.OTHERS and any("(MG)" in r for r in rows)
    mg = next(i for i, r in enumerate(rows) if "(MG)" in r)
    assert t.item(mg, 1).text() == "Birds, Mammals, Plants"
    assert "284 birds" in t.item(mg, 2).text()
    assert "not fetched yet: mammals, plants" in t.item(mg, 2).text()
    g = acd.GroupsDialog("MG", ["plants"])
    assert g.chosen() == ["plants"]
    from PyQt6.QtWidgets import QPushButton
    btn = {b.text(): b for b in g.findChildren(QPushButton)}
    btn["All"].click()
    assert len(g.chosen()) == len(inat_lists.GROUPS)
    btn["None"].click()
    assert g.chosen() == []
    assert d.minimumSizeHint().width() < 900
