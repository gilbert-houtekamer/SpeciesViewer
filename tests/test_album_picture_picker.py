"""Album picture picker + blog-day picture picker: what they offer."""
from pathlib import Path


def test_range_pictures_best_per_species_on_disk(tmp_path):
    from gui.album_picture_picker import range_pictures
    folder = tmp_path / "biota" / "x"
    folder.mkdir(parents=True)
    for n in ("a1.jpg", "a2.jpg", "b1.jpg"):
        (folder / n).write_bytes(b"jpg")
    row = lambda upn, sci, rate, jpg: {"UniquePictureName": upn, "WetSoortNaam": sci,
                                        "EngSoortNaam": sci.split()[-1], "Rate": rate,
                                        "BestJPG": jpg, "TargetPath": "biota/x/"}
    by_day = {"2015-04-18": [row("a1", "Aa aa", "5", "a1.jpg"),
                             row("b1", "Bb bb", "45", "b1.jpg")],
              "2015-04-19": [row("a2", "Aa aa", "30", "a2.jpg"),
                             row("c1", "Cc cc", "90", "missing.jpg")]}   # not on disk
    got = range_pictures(tmp_path, by_day)
    assert [p.name for p, _c, _d in got] == ["b1.jpg", "a2.jpg"]   # best first, per species
    assert got[0][1] == "Bb"                                       # caption capitalised


def test_blog_pictures_found_for_an_album_day(tmp_path):
    from gui.blog_cover_picker import blog_pictures_for_day
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "blog_admin.csv").write_text(
        "blog;Project;SelectedGroup;BestJPGs\n"
        "Blog Pictures 2015-04-18--biota-0;;biota;['x dsc_1.jpg', 'x dsc_2.jpg']\n",
        encoding="utf-8")
    blog, names = blog_pictures_for_day(tmp_path, "2015-04-18")
    assert blog == "Blog Pictures 2015-04-18--biota-0"
    assert names == ["x dsc_1.jpg", "x dsc_2.jpg"]
    assert blog_pictures_for_day(tmp_path, "2015-04-19") == ("", [])


def test_set_ini_min_score_changes_only_that_line(tmp_path):
    from batch.svv3WriteBlog import _set_ini_min_score, _read_blog_ini
    ini = tmp_path / "blog_2015-04-18.ini"
    ini.write_bytes("[blog_config]\r\nonly_new = False\r\nminimum_score = 6\r\n"
                    "[blog_text]\r\ntitel_nl = Mindo ♀\r\n".encode("utf-8"))
    assert _set_ini_min_score(ini, 1)
    raw = ini.read_bytes().decode("utf-8")
    assert "minimum_score = 1\r\n" in raw and "titel_nl = Mindo ♀\r\n" in raw
    assert _read_blog_ini(ini)["minimum_score"] == 1
    ini.write_text("[blog_config]\nonly_new = False\n", encoding="utf-8")
    assert _set_ini_min_score(ini, 30)
    assert _read_blog_ini(ini)["minimum_score"] == 30


def test_album_own_banner_wins_over_registry(tmp_path):
    from engine.triplib import Trip
    t = Trip.__new__(Trip)
    t.tripname = "fuerteventura"
    t.trip_dict = {"fuerteventura": {"trip_name": "fuerteventura",
                                     "trip_banner": "tripbanner.jpg",
                                     "trip_picture": "tripimage.jpg"}}
    t.useOwnImages({"trip_banner": "2016 maart dsc_1455.jpg", "trip_picture": ""})
    row = t.trip_dict["fuerteventura"]
    assert row["trip_banner"] == "2016 maart dsc_1455.jpg"
    assert row["trip_picture"] == "tripimage.jpg"           # empty never blanks

    (tmp_path / "2016 maart dsc_1455.jpg").write_bytes(b"x")
    sent = []

    class FakeSftp:
        def cachedUpload(self, src, dst, preserve_mtime=True):
            sent.append(dst)
    assert t.uploadOwnImages(FakeSftp(), tmp_path, "/trips/fuerteventura/") == \
        ["2016 maart dsc_1455.jpg"]                         # tripimage.jpg missing: logged
    assert sent == ["/trips/fuerteventura/2016 maart dsc_1455.jpg"]
