"""The site menu only offers groups the repository has pictures of (ch.7, 2026-10-18).

An album without fungi linked a Fungi page that was old, empty or missing.
"""
from types import SimpleNamespace

from engine.websupport import getTemplateOutputString

MENU_BIRDS = '\t\t<a href="%%90%%biota/animalia/chordata/aves/aves-en.html" class="w3-bar-item w3-button">Birds</a>'
MENU_FUNGI = '\t\t<a href="%%90%%biota/fungi/fungi-en.html" class="w3-bar-item w3-button">Fungi</a>'
LIST_FUNGI = '<li><a href="%%90%%biota/fungi/fungi-en.html">Fungi</a></li>'
IN_TEXT = 'and with the link <a href="%%89%%biota/biota-en.html">nature</a> you will go'


def _album(groups):
    tree = {"biota": {"iPicturesWithJPGsCollection": 1, "iPicturesWithJPGsThisRun": 0}}
    tree.update({g: {"iPicturesWithJPGsCollection": n, "iPicturesWithJPGsThisRun": 0}
                 for g, n in groups.items()})
    return SimpleNamespace(sp=SimpleNamespace(species_dict=tree))


def test_menu_leaves_out_groups_without_pictures():
    album = _album({"aves": 12, "mammalia": 0})
    assert getTemplateOutputString(album, None, "", "", MENU_FUNGI) == ""
    assert getTemplateOutputString(album, None, "", "", LIST_FUNGI) == ""
    assert getTemplateOutputString(_album({"aves": 0}), None, "", "", MENU_BIRDS) == ""


def test_menu_keeps_groups_with_pictures_and_text_links():
    album = _album({"aves": 12})
    out = getTemplateOutputString(album, None, "", "", MENU_BIRDS)
    assert out != "" and "Birds" in out
    assert getTemplateOutputString(album, None, "", "", IN_TEXT) != ""


def test_menu_unchanged_without_a_species_tree():
    """A repository being created has no tree yet: keep the whole menu."""
    assert getTemplateOutputString(SimpleNamespace(sp=None), None, "", "", MENU_FUNGI) != ""


def test_blog_pages_follow_the_whole_album():
    """A blog page holds one day's tree; menu_groups (the whole album) wins."""
    day = _album({"aves": 3})                    # this day: birds only
    day.menu_groups = {"biota", "plantae", "aves"}
    assert getTemplateOutputString(day, None, "", "", MENU_FUNGI) == ""
    plants = MENU_FUNGI.replace("fungi/fungi", "plantae/plantae").replace("Fungi", "Plants")
    assert getTemplateOutputString(day, None, "", "", plants) != ""


def test_album_groups_walks_up_from_pictures_with_a_jpg():
    from batch.album_source import album_groups
    tree = {"biota": {"Up": ""}, "plantae": {"Up": "biota"}, "rosa canina": {"Up": "plantae"},
            "animalia": {"Up": "biota"}, "fungi": {"Up": "biota"}, "amanita muscaria": {"Up": "fungi"}}
    db = SimpleNamespace(pictures_in_daterange=lambda a, b: {"2025-11-02": [
        {"WetSoortNaam": "rosa canina", "BestJPG": "r.jpg"},
        {"WetSoortNaam": "amanita muscaria", "BestJPG": ""},      # RAW only: no page
        {"WetSoortNaam": "not in the list", "BestJPG": "x.jpg"}]})
    assert album_groups(db, "2025-11-01", "2025-11-25", tree) == {"rosa canina", "plantae", "biota"}
