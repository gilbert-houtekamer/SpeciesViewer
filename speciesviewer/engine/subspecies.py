"""Subspecies count as their species (ch.6 subspecies step 2, 2026-10-18).

ONE JOB: tell which species a name counts as, and add up a group's species
counters so that a species and its subspecies count ONCE — the two caracaras,
the two crows, the two turkey vultures are one species each (Gilbert).

A subspecies is a row BESIDE its species (same parent, the family) whose
SubspeciesOf column names the species; its pictures live in the species'
folder. So the folding happens where the two meet: when a family adds up its
children. Every level above just adds up families, as before.

INVARIANT: on a list without subspecies nothing changes — every group then
has one member and its counters pass through untouched.
"""
from __future__ import annotations

import csv


def species_of(sc_dict, name: str) -> str:
    """The species `name` counts as: its SubspeciesOf, else itself."""
    row = sc_dict.get(name) if sc_dict else None
    return (row.get("SubspeciesOf") or name) if row else name


def fold_species_flags(sc_dict, children) -> tuple[int, int]:
    """(collection, new) species counts for one parent, from its children's
    (name, collection, new) counts. A species with its subspecies counts once:
    in the collection when any of them already is, else new when any is new.
    A child that is no species group of more than one passes through as is."""
    groups: dict[str, list] = {}
    for name, coll, new in children:
        groups.setdefault(species_of(sc_dict, name), []).append((int(coll or 0), int(new or 0)))
    coll_total = new_total = 0
    for members in groups.values():
        if len(members) == 1:
            coll, new = members[0]
        else:
            coll = 1 if any(c > 0 for c, _n in members) else 0
            new = 0 if coll else (1 if any(n > 0 for _c, n in members) else 0)
        coll_total += coll
        new_total += new
    return coll_total, new_total


def species_names(sc_dict, names) -> set[str]:
    """The species a set of names counts as (for comparing lifer baselines)."""
    return {species_of(sc_dict, n) for n in names}


def read_subspecies(speciescollection_csv) -> dict:
    """{name_lower: {'SubspeciesOf': species_lower}} for the subspecies in a
    species list (species.csv does not carry the column); {} when unreadable."""
    try:
        with open(speciescollection_csv, "r", encoding="utf-8", newline="") as fh:
            return {r["ScientificName"].strip().lower():
                        {"SubspeciesOf": r["SubspeciesOf"].strip().lower()}
                    for r in csv.DictReader(fh, delimiter=";") if (r.get("SubspeciesOf") or "").strip()}
    except OSError:
        return {}
