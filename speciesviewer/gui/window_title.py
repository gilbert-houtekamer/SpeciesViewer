"""One window-title scheme for the windows that stay open side by side.

ONE job: build a title as  <Kind> — <Where> · <What>  (n of m)

- Kind:  the window's name, always first (Image Viewer, Editor, ...), so the
  windows of one kind sort together on the taskbar.
- Where: what the window shows RIGHT NOW — its scope, named the way you say it:
  a day "2026-09-15", a trip "Brazil 2026", "Brazil 2026 + master", "master".
  A window re-titles itself when its scope changes.
- What:  the current filter or selection (a species, "12 missing GPS"), or
  nothing.
- pos:   (index, count) → "(3 of 22)", kept even when filtered to one species.

See docs/plans/window_titles.md.
"""


def window_title(kind: str, where: str = "", what=(), pos=None) -> str:
    """Build a title. `what` may be one string or a sequence of parts; empty
    parts are dropped. `pos` is (zero-based index, count) or None."""
    if isinstance(what, str):
        what = (what,)
    parts = [p for p in (where, *what) if p]
    title = kind
    if parts:
        title += " — " + " · ".join(parts)
    if pos is not None:
        index, count = pos
        # The position belongs to the FIRST part (the picture on screen), so
        # it follows it: "Editor — Rufous hornero (3 of 22) · 2026-09-15".
        counter = f" ({index + 1} of {count})"
        if parts:
            head, *rest = parts
            title = kind + " — " + head + counter + "".join(f" · {p}" for p in rest)
        else:
            title += counter
    return title
