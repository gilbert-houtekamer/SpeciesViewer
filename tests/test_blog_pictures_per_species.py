"""The blog's "pictures per species" (engine.websupport.collectBestJPGs,
2026-10-21): the best N of each species — per sex / age / plumage — by rating;
N = 1 is the old behaviour."""

from types import SimpleNamespace

import engine.projectlib  # noqa: F401 — loads first, as in the app (the two import each other)
from engine import websupport

SPECIES = "anser erythropus"


def _project(max_per_species):
    pictures = {
        # upn: (rating, sex, time)
        "m1": (40, "male", "2026:10:10 08:00:00"),
        "m2": (90, "male", "2026:10:10 08:05:00"),
        "m3": (60, "male", "2026:10:10 08:10:00"),
        "f1": (30, "female", "2026:10:10 09:00:00"),
        "f2": (70, "female", "2026:10:10 09:05:00"),
        "x9": (99, "male", "2026:10:11 08:00:00"),          # another day
    }
    upn_dict, files = {}, {}
    for upn, (rate, sex, when) in pictures.items():
        jpg = f"{upn}.jpg"
        upn_dict[upn] = {"WetSoortNaam": SPECIES, "BestJPG": jpg, "Rate": str(rate),
                         "DateTimeOriginal": when, "sex": sex}
        files[jpg] = {"UniquePictureName": upn}
    blog = SimpleNamespace(from_time="2026:10:10 00:00:00", last_time="2026:10:10 23:59:59",
                           MaxPerSpecies=max_per_species)
    return SimpleNamespace(UniquePictureName_dict=upn_dict, files_dict=files, Blog=blog,
                           getPictureAgeSexePlumage=lambda upn: upn["sex"])


def _chosen(n):
    selected = {}
    websupport.collectBestJPGs(_project(n), SPECIES, selected)
    return sorted(selected)


def test_one_per_species_is_the_best_of_each_sex():
    assert _chosen(1) == ["f2.jpg", "m2.jpg"]


def test_two_per_species_are_the_two_best_of_each_sex():
    assert _chosen(2) == ["f1.jpg", "f2.jpg", "m2.jpg", "m3.jpg"]


def test_more_than_there_are_shows_them_all_from_that_day_only():
    assert _chosen(8) == ["f1.jpg", "f2.jpg", "m1.jpg", "m2.jpg", "m3.jpg"]
