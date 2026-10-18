"""A species and its subspecies count once (ch.6 subspecies step 2, 2026-10-18)."""
from engine.subspecies import fold_species_flags, species_names, species_of

SC = {
    "corvus corone": {"SubspeciesOf": ""},
    "corvus corone cornix": {"SubspeciesOf": "corvus corone"},
    "cyanocorax yncas": {"SubspeciesOf": ""},
    "cyanocorax yncas luxuosus": {"SubspeciesOf": "cyanocorax yncas"},
    "pica pica": {"SubspeciesOf": ""},
}


def test_species_of():
    assert species_of(SC, "corvus corone cornix") == "corvus corone"
    assert species_of(SC, "pica pica") == "pica pica"
    assert species_of(SC, "not in the list") == "not in the list"


def test_species_and_subspecies_count_once():
    family = [("corvus corone", 1, 0), ("corvus corone cornix", 1, 0), ("pica pica", 1, 0)]
    assert fold_species_flags(SC, family) == (2, 0)


def test_only_the_subspecies_pictured_still_counts():
    assert fold_species_flags(SC, [("cyanocorax yncas luxuosus", 1, 0)]) == (1, 0)


def test_new_subspecies_of_a_known_species_is_not_new():
    assert fold_species_flags(SC, [("corvus corone", 1, 0), ("corvus corone cornix", 0, 1)]) == (1, 0)
    assert fold_species_flags(SC, [("corvus corone", 0, 1), ("corvus corone cornix", 0, 1)]) == (0, 1)


def test_groups_above_the_family_pass_through():
    assert fold_species_flags(SC, [("corvidae", 31, 2), ("laniidae", 9, 0)]) == (40, 2)


def test_lifer_baseline_by_species():
    assert "corvus corone" in species_names(SC, {"corvus corone cornix"})
