"""place_queries: search texts from the place written on a picture."""
from gui.map_view import place_queries


def test_full_chain_then_shorter():
    q = place_queries({"Location": "Huguenot Memorial Park, Jacksonville",
                       "City": "Jacksonville", "State": "Florida",
                       "Country": "United States"})
    assert q == ["Huguenot Memorial Park, Jacksonville, Florida, United States",
                 "Huguenot Memorial Park, Jacksonville, United States",
                 "Huguenot Memorial Park, Jacksonville"]


def test_town_only():
    assert place_queries({"City": "Yerseke", "Country": "Netherlands"}) == \
        ["Yerseke, Netherlands"]


def test_nothing_written():
    assert place_queries({"Location": "", "City": None}) == []
    assert place_queries(None) == []
