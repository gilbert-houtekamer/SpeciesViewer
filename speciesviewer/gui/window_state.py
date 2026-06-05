"""Per-dialog window geometry + column persistence.

Stores position/size (and an optional column count) per dialog in the
per-machine sv_config_<machine>.ini — geometry is monitor-specific, so the
machine config is the right home. The clamp keeps a remembered geometry from
landing off-screen (e.g. a laptop undocked from a second monitor).
"""

from PyQt6.QtGui import QGuiApplication

from utils.config import save_window_state, get_window_state


def restore_window(dialog, key, default_size=None):
    """Apply the saved geometry for `key` (falling back to default_size), clamp
    it onto a visible screen, and return the saved state dict so the caller can
    use state["cols"] before building its grid."""
    st = get_window_state(key)
    if st["w"] and st["h"]:
        x = st["x"] if st["x"] is not None else dialog.x()
        y = st["y"] if st["y"] is not None else dialog.y()
        dialog.setGeometry(x, y, st["w"], st["h"])
    elif default_size:
        dialog.resize(*default_size)
    clamp_to_visible_screen(dialog)
    return st


def save_window(dialog, key, cols=None, vsplit=None, hsplit=None):
    g = dialog.geometry()
    save_window_state(key, x=g.x(), y=g.y(), w=g.width(), h=g.height(),
                      cols=cols, vsplit=vsplit, hsplit=hsplit)


def clamp_to_visible_screen(dialog):
    """Move/resize the window so it sits FULLY inside one screen's available
    area. A partly off-screen window (e.g. a negative top-left straddling two
    high-DPI monitors) is a known Qt/Windows case where the backing store never
    paints — the window comes up blank/white. Intersecting a screen isn't
    enough; the whole frame must fit, or we reposition it."""
    frame = dialog.frameGeometry()
    center = frame.center()
    # Prefer the screen containing the window's center; else any it intersects;
    # else the primary screen.
    target = next((s for s in QGuiApplication.screens()
                   if s.availableGeometry().contains(center)), None)
    if target is None:
        target = next((s for s in QGuiApplication.screens()
                       if s.availableGeometry().intersects(frame)), None)
    avail = (target or QGuiApplication.primaryScreen()).availableGeometry()

    # setGeometry positions the CLIENT area; the title bar sits above it. Reserve
    # room for the title bar/border so clamping to the top edge doesn't push the
    # title bar off-screen (frame margins are 0 before the window is shown, so
    # fall back to a sensible default).
    g = dialog.geometry()
    frame = dialog.frameGeometry()
    top_margin = max(g.y() - frame.y(), 36)
    left_margin = max(g.x() - frame.x(), 0)
    w = min(g.width(), avail.width() - left_margin)
    h = min(g.height(), avail.height() - top_margin)
    x = max(avail.left() + left_margin, min(g.x(), avail.right() - w + 1))
    y = max(avail.top() + top_margin, min(g.y(), avail.bottom() - h + 1))
    if (x, y, w, h) != (g.x(), g.y(), g.width(), g.height()):
        dialog.setGeometry(x, y, w, h)
