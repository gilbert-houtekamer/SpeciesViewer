"""Single policy gate: what SpeciesViewer writes to a picture's RAW-adjacent
sidecars — the DxO PhotoLab `.dop` and the Lightroom `.xmp`.

Validated behaviour (2026-07-05, live in PL 9.x):
  - Colour label / rating written to the `.xmp` → PhotoLab ignores it, loses it on
    round-trip, and its filter can hide the image. BAD channel for PL.
  - Colour label / rating written to the `.dop` (PhotoLab's own store, Item-level
    `ColorLabel` / `Rating`) → PhotoLab reads it cleanly on "import sidecars"
    (Ctrl-A → read). GOOD channel for PL.
  - Crop → `.dop` is PhotoLab's native crop; crop → `.xmp` is for Lightroom.
  - Lightroom ignores `.dop` entirely.

So the user-facing controls are a 2×2 — {colour&rating, crop} × {.dop, .xmp}.
Keywords/description are NOT gated here: the species pipeline and website need
them, and they never caused the PL trouble, so they always go to .xmp + JPG.

Stored in [sidecars] of the per-machine config, read LIVE at each write site.
Defaults encode the "prepare in SpeciesViewer, Ctrl-A import sidecars in
PhotoLab" workflow: colour/rating/crop → .dop on, → .xmp off.
"""

from utils.config import _config, persist_config

_SECTION = "sidecars"

# Defaults target the PhotoLab workflow: everything to the .dop (PL's native
# store, read on "import sidecars"), nothing to the .xmp.
#   - Colour/rating → .dop : ColorLabel/Rating via write_dop_metadata (safe
#                            surgical single-field replace).
#   - Crop → .dop          : CropActive/CropRect merged into Overrides via the
#                            balanced-brace write_dop_edits (was corrupting a
#                            non-empty Overrides block; fixed 2026-07-05).
#   - .xmp channels stay off: PL mistreats colour/rating there, and its crop
#                            interpretation ignores/loses ours. Turn on only for
#                            a Lightroom workflow.
_DEFAULTS = {
    "colorrating_dop": True,   # colour label + rating → DxO .dop (PhotoLab reads on import)
    "colorrating_xmp": False,  # colour label + rating → Lightroom .xmp (PL mistreats these)
    "crop_dop":        True,   # crop → DxO .dop (merged into Overrides, non-destructive)
    "crop_xmp":        False,  # crop → Lightroom .xmp (Lightroom workflow only)
}


def _flag(key: str) -> bool:
    return _config.getboolean(_SECTION, key, fallback=_DEFAULTS[key])


def color_rating_to_dop_enabled() -> bool:
    """Write colour label + rating into the DxO .dop (PhotoLab's native store)."""
    return _flag("colorrating_dop")


def color_rating_to_xmp_enabled() -> bool:
    """Write colour label + rating into the Lightroom .xmp. Default off: PhotoLab
    watches the .xmp and mistreats these fields (ignores / clobbers / filter-hides)."""
    return _flag("colorrating_xmp")


def crop_to_dop_enabled() -> bool:
    """Write a suggested crop to the DxO PhotoLab .dop."""
    return _flag("crop_dop")


def crop_to_xmp_enabled() -> bool:
    """Write a suggested crop to the Lightroom .xmp (for Lightroom users)."""
    return _flag("crop_xmp")


def load_sidecar_config() -> dict:
    return {k: _flag(k) for k in _DEFAULTS}


def save_sidecar_config(cfg: dict):
    if not _config.has_section(_SECTION):
        _config.add_section(_SECTION)
    for k in _DEFAULTS:
        if k in cfg:
            _config.set(_SECTION, k, "yes" if cfg[k] else "no")
    persist_config()
