"""Species-name language profile — which of NL / EN / Sci is shown as the label
UNDER each picture in the viewers (and, for consistency, in the species picker
that lists the same names).

A profile is an ORDERED fallback chain: the first name that exists wins. The
chains are chosen so "English / Scientific" NEVER falls back to Dutch — an
American never sees a Dutch name — and an African bird with no Dutch name simply
lands on English. Scientific is always the last link (every species has one).

Precedence: a per-trip / per-virttrip override wins over the global default (set
in Global Settings). A viewer's own combo is a per-session override on top.

Scope: this governs the DISPLAY name only. It does NOT touch the website output,
species-name suggestions, or any editing — purely which language reads out.
"""

from utils.config import get_setting, set_setting

# key -> (menu label, ordered chain of language codes)
NAME_PROFILES = {
    "nl_en_sci": ("Dutch / English / Scientific", ["nl", "en", "sci"]),
    "en_sci":    ("English / Scientific",         ["en", "sci"]),
    "nl_sci":    ("Dutch / Scientific",           ["nl", "sci"]),
    "sci":       ("Scientific only",              ["sci"]),
}
DEFAULT_PROFILE = "nl_en_sci"

# Each language code maps to the row field names it can appear under: the trip
# pipeline rows use NLSoortNaam/EngSoortNaam/WetSoortNaam; the Master Species
# Viewer's own rows use nl/en/sci. Try both so one resolver serves every viewer.
_FIELDS = {
    "nl":  ("NLSoortNaam", "nl"),
    "en":  ("EngSoortNaam", "en"),
    "sci": ("WetSoortNaam", "sci"),
}


def profile_label(key: str) -> str:
    return NAME_PROFILES.get(key, NAME_PROFILES[DEFAULT_PROFILE])[0]


def profile_short_label(key: str) -> str:
    """Compact label for a toolbar combo, e.g. 'NL/EN/Sci'."""
    chain = _chain(key)
    return "/".join({"nl": "NL", "en": "EN", "sci": "Sci"}[c] for c in chain)


def profile_keys() -> list:
    """Profile keys in menu order."""
    return list(NAME_PROFILES.keys())


def _chain(key: str) -> list:
    return NAME_PROFILES.get(key, NAME_PROFILES[DEFAULT_PROFILE])[1]


def global_profile() -> str:
    """The machine-wide default (Global Settings ▸ Preferred names)."""
    key = (get_setting("Global", "names_profile", fallback=DEFAULT_PROFILE) or "").strip()
    return key if key in NAME_PROFILES else DEFAULT_PROFILE


def set_global_profile(key: str) -> None:
    if key in NAME_PROFILES:
        set_setting("Global", "names_profile", key)


def effective_profile(override: str | None = None) -> str:
    """Resolve the profile to use: an explicit override (a trip/virttrip setting
    or a per-view combo choice) wins; an empty / 'default' / unknown override
    falls through to the global default."""
    o = (override or "").strip()
    if o and o != "default" and o in NAME_PROFILES:
        return o
    return global_profile()


def _first(row: dict, lang: str) -> str:
    for f in _FIELDS[lang]:
        v = (row.get(f) or "").strip()
        if v:
            return v
    return ""


def name_for(row: dict, profile: str) -> str:
    """The first non-empty name in the profile's fallback chain. Falls back to any
    name at all (Sci → NL → EN) if the chain is exhausted, so a card never goes
    nameless."""
    for lang in _chain(profile):
        v = _first(row, lang)
        if v:
            return v
    for lang in ("sci", "nl", "en"):
        v = _first(row, lang)
        if v:
            return v
    return ""
