"""The blog-index picture picker (gui/blog_cover_picker.py): it offers exactly
the pictures the day's blog was last built with, plus "Automatic"."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from gui.blog_cover_picker import BlogCoverPicker, blog_pictures_for_day

# Keep the application referenced: an unreferenced QApplication is collected
# at once and the next widget takes the process down.
_APP = QApplication.instance() or QApplication([])

HEADER = ("blog;title_nl;title_en;message_nl;message_en;blogjpg;Project;CreatedTime;"
          "SelectedGroup;OnlyNewSpecies;MinimumScore;BestJPGs;MaxPerSpecies\n")


def _trip(tmp_path):
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "blog_admin.csv").write_text(
        HEADER
        + "Blog Pictures 2026-10-19-2026-10-19-biota-0;t;t;m;m;;2026-10-19;x;biota;False;1;"
          "['a_DxO.jpg', 'b_DxO.jpg'];3\n"
        + "Blog Pictures 2026-10-10-2026-10-10-biota-0;t;t;m;m;;2026-10-10;x;biota;False;1;"
          "['c_DxO.jpg'];1\n",
        encoding="utf-8")
    return tmp_path


def test_reads_the_days_blog_pictures(tmp_path):
    trip = _trip(tmp_path)
    blog, names = blog_pictures_for_day(trip, "2026-10-19")
    assert blog == "Blog Pictures 2026-10-19-2026-10-19-biota-0"
    assert names == ["a_DxO.jpg", "b_DxO.jpg"]


def test_a_day_never_built_has_no_pictures(tmp_path):
    assert blog_pictures_for_day(_trip(tmp_path), "2026-10-25") == ("", [])
    assert blog_pictures_for_day(tmp_path / "nowhere", "2026-10-19") == ("", [])


def test_picker_offers_automatic_plus_the_blogs_pictures(tmp_path):
    picker = BlogCoverPicker(_trip(tmp_path), "2026-10-19", "biota", "b_DxO.jpg")
    assert picker._list.count() == 3
    assert picker.choice() == "b_DxO.jpg"
    picker._list.setCurrentRow(0)
    assert picker.choice() == ""                     # Automatic


def test_a_cover_no_longer_in_the_blog_falls_back_to_automatic(tmp_path):
    picker = BlogCoverPicker(_trip(tmp_path), "2026-10-19", "biota", "gone_DxO.jpg")
    assert picker.choice() == ""
    assert "no longer in the blog" in picker._note.text()
