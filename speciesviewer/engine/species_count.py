"""Which names count as a species (uncertain IDs, phase 1 — docs/plans/uncertain_ids_plan.md).

ONE JOB: answer "does this name count as a species?", and recognise a GROUP
name — "oplurus sp": surely an Oplurus, but not which one.

A group is an ordinary species-level row in the species list, filed under its
family, with its own folder and species page. It just never adds to a count:
not to trip totals, lifers, the master's species number or the website.
A "? name" placeholder never counts either (it retires in phase 4).

The stored name has NO trailing dot ("oplurus sp", not "oplurus sp."): the
name is also a folder name, and Windows silently drops a trailing dot from a
folder while macOS and the web server keep it. The Dutch and English names
carry the dot ("oplurus sp."), so that is what you read and can type.

INVARIANT: every place that counts species asks counts_as_species(); none
decides for itself. Most counters reach it through engine/subspecies.py
(fold_species_flags, species_names), the same funnel subspecies use.
"""
from __future__ import annotations

# what may follow the genus or family: "oplurus sp.", "oplurus spec.", "oplurus spp"
GROUP_WORDS = ("sp", "sp.", "spec", "spec.", "spp", "spp.")
GROUP_SUFFIX = "sp"          # the stored form: "<genus or family> sp"


def group_base(name: str) -> str:
    """The genus or family of a group name, lower-cased; "" for anything else."""
    parts = (name or "").strip().lower().split()
    if len(parts) == 2 and parts[1] in GROUP_WORDS and parts[0].isalpha():
        return parts[0]
    return ""


def is_group(name: str) -> bool:
    return bool(group_base(name))


def group_name(base: str) -> str:
    """The stored name of the group of `base`: "oplurus" -> "oplurus sp"."""
    return f"{base.strip().lower()} {GROUP_SUFFIX}"


def group_label(base: str) -> str:
    """How a group reads (its Dutch and English name): "oplurus sp."."""
    return f"{base.strip().lower()} {GROUP_SUFFIX}."


def is_placeholder(name: str) -> bool:
    """A "? name" placeholder (the old way to say "not sure")."""
    return (name or "").strip().startswith("?")


def counts_as_species(name: str) -> bool:
    """THE counting rule: True when a picture with this name adds a species to
    a count. A group or a placeholder never does."""
    n = (name or "").strip()
    return bool(n) and not is_placeholder(n) and not is_group(n)
