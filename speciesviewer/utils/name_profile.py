"""Species-name language profile — which of NL / EN / Sci is shown as the label(s)
UNDER each picture in the viewers (and, for consistency, in the species picker
that lists the same names).

A profile is an ORDERED LIST of the languages to render, each as its own line
(up to three). The list is de-duplicated — a name is never shown twice, so
picking "Scientific first" on a taxon that has no vernacular yields a single
line, not the same Latin name stacked on itself. The list is also fallback-
filled: if none of the profile's languages have a name, any name at all is used
(sci → nl → en) so a card is never nameless.

The chains are chosen so "English / …" never silently falls back to Dutch — an
American reader lands on English/Scientific — while an African bird with no
Dutch name simply drops the empty line.

One setting, global (Global Settings ▸ Preferred names), used identically by
every viewer. There used to be a per-trip/per-virttrip override on top of it,
set once at trip creation — dropped because it went stale the moment you
changed your global preference later, and half the viewers ignored it anyway
(it never touched the website, so no real per-trip need was actually being
served by keeping it).

Scope: this governs the DISPLAY name only. It does NOT touch the website output,
species-name suggestions, or any editing — purely which language reads out. The
scientific name remains the stable identity everywhere (paths, keys, matching);
this is cosmetics.
"""

from utils.config import get_setting, set_setting

# key -> (menu label, ordered list of language lines to render)
# Order matters: it is the top-to-bottom line order on the card, and the first
# entry is the primary (largest) line / the single name used where only one fits.
NAME_PROFILES = {
    "en_nl":     ("English / Dutch",              ["en", "nl"]),
    "nl_en":     ("Dutch / English",              ["nl", "en"]),
    "nl_en_sci": ("Dutch / English / Scientific", ["nl", "en", "sci"]),
    "en_nl_sci": ("English / Dutch / Scientific", ["en", "nl", "sci"]),
    "en_sci":    ("English / Scientific",         ["en", "sci"]),
    "nl_sci":    ("Dutch / Scientific",           ["nl", "sci"]),
    "sci_en":    ("Scientific / English",         ["sci", "en"]),
    "sci_nl":    ("Scientific / Dutch",           ["sci", "nl"]),
}
DEFAULT_PROFILE = "en_nl"

# Legacy stored keys → their nearest current equivalent. The old plain "sci"
# (Scientific only) is gone; "sci_en" is the sensible successor (it collapses to
# one line anyway when there is no English name).
_ALIASES = {"sci": "sci_en"}

# Each language code maps to the row field names it can appear under: the trip
# pipeline rows use NLSoortNaam/EngSoortNaam/WetSoortNaam; the Master Species
# Viewer's own rows (and the Biota nodes) use nl/en/sci. Try both so one resolver
# serves every viewer.
_FIELDS = {
    "nl":  ("NLSoortNaam", "nl"),
    "en":  ("EngSoortNaam", "en"),
    "sci": ("WetSoortNaam", "sci"),
}


def _norm(key: str) -> str:
    """Map a stored/legacy key onto a live profile key (or the default)."""
    k = (key or "").strip()
    k = _ALIASES.get(k, k)
    return k if k in NAME_PROFILES else DEFAULT_PROFILE


def profile_label(key: str) -> str:
    return NAME_PROFILES[_norm(key)][0]


def profile_short_label(key: str) -> str:
    """Compact label for a toolbar combo, e.g. 'EN/NL'."""
    chain = _chain(key)
    return "/".join({"nl": "NL", "en": "EN", "sci": "Sci"}[c] for c in chain)


def profile_keys() -> list:
    """Profile keys in menu order — sorted A-Z on the menu label, so every
    dropdown that offers them (Global Settings, Species Settings, Create Trip /
    Album, the viewers' own combo) lists them the same, findable way. The
    NAME_PROFILES definition order is free to stay grouped by language."""
    return sorted(NAME_PROFILES, key=lambda k: NAME_PROFILES[k][0].casefold())


def _chain(key: str) -> list:
    return NAME_PROFILES[_norm(key)][1]


def global_profile() -> str:
    """The machine-wide default (Global Settings ▸ Preferred names)."""
    return _norm(get_setting("Global", "names_profile", fallback=DEFAULT_PROFILE))


def set_global_profile(key: str) -> None:
    if key in NAME_PROFILES:
        set_setting("Global", "names_profile", key)


def effective_profile() -> str:
    """The profile every viewer should use — currently just the global
    default. Kept as its own name (rather than every caller reading
    global_profile() directly) in case a real per-scope need shows up again."""
    return global_profile()


def _first(row: dict, lang: str) -> str:
    for f in _FIELDS[lang]:
        v = (row.get(f) or "").strip()
        if v:
            return v
    return ""


def name_lines(row: dict, profile: str) -> list:
    """Ordered, de-duplicated display names for the profile, as (text, is_sci)
    tuples — one per language in the chain that has a name, skipping any that
    repeat a name already shown. `is_sci` marks the line that is the scientific
    name (so a caller can italicise exactly that one, wherever it lands).

    Fallback-filled (sci → nl → en) so the list is never empty."""
    sci_cf = _first(row, "sci").casefold()
    out: list = []
    seen: set = set()

    def add(v: str) -> None:
        v = (v or "").strip()
        k = v.casefold()
        if v and k not in seen:
            seen.add(k)
            out.append((v, k == sci_cf))

    for lang in _chain(profile):
        add(_first(row, lang))
    if not out:
        for lang in ("sci", "nl", "en"):
            add(_first(row, lang))
    return out


def all_names_ordered(row: dict, profile: str) -> list:
    """EVERY distinct name (NL/EN/Sci) the row has, as (text, is_sci) tuples,
    ordered with the profile's languages first (in the profile's order) and any
    remaining language appended. Used by the *detail* surfaces (large image
    viewer, species page) which show all three at once — the profile only sets
    which name leads. Cards use `name_lines` instead (profile languages only)."""
    sci_cf = _first(row, "sci").casefold()
    out: list = []
    seen: set = set()

    def add(v: str) -> None:
        v = (v or "").strip()
        k = v.casefold()
        if v and k not in seen:
            seen.add(k)
            out.append((v, k == sci_cf))

    for lang in _chain(profile):
        add(_first(row, lang))
    for lang in ("nl", "en", "sci"):   # then whatever the profile left out
        add(_first(row, lang))
    return out


def name_for(row: dict, profile: str) -> str:
    """Backwards-compatible shim: the single primary display name (the first line
    of `name_lines`). Callers that only need one string keep using this."""
    lines = name_lines(row, profile)
    return lines[0][0] if lines else ""
