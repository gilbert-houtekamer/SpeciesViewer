"""Album Count Species: one row per species in list order, a star column in
place of New, per-day marks, Pics without Blue/Purple, and the no-JPG flag."""
from engine.album_species import species_table, write_report
from engine.specieslib import Level

SP = str(Level.SPECIES)
SC = {
    "biota": {"ScientificName": "biota", "Leaves": ["aves", "insecta"], "Level": "B"},
    "aves": {"ScientificName": "aves", "Up": "biota", "Leaves": ["anatidae"], "Level": "C"},
    "anatidae": {"ScientificName": "anatidae", "Up": "aves", "Leaves": ["anas crecca"],
                 "Level": "F"},
    "anas crecca": {"ScientificName": "anas crecca", "Up": "anatidae", "Level": SP,
                    "EnglishName": "teal", "DutchName": "wintertaling"},
    "insecta": {"ScientificName": "insecta", "Up": "biota", "Leaves": ["apis mellifera"],
                "Level": "C"},
    "apis mellifera": {"ScientificName": "apis mellifera", "Up": "insecta", "Level": SP,
                       "EnglishName": "honey bee", "DutchName": "honingbij"},
}


def test_table_and_report(tmp_path):
    by_day = {
        "2016-03-25": [
            {"WetSoortNaam": "Anas crecca", "Rating": "2", "Label": "Green", "BestJPG": "a.jpg"},
            {"WetSoortNaam": "anas crecca", "Rating": "0", "Label": "Blue", "BestJPG": ""},
        ],
        "2016-03-26": [
            {"WetSoortNaam": "apis mellifera", "Rating": "", "Label": "", "BestJPG": ""},
            {"WetSoortNaam": "", "Rating": "5", "Label": "", "BestJPG": "x.jpg"},
        ],
    }
    days, rows = species_table(SC, by_day)
    assert days == ["2016-03-25", "2016-03-26"]
    teal, bee = rows
    assert (teal["Group"], teal["Nr"], teal["Stars"], teal["Pics"]) == ("Birds", 1, 2, 1)
    assert teal["2016-03-25"] == "2" and teal["2016-03-26"] == "" and teal["JPG"] == "yes"
    assert teal["Blue"] == 1 and teal["Green"] == 1 and teal["Total"] == 2
    assert (bee["Group"], bee["Nr"], bee["2016-03-26"], bee["JPG"]) == ("Other", 1, "x", "no")
    txt, csv_path = write_report(tmp_path, "Test", "2016-03-25", "2016-03-26", days, rows)
    text = txt.read_text(encoding="utf-8")
    assert "No JPG" in text and "Totals: 2 species, 1 without a JPG." in text
    assert "New" not in csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
