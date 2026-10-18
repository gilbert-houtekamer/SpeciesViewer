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


# A species with THREE subspecies (none in the master yet, 2026-10-18).
SC3 = {"motacilla flava": {"SubspeciesOf": ""},
       "motacilla flava feldegg": {"SubspeciesOf": "motacilla flava"},
       "motacilla flava flavissima": {"SubspeciesOf": "motacilla flava"},
       "motacilla flava thunbergi": {"SubspeciesOf": "motacilla flava"}}


def test_three_subspecies_count_once():
    family = [("motacilla flava", 1, 0), ("motacilla flava feldegg", 1, 0),
              ("motacilla flava flavissima", 0, 1), ("motacilla flava thunbergi", 1, 0)]
    assert fold_species_flags(SC3, family) == (1, 0)
    only_ssp = [("motacilla flava feldegg", 0, 1), ("motacilla flava thunbergi", 0, 1)]
    assert fold_species_flags(SC3, only_ssp) == (0, 1)


def test_biota_browser_puts_three_subspecies_after_their_species():
    from gui.biota_source import MasterBiotaSource
    src = MasterBiotaSource.__new__(MasterBiotaSource)
    names = ["anthus pratensis", "motacilla flava thunbergi", "motacilla alba",
             "motacilla flava", "motacilla flava feldegg", "motacilla flava flavissima"]
    src._by_sci = {n: {"sci": n, "up": "motacillidae"} for n in names}
    src._kids = {"motacillidae": names}
    src._pics = {n: 5 for n in names}
    src._own_pics = dict(src._pics)
    src._subs = {k: {"SubspeciesOf": v["SubspeciesOf"]} for k, v in SC3.items() if v["SubspeciesOf"]}
    order = [n["sci"] for n in src.children("motacillidae")]
    i = order.index("motacilla flava")
    assert set(order[i + 1:i + 4]) == {"motacilla flava feldegg", "motacilla flava flavissima",
                                       "motacilla flava thunbergi"}
    assert [n["sci"] for n in src.subspecies("motacilla flava")] == order[i + 1:i + 4]
