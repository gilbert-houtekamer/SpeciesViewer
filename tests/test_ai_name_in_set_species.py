"""Set species offers the AI's name (AI… on the grid): a button that puts the
scientific name in the field; a species not in your list is looked up online
first. Headless; GBIF and the recents are stubbed."""

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication, QPushButton


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _SC:
    sc_dict = {"oplurus cuvieri": {"DutchName": "madagaskarleguaan", "EnglishName": "Cuvier's Madagascar Swift"}}

    def getKnownSpecies(self, text, *a, **k):
        return text if text in self.sc_dict else ""


def _widget(monkeypatch, ai):
    import gui.culling_species as cs
    monkeypatch.setattr(cs, "load_recent_species", lambda: [])
    monkeypatch.setattr(cs, "species_name_candidates", lambda sc: [])
    monkeypatch.setattr(cs, "get_setting", lambda *a, **k: "no")
    looked_up = []
    monkeypatch.setattr(cs, "gbif_match", lambda name: (looked_up.append(name), ("none", ""))[1])
    return cs.CullingSpeciesWidget(_SC(), "", 6, None, ai_name=ai), looked_up


def _ai_button(w):
    return next(b for b in w.findChildren(QPushButton) if b.text().startswith("AI: "))


def test_known_species_fills_field_without_going_online(app, monkeypatch):
    w, looked_up = _widget(monkeypatch, {"name": "madagaskarleguaan", "sci": "Oplurus cuvieri",
                                         "prob": 0.98, "how": "exact", "count": 4, "of": 6})
    b = _ai_button(w)
    assert b.text() == "AI: madagaskarleguaan · Oplurus cuvieri  98 %  (4 of 6 pictures)"
    b.click()
    assert w.field.text() == "oplurus cuvieri"
    assert w._is_known and looked_up == []


def test_new_species_is_looked_up_online(app, monkeypatch):
    w, looked_up = _widget(monkeypatch, {"name": "Thick tail gecko", "sci": "Uroplatus fimbriatus",
                                         "prob": 0.52, "how": ""})
    _ai_button(w).click()
    assert w.field.text() == "uroplatus fimbriatus"
    assert looked_up == ["uroplatus fimbriatus"]          # the SCIENTIFIC name goes to GBIF
    assert w.minimumSizeHint().width() < 700


def test_no_ai_name_no_button(app, monkeypatch):
    w, _ = _widget(monkeypatch, None)
    assert not [b for b in w.findChildren(QPushButton) if b.text().startswith("AI: ")]


def test_grid_picks_the_name_most_pictures_carry():
    from gui.image_viewer import ImagePreviewDialog as Grid
    grid = Grid.__new__(Grid)
    grid._ai_names = {"a": {"sci": "Oplurus cuvieri", "prob": 0.6},
                      "b": {"sci": "Oplurus cuvieri", "prob": 0.9},
                      "c": {"sci": "Chalarodon madagascariensis", "prob": 0.99}}
    got = Grid.ai_name_for(grid, ["a", "b", "c", "d"])
    assert (got["sci"], got["prob"], got["count"], got["of"]) == ("Oplurus cuvieri", 0.9, 2, 4)
    assert Grid.ai_name_for(grid, ["d"]) is None


def test_pipeline_panel_offers_ai_name_and_validates(app, monkeypatch, tmp_path):
    """A day already in the trip pipeline opens the other Set species panel."""
    import gui.species_editor as se
    monkeypatch.setattr(se, "set_setting", lambda *a, **k: None)
    monkeypatch.setattr(se, "get_setting", lambda *a, **k: "no")
    asked = []

    class SC(_SC):
        def getSpecies(self, name, fuzzy=False):
            asked.append(name)
            return name

    class Fpr:
        sc = SC()

        def __getattr__(self, name):            # getSubject, getErrorMsg, …
            return lambda *a, **k: ""
    panel = se.SpeciesInfoPanel(Fpr(), [tmp_path / "a.orf"], [""], [""],
                                ai_name={"name": "Thick tail gecko", "sci": "Uroplatus fimbriatus",
                                         "prob": 0.52, "how": ""})
    b = next(b for b in panel.findChildren(QPushButton) if b.text().startswith("AI: "))
    b.click()
    assert panel.subject_field.field.text() == "uroplatus fimbriatus"
    assert asked == ["uroplatus fimbriatus"]        # Validate ran (GBIF for a new name)
