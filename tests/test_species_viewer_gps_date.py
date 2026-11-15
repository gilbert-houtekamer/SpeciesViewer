"""Trip Species Viewer: paste / remove a hand-placed GPS and set a capture date
keep the picture rows and the day CSVs in step (RatingEditorCollection). No
exiftool and no files: the writes themselves are tested in test_remove_gps.py
and test_capture_date_write.py."""

from datetime import datetime
from types import SimpleNamespace


class _Table(SimpleNamespace):
    def write_project_summary(self):
        self.saved = getattr(self, "saved", 0) + 1

    def write_project_files(self):
        self.saved = getattr(self, "saved", 0) + 1


def _collection(rows, files=None):
    from gui.rating_provider import RatingEditorCollection
    fpr = SimpleNamespace(upn=_Table(UniquePictureName_dict=rows),
                          fi=_Table(files_dict=files or {}))
    c = RatingEditorCollection(fpr, None, {}, loader=object())
    c._order = list(rows)
    return c, fpr


def test_paste_then_remove_gps_round_trip():
    from engine.geo import parse_gps_dms
    c, fpr = _collection({"P1": {"Files": [], "City": "Ranohira"}})
    assert not c.gps_is_derived("P1")

    c.note_gps_written("P1", -18.766605, 47.398152)        # the paste
    row = fpr.upn.UniquePictureName_dict["P1"]
    lat, lon = parse_gps_dms(row["GPSPosition"])
    assert abs(lat + 18.766605) < 1e-5 and abs(lon - 47.398152) < 1e-5
    assert c.gps_is_derived("P1")
    assert fpr.upn.saved == 1                              # day saved

    c.note_gps_removed("P1")                               # the undo
    assert not row["GPSPosition"] and not row["GPSProcessingMethod"]
    assert not row["City"]
    assert not c.gps_is_derived("P1")


def test_camera_gps_is_not_removable():
    from gui import viewer_clipboard as clip
    c, _ = _collection({"P1": {"Files": [], "GPSPosition": "1 deg 0' 0.00\" N, 2 deg 0' 0.00\" E"}})
    assert not c.gps_is_derived("P1")
    assert clip.removable_gps_keys(c, ["P1"]) == []


def test_master_card_is_never_touched():
    c, fpr = _collection({"M1": {"Files": []}})
    c._master_keys = {"M1"}
    c._master_upn = {"M1": {}}
    c.note_gps_written("M1", 1.0, 2.0)
    assert "GPSPosition" not in fpr.upn.UniquePictureName_dict["M1"]
    assert c.date_targets("M1") == []


def test_capture_times_cover_the_whole_trip_and_date_is_saved():
    rows = {"P1": {"Files": [], "DateTimeOriginal": "2026:11:01 10:15:00"},
            "P2": {"Files": [], "DateTimeOriginal": ""},
            "OTHER": {"Files": [], "DateTimeOriginal": "2026:11:01 10:15:01"}}
    c, fpr = _collection(rows)
    c._order = ["P1", "P2"]                    # one species shown; OTHER is not
    assert datetime(2026, 11, 1, 10, 15, 1) in c.capture_times()
    c._dates_by_hand = {"P2": False}
    c.note_date_written("P2", "2026:11:01 10:15:02")
    assert rows["P2"]["DateTimeOriginal"] == "2026:11:01 10:15:02"
    assert c.date_is_by_hand("P2")
    assert fpr.upn.saved == 1
