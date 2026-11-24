"""BioCLIP's names in YOUR words — a species of your own species list.

ONE JOB: turn the AI's scientific name (BioCLIP 2's taxonomy, sometimes an older
genus) into the species of your species list (speciescollection.csv of the
master): its Dutch / English name and scientific name, and how sure the match is:
  "exact"  — the same scientific name;
  "renamed" — the same species epithet under another genus of the SAME family
              that is in your list (Musophaga violacea → Tauraco violaceus): very
              likely the same bird;
  ""       — not in your list (shown with the AI's own English name).

INVARIANT: read-only; the list is read once per master and run of the program.
"""
from __future__ import annotations

import threading
from pathlib import Path

_lock = threading.Lock()
_ENDINGS = ("us", "um", "is", "a", "e")


def _stem(epithet: str) -> str:
    """The epithet without its Latin gender ending: violaceus / violacea /
    violaceum → violace (the ending follows the genus, which may have changed)."""
    for end in _ENDINGS:
        if epithet.endswith(end) and len(epithet) - len(end) >= 4:
            return epithet[: -len(end)]
    return epithet
_lists: dict[str, tuple[dict, dict, dict]] = {}


def _species_list(master_root) -> tuple[dict, dict, dict]:
    """({sci: row}, {epithet: [sci]}, {sci: family}) of the species-level names."""
    key = str(master_root or "")
    with _lock:
        if key not in _lists:
            from db.master_mutate import _read_collection
            allrows = _read_collection(Path(master_root)) if master_root else {}
            rows = {s: r for s, r in allrows.items()
                    if (r.get("Level") or "") == "Level.SPECIES"}
            by_epithet: dict[str, list[str]] = {}
            family: dict[str, str] = {}
            for s, r in rows.items():
                parts = s.split()
                if len(parts) == 2:
                    by_epithet.setdefault(_stem(parts[1]), []).append(s)
                # In the list a species usually hangs under its FAMILY; when there
                # is a genus level in between, its parent is the family.
                up = (r.get("Up") or "").strip().lower()
                above = allrows.get(up, {})
                if (above.get("Level") or "") == "Level.GENUS":
                    up = (above.get("Up") or "").strip().lower()
                family[s] = up
            _lists[key] = (rows, by_epithet, family)
        return _lists[key]


def match(sci: str, master_root, family: str = "") -> tuple[str, dict | None]:
    """(how, your row) for the AI's scientific name; ("", None) when not in
    your list. `family` = the AI's family for that name (needed for "renamed")."""
    rows, by_epithet, fam = _species_list(master_root)
    s = (sci or "").strip().lower()
    if s in rows:
        return "exact", rows[s]
    parts = s.split()
    family = (family or "").strip().lower()
    if len(parts) == 2 and family:
        same = [o for o in by_epithet.get(_stem(parts[1]), [])
                if o.split()[0] != parts[0] and fam.get(o) == family]
        exact_ep = [o for o in same if o.split()[1] == parts[1]]
        if len(exact_ep) == 1:           # the same ending too: that one
            return "renamed", rows[exact_ep[0]]
        if len(same) == 1:
            return "renamed", rows[same[0]]
    return "", None


def display(sci: str, common: str, master_root, profile: str | None = None,
            family: str = "") -> dict:
    """{"name", "sci", "how"} to show: your own name when the species is in your
    list (in your Preferred names, Global Settings), else the AI's English name."""
    how, row = match(sci, master_root, family)
    if row is None:
        return {"name": common or "", "sci": sci, "how": ""}
    from utils.name_profile import effective_profile, name_lines
    sci_yours = (row.get("ScientificName") or sci).strip()
    sci_yours = sci_yours[:1].upper() + sci_yours[1:]
    lines = name_lines({"nl": row.get("DutchName") or "", "en": row.get("EnglishName") or "",
                        "sci": sci_yours}, profile or effective_profile())
    name = next((t for t, is_sci in lines if not is_sci), "") or common or ""
    return {"name": name, "sci": sci_yours, "how": how}
