"""Single policy gate: what SpeciesViewer writes to a picture's RAW-adjacent
sidecars — the DxO PhotoLab `.dop` and the Lightroom `.xmp`.

DECISION 2026-07-20 (user): WRITE BOTH, ALWAYS. Colour label, rating and crop go
to the `.xmp` unconditionally, and to the `.dop` as well whenever PhotoLab has
already authored one. The four switches that used to gate this are retired — the
functions below return True and the stored [sidecars] keys are ignored.

Why the earlier 2x2 was wrong. It assumed you must pick a channel per tool. But
PhotoLab ignores the `.xmp` when "Synchronize metadata with XMP sidecar files" is
off, Lightroom ignores the `.dop` always, and NEITHER reads the other's file in
any setting. So writing both cannot cause a conflict, and it is the only way the
two stay in step: whichever program you open next sees the same colour, rating
and crop. Writing one channel only meant an edit made in SpeciesViewer was
invisible to half the toolchain, and the setting that chose which half was easy
to get wrong — a single afternoon with the `.xmp` channel off put an entire day's
colour work somewhere PhotoLab could not read.

The hard rule that remains: we NEVER create a `.dop`. PhotoLab owns the master
UUID, so a recipe it did not author is taken as a virtual copy. `.dop` writes are
edits to an existing recipe; when there is none, the `.xmp` carries the edit
alone until PhotoLab writes sidecars (select all -> write sidecars). See
engine/sidecarlib.ensure_dop.

Keywords/description are not gated here either: the species pipeline and website
need them, and they always go to .xmp + JPG.
"""

from utils.config import _config, persist_config

_SECTION = "sidecars"

# Retained so an existing machine config still loads and the dialog can show what
# is written. No longer consulted when deciding to write — see the module docstring.
_DEFAULTS = {
    "colorrating_dop": True,
    "colorrating_xmp": True,
    "crop_dop":        True,
    "crop_xmp":        True,
}


def _flag(key: str) -> bool:
    return _config.getboolean(_SECTION, key, fallback=_DEFAULTS[key])


# ── the gate: both channels, always ──────────────────────────────────────────
# Kept as four functions rather than deleted, so the nine call sites keep reading
# their intent at the point of the write ("am I allowed to write the .dop here?")
# instead of every site growing its own copy of the policy.

def color_rating_to_dop_enabled() -> bool:
    """Colour + rating -> the .dop, when PhotoLab has authored one (never created)."""
    return True


def color_rating_to_xmp_enabled() -> bool:
    """Colour + rating -> the .xmp. Always: it is the channel that always exists."""
    return True


def crop_to_dop_enabled() -> bool:
    """Crop -> the .dop, when one exists."""
    return True


def crop_to_xmp_enabled() -> bool:
    """Crop -> the .xmp, so Lightroom sees the same crop PhotoLab does."""
    return True


def load_sidecar_config() -> dict:
    return {k: _flag(k) for k in _DEFAULTS}


def save_sidecar_config(cfg: dict):
    if not _config.has_section(_SECTION):
        _config.add_section(_SECTION)
    for k in _DEFAULTS:
        if k in cfg:
            _config.set(_SECTION, k, "yes" if cfg[k] else "no")
    persist_config()
