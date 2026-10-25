"""Pictures without a capture date (WhatsApp downloads, pictures received from
others) stay out of the blog — but never silently (2026-10-25: two landscapes
vanished from the post without a word)."""

import logging
from types import SimpleNamespace

import engine.projectlib  # noqa: F401 — loads first, as in the app (the two import each other)
from engine import websupport
from engine.speciesclassification import SpeciesClassification
from batch import sv2ReadDirExif


def _project():
    upn_dict = {
        "owl": {"WetSoortNaam": "surnia ulula", "BestJPG": "D:/day/owl_DxO.jpg", "Rate": "75",
                "DateTimeOriginal": "2026:10:25 10:34:46", "subject": "sperweruil"},
        "land": {"WetSoortNaam": "", "BestJPG": "D:/day/vogelaars_DxO.jpg", "Rate": "2",
                 "DateTimeOriginal": "", "subject": "landscape"},
        "nojpg": {"WetSoortNaam": "", "BestJPG": "", "Rate": "0",
                  "DateTimeOriginal": "", "subject": "landscape"},
    }
    blog = SimpleNamespace(from_time="2026:10:25 00:00:00", last_time="2026:10:25 23:59:59")
    return SimpleNamespace(UniquePictureName_dict=upn_dict, Blog=blog)


def test_dateless_landscape_is_left_out_of_the_blog():
    selected = {}
    websupport.collectBlogExtras(_project(), selected)
    assert selected == {}


def test_blog_says_which_pictures_it_left_out_once(caplog):
    mp = _project()
    with caplog.at_level(logging.WARNING, logger="speciesviewer"):
        websupport.warnDatelessJPGs(mp)
        websupport.warnDatelessJPGs(mp)          # second language: no repeat
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "vogelaars_DxO.jpg" in warnings[0] and "WhatsApp" in warnings[0]
    assert "owl" not in warnings[0]


def test_read_step_warns_once_per_picture_not_per_file(caplog):
    rows = [
        {"File:FileName": "vogelaars.jpeg", "WindowsExtension": "jpeg",
         "UniquePictureName": "vogelaars", "EXIF:DateTimeOriginal": ""},
        {"File:FileName": "vogelaars_DxO.jpg", "WindowsExtension": "jpg",
         "UniquePictureName": "vogelaars", "EXIF:DateTimeOriginal": ""},
        {"File:FileName": "NA250082.ORF", "WindowsExtension": "orf",
         "UniquePictureName": "NA250082", "EXIF:DateTimeOriginal": "2026:10:25 10:34:46"},
        {"File:FileName": "NA250082.xmp", "WindowsExtension": "xmp",
         "UniquePictureName": "NA250082", "EXIF:DateTimeOriginal": ""},
    ]
    with caplog.at_level(logging.WARNING, logger="speciesviewer"):
        names = sv2ReadDirExif._warn_dateless(rows, {"jpeg", "jpg", "orf"})
    assert names == ["vogelaars.jpeg"]
    assert "No capture date on 1 picture(s)" in caplog.records[-1].getMessage()


def test_landscape_is_a_blog_extra_not_an_unknown_species():
    assert SpeciesClassification.isBlogExtra("landscape")
    assert SpeciesClassification.isBlogExtra("['landscape']")
    assert not SpeciesClassification.isBlogExtra("sperweruil")
