"""Which names count as a species (uncertain IDs, phase 1 — docs/plans/uncertain_ids_plan.md).

ONE JOB: answer "does this name count as a species?", and recognise a GROUP
name — "oplurus sp": surely an Oplurus, but not which one.

A group is an ordinary species-level row in the species list, filed under its
family, with its own folder and species page. It just never adds to a count:
not to trip totals, lifers, the master's species number or the website.
A "? name" placeholder never counts either (it retires in phase 4), nor does a
picture whose identification is only probable or possible (phase 2).

What a species row's iSpeciesCollectionCount means (decided 16 Nov, option A):
"this counts as a species" — 1 on a species row with at least one picture that
counts, 0 otherwise, the sum above. "Has pictures / has a page" is the picture
count, never this one.

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


# ── certainty (phase 2): how sure the identification is ─────────────────────
# Stored as a keyword word beside the name, like sex/age/plumage
# ("furcifer lateralis, probable") and mirrored in the picture's `certainty`
# column. Certain is the default and has no word.
PROBABLE, POSSIBLE = "probable", "possible"
CERTAINTY_WORDS = {
    PROBABLE: {"probable", "probably", "cf", "cf.", "waarschijnlijk"},
    POSSIBLE: {"possible", "possibly", "?", "mogelijk"},
}
# how an uncertain name reads: "carpet chameleon cf." / "carpet chameleon ?"
CERTAINTY_MARK = {PROBABLE: "cf.", POSSIBLE: "?"}
_WORD_TO_CERTAINTY = {w: c for c, words in CERTAINTY_WORDS.items() for w in words}


def certainty_of(subject: str) -> str:
    """"probable", "possible" or "" (certain) for a stored keyword, in either
    stored form ("x, probable" or "['x', 'probable']")."""
    s = str(subject or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    for part in s.split(","):
        c = _WORD_TO_CERTAINTY.get(part.strip().strip("'\"").lower())
        if c:
            return c
    return ""


def counts_as_species(name: str, certainty: str = "") -> bool:
    """THE counting rule: True when a picture with this name adds a species to
    a count. A group, a placeholder or an uncertain picture never does."""
    n = (name or "").strip()
    # Only the two known words make a picture uncertain: a stray value in the
    # column (a positional re-head of an old CSV) must never un-count a species.
    uncertain = (certainty or "").strip().lower() in (PROBABLE, POSSIBLE)
    return bool(n) and not is_placeholder(n) and not is_group(n) and not uncertain


def is_uncertain_id(subject: str = "", name: str = "") -> bool:
    """What the "Uncertain" review views show (phase 3): a picture whose ID is
    still open — a group ("oplurus sp"), a probable or possible ID, or an old
    "? name" placeholder. `subject` is the stored keyword, `name` the resolved
    scientific name (either may be empty)."""
    if certainty_of(subject):
        return True
    s = str(subject or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    parts = [p.strip().strip("'\"") for p in s.split(",")] + [name or ""]
    return any(is_group(p) or is_placeholder(p) for p in parts if p.strip())
