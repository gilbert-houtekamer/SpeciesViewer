"""The Kingdom…Family picture columns hold each group's own name (2026-10-18).

They used to hold the parent: Kingdom 'biota', Family the order.
"""
import pytest

from engine import speciesclassification, specieslib
from engine.projectlib import taxon_columns


def _node(level, name, up):
    return {"Level": level, "ScientificName": name, "Up": up}


def _tree(Level):
    return {n["ScientificName"]: n for n in [
        _node(Level.BIOTA, "biota", ""),
        _node(Level.KINGDOM, "animalia", "biota"),
        _node(Level.PHYLUM, "chordata", "animalia"),
        _node(Level.CLASS, "aves", "chordata"),
        _node(Level.ORDER, "passeriformes", "aves"),
        _node(Level.FAMILY, "corvidae", "passeriformes"),
        _node(Level.SPECIES, "pica pica", "corvidae"),
        _node(Level.SPECIES, "odd one", "passeriformes"),      # tree skips the family
    ]}


# The app's tree uses speciesclassification.Level; specieslib has its own.
LEVELS = pytest.mark.parametrize("Level", [speciesclassification.Level, specieslib.Level])


@LEVELS
def test_each_column_holds_its_own_group(Level):
    tree = _tree(Level)
    assert taxon_columns(tree, tree["pica pica"]) == {
        "Kingdom": "animalia", "Phylum": "chordata", "Class": "aves",
        "Order": "passeriformes", "Family": "corvidae"}


@LEVELS
def test_a_skipped_level_stays_empty(Level):
    tree = _tree(Level)
    cols = taxon_columns(tree, tree["odd one"])
    assert cols["Family"] == "" and cols["Order"] == "passeriformes"
