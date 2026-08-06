"""The five picture-label colours (Red / Yellow / Green / Blue / Purple) and the
selectable colour SCHEMES that re-tint them.

The active scheme is a user "Look & feel" setting (``[lookfeel] color_scheme``).
It is read ONCE at import and baked into ``COLOR_HEX`` — many viewers do
``from gui.rating_editor import COLOR_HEX`` (which re-exports this), capturing the
values at their own import time — so a change takes effect on the next restart.
That "change → restart to apply" contract is deliberate for v1; making it live
would mean re-reading the palette in every consumer.

Only the five LABEL hues change per scheme. The olive picture-frame accent and
the neutrals live in gui/theme.py and are NOT part of a scheme.
"""

from utils.config import get_setting

COLORS = ["Red", "Yellow", "Green", "Blue", "Purple"]

# Three schemes (user-simplified). "Vivid" is the app's long-standing saturated
# palette, so it's the default and existing installs look identical until changed.
COLOR_SCHEMES: dict[str, dict[str, str]] = {
    "Vivid":  {"Red": "#FF4C4C", "Yellow": "#FFD700", "Green": "#32CD32",
               "Blue": "#1E90FF", "Purple": "#9370DB"},
    "Nature": {"Red": "#B5544F", "Yellow": "#C2A24A", "Green": "#6D8A5A",
               "Blue": "#5F7FA6", "Purple": "#8A749E"},
    "Pastel": {"Red": "#E29A97", "Yellow": "#E6D18F", "Green": "#A9C69C",
               "Blue": "#9DB8D6", "Purple": "#C1ABD6"},
}
DEFAULT_SCHEME = "Vivid"
# Back-compat: earlier builds wrote these names for the same palettes.
_ALIASES = {"classic": "Vivid", "muted": "Nature"}


def active_scheme_name() -> str:
    """The persisted scheme name (case-insensitive, with legacy aliases), or the default."""
    name = (get_setting("lookfeel", "color_scheme", fallback=DEFAULT_SCHEME)
            or DEFAULT_SCHEME).strip()
    name = _ALIASES.get(name.lower(), name)
    for k in COLOR_SCHEMES:
        if k.lower() == name.lower():
            return k
    return DEFAULT_SCHEME


# Baked at import — this is what the app runs with until the next restart.
COLOR_HEX: dict[str, str] = dict(COLOR_SCHEMES[active_scheme_name()])
