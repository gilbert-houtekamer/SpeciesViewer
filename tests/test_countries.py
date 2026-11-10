"""engine.countries: every spelling in the master resolves; unknown passes through."""
from engine.countries import country_name, inventory, iso_of

# The distinct Country values in the main master (docs/design-docs/landcodes.xlsx).
MASTER_SPELLINGS = {
    "Belgie": "BE", "Belgium": "BE", "Brazil": "BR", "Brazilie": "BR",
    "Canada": "CA", "Costa Rica": "CR", "Denmark": "DK", "Duitsland": "DE",
    "Ecuador": "EC", "Ecuados": "EC", "England": "GB", "Eswatini": "SZ",
    "France": "FR", "Frankrijk": "FR", "Friesland": "NL", "Germany": "DE",
    "Greece": "GR", "Kenya": "KE", "Marokko": "MA", "Morocco": "MA",
    "Nederland": "NL", "Netherlands": "NL", "Oman": "OM", "Oostvoorne": "NL",
    "Poland": "PL", "South Africa": "ZA", "Spain": "ES", "Spanje": "ES",
    "Sri Lanka": "LK", "Tanzania": "TZ", "Texas": "US", "The Gambia": "GM",
    "UK": "GB", "United Kingdom": "GB", "United States": "US", "USA": "US",
    "Verenigde Staten van Amerika": "US", "Zweden": "SE",
}


def test_every_master_spelling_resolves():
    wrong = {s: iso_of(s) for s, iso in MASTER_SPELLINGS.items() if iso_of(s) != iso}
    assert not wrong


def test_case_accent_and_space_insensitive():
    assert iso_of("belgië") == iso_of("BELGIE") == iso_of(" Belgie ") == "BE"
    assert iso_of("Côte d’Ivoire") == iso_of("cote d'ivoire") == "CI"


def test_display_name_per_language():
    assert country_name("Zuid-Afrika", "en") == "South Africa"
    assert country_name("South Africa", "nl") == "Zuid-Afrika"
    assert country_name("USA", "nl") == "Verenigde Staten"
    assert country_name("Swaziland", "en") == "Eswatini"
    assert country_name("ZA", "nl") == "Zuid-Afrika"


def test_unknown_passes_through_unchanged():
    assert iso_of("Atlantis") == ""
    assert country_name("Atlantis", "nl") == "Atlantis"
    assert country_name("", "en") == ""


def test_inventory_lists_unknown_first():
    rows = inventory({"Nederland": 50, "Atlantis": 1, "Texas": 3, "": 9})
    assert [r[0] for r in rows] == ["Atlantis", "Nederland", "Texas"]
    assert [r[3] for r in rows] == ["unknown", "name", "alias"]
    assert rows[2][5]          # Texas carries its "not a country" note
