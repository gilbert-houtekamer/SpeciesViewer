"""Trip Species Viewer ▸ AI…: the viewer hands the day AI its trip pictures and
writes colours through its own route — never on a frozen day, never names."""
from types import SimpleNamespace

import pytest

from gui.rating_editor import RatingEditorDialog as V


class _Col:
    def __init__(self, tmp_path, rows):
        self.rows, self.labels, self.frozen = rows, {}, {"p3"}
        self.files = {}
        for k in rows:
            raw = tmp_path / f"{k}.ORF"
            raw.write_bytes(b"x" * 10)
            self.files[k] = raw
        self.files["p4"] = tmp_path / "p4.MOV"
        self.files["p4"].write_bytes(b"v")

    def is_master(self, k): return k == "m1"
    def raw_path(self, k): return self.files.get(k) if k != "p4" else None
    def jpg_path(self, k): return self.files["p4"] if k == "p4" else None
    def _upn_row(self, k): return dict(self.rows.get(k, {}), Label=self.labels.get(k, ""))
    def is_frozen_day(self, k): return k in self.frozen

    def apply_colour_many(self, keys, colour):
        ok = {k for k in keys if k not in self.frozen}
        for k in ok:
            self.labels[k] = colour
        return ok, set(keys) - ok


def _viewer(tmp_path):
    rows = {f"p{i}": {"DateTimeOriginal": f"2024:06:25 10:0{i}:00", "Rating": "0",
                      "Label": "", "Country": "Zuid-Afrika", "WetSoortNaam": "ploceus"}
            for i in range(5)}
    rows["m1"] = dict(rows["p0"])
    col = _Col(tmp_path, rows)
    v = SimpleNamespace(_collection=col, fpr=SimpleNamespace(
        upn=SimpleNamespace(UniquePictureName_dict=rows)),
        _sync_all_cards=lambda: None, _relayout=lambda force=False: None)
    v.ai_colour_of = lambda k: V.ai_colour_of(v, k)
    return v


def test_the_trip_hands_over_its_own_pictures(tmp_path):
    pics, region = V.ai_pictures(_viewer(tmp_path))
    assert [p["key"] for p in pics] == ["p0", "p1", "p2", "p3"]   # no master, no video
    assert region == "ZA" and pics[0]["src"].suffix == ".ORF"
    assert not any(p["jpg"] for p in pics)


def test_colours_land_but_never_on_a_frozen_day_and_no_names(tmp_path):
    v = _viewer(tmp_path)
    done, failed = V.ai_write_colours(v, {"p1": "Purple", "p3": "Purple"})
    assert done == {"p1"} and failed == {"p3"}
    assert V.ai_colour_of(v, "p1") == "Purple" and V.ai_colour_of(v, "p3") == ""
    assert V.ai_can_write_species(v) is False


def test_any_trip_picture_has_a_card_also_another_species():
    from gui.rating_provider import RatingEditorCollection
    col = RatingEditorCollection(None, {}, {"a": "2024-06-25", "b": "2024-06-25"})
    col._order = ["a"]                     # the species in view holds only "a"
    assert col.item_for_key("b").key == "b"


def _have_exiftool():
    import os
    import shutil
    try:
        from engine.exif_writer import exiftool_exe
        exe = exiftool_exe()
        return bool(exe) and bool(shutil.which(exe) or os.path.exists(exe))
    except Exception:
        return False


@pytest.mark.skipif(not _have_exiftool(), reason="exiftool not installed")
def test_one_write_for_many_pictures_checked_per_file(tmp_path, monkeypatch):
    """apply_colour_many: one exiftool write, read back per file; a frozen day
    and a sidecar exiftool refuses are failed and keep their row."""
    import gui.rating_provider as rp
    from gui.rating_provider import RatingEditorCollection
    monkeypatch.setattr(rp, "color_rating_to_xmp_enabled", lambda: True)
    monkeypatch.setattr(rp, "color_rating_to_dop_enabled", lambda: False)
    xmp = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf='
           '"http://www.w3.org/1999/02/22-rdf-syntax-ns#"></rdf:RDF></x:xmpmeta>')
    files, rows, days = {}, {}, {}
    for k in ("a", "b", "c", "d"):
        f = tmp_path / f"{k}.xmp"
        if k != "d":                       # d: its sidecar is gone — cannot be written
            f.write_text(xmp, encoding="utf-8")
        files[f.name] = {"WindowsExtension": "xmp", "WindowsFileName": str(f)}
        rows[k] = {"Files": [f.name], "Label": "", "Rating": "0"}
        days[k] = "2024-06-25"
    fpr = SimpleNamespace(fi=SimpleNamespace(files_dict=files, write_project_files=lambda: None),
                          upn=SimpleNamespace(UniquePictureName_dict=rows,
                                              write_project_summary=lambda: None))
    col = RatingEditorCollection(fpr, None, days, frozen_days=set())
    col._frozen_days = {"frozen"}
    col._upn_day_map["c"] = "frozen"
    (tmp_path / "b.xmp").write_bytes(bytes([0, 1]) + b" damaged")   # b: exiftool refuses it
    done, failed = col.apply_colour_many(["a", "b", "c", "d"], "Purple")
    assert done == {"a", "d"} and failed == {"b", "c"}   # d: no file, so the index only
    assert rows["a"]["Label"] == "Purple"
    assert rows["b"]["Label"] == rows["c"]["Label"] == ""   # the row follows the file
    assert "Purple" in (tmp_path / "a.xmp").read_text(encoding="utf-8")
    assert "Purple" not in (tmp_path / "c.xmp").read_text(encoding="utf-8")


def test_accept_covers_only_the_species_shown():
    """AI…: with one species (or group) shown on its own, Accept plans only
    those pictures; with all shown, the whole set."""
    import gui.day_ai_dialog as d
    D = d.DayAiDialog
    grid = SimpleNamespace(ai_can_write_species=lambda: False, ai_species_of=lambda k: "",
                           ai_colour_of=lambda k: "")
    pics = [{"key": k, "verdict": "purple", "name": None} for k in ("a", "b", "c")]
    me = SimpleNamespace(_grid=grid, _pics=pics, _GROUP_NAMES=D._GROUP_NAMES,
                         _group_on=None, _species_on="sp:blue waxbill",
                         _groups_keys={"sp:blue waxbill": ["a", "b"], "faulty": ["b", "c"]})
    me._shown_only = lambda: D._shown_only(me)
    assert set(D._plan_with_new(me)[1]) == {"a", "b"}
    me._group_on = "faulty"                     # a group AND a species: their overlap
    assert set(D._plan_with_new(me)[1]) == {"b"}
    assert D._shown_label(me) == "not good enough · blue waxbill"
    me._group_on = me._species_on = None
    assert set(D._plan_with_new(me)[1]) == {"a", "b", "c"}
