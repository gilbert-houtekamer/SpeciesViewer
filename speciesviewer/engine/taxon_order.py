# taxon_order.py: the order in which taxa are shown (ch.6).
#
# ONE JOB: put sibling taxa in display order. A converted species list
# (speciescollection.csv) carries a Sequence column — the sort number among
# siblings (birds from AviList, mammals from MDD, the rest from a hand table or
# the author's book order). A list without it (an unconverted master, where the
# order lives in the "040 passeriformes" prefix of the names) sorts exactly as
# before: by the fallback key the caller passes.
#
# INVARIANT: a taxon with a Sequence comes before a sibling without one, and
# siblings without one keep the old order among themselves.

import csv
import os

_cache = {}     # path -> (mtime, {name: float})


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sort_key(seq, fallback=""):
    """Key for one taxon: its Sequence (a float or None) and the old order."""
    return (0, seq, fallback) if seq is not None else (1, 0.0, fallback)


def sequence_of(entry):
    """The Sequence of one species-list row (a dict), or None."""
    return _number(entry.get("Sequence")) if entry else None


def ordered(names, rows, fallback=lambda n: n):
    """`names` in display order. `rows` maps a name to its species-list row (the
    in-memory sc_dict); `fallback(name)` is the old sort key."""
    return sorted(names, key=lambda n: sort_key(sequence_of(rows.get(n)), fallback(n)))


def load_sequence(collection_csv):
    """{lowercased name: Sequence} from a speciescollection.csv, cached until the
    file changes. Empty for a list without the column."""
    try:
        mtime = os.path.getmtime(collection_csv)
    except OSError:
        return {}
    hit = _cache.get(collection_csv)
    if hit and hit[0] == mtime:
        return hit[1]
    seq = {}
    with open(collection_csv, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        if "Sequence" in (reader.fieldnames or []):
            for row in reader:
                n = _number(row.get("Sequence"))
                if n is not None:
                    seq[(row.get("ScientificName") or "").strip().lower()] = n
    _cache[collection_csv] = (mtime, seq)
    return seq
