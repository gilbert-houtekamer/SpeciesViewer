"""Per-dialog window geometry + column persistence.

Stores position/size (and an optional column count) per dialog in the
per-machine sv_config_<machine>.ini — geometry is monitor-specific, so the
machine config is the right home. The clamp keeps a remembered geometry from
landing off-screen (e.g. a laptop undocked from a second monitor).
"""

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

from utils.config import save_window_state, get_window_state


def give_independent_taskbar_entry(widget):
    """Force a real Windows taskbar entry for a top-level SV window that is
    still a Qt child of whatever opened it (the grid, the main window, ...).

    Windows hides ANY window from the taskbar if it has a native "owner"
    HWND, regardless of Qt's WindowType — confirmed live: a window whose Qt
    type was explicitly set to Window (not Dialog) still came up with
    APPWINDOW=NO and a non-zero owner HWND once it had a Qt parent. The
    other way to fix this — construct these windows with no Qt parent at
    all — reopens the parent/child lifecycle trouble this app has been bitten
    by before (dangling windows, lost auto-close-with-opener), so instead
    this leaves the Qt object graph completely untouched and patches the
    native window style directly after the fact. Windows-only; a no-op
    everywhere else, and fails silently if anything here goes wrong — the
    worst case is today's behaviour (no separate taskbar entry), never a
    crash or a leaked/dangling window.

    Call once from the widget's showEvent (after super().showEvent(event)),
    deferred via QTimer.singleShot(0, ...) so it runs after the window is
    actually on screen, not synchronously inside the event handler.
    """
    if sys.platform != "win32":
        return
    # Never on a modal window. The hide() below ends a running exec() loop:
    # exec() returned at once while the dialog stayed up, and once it closed
    # Qt still counted it as the active modal window — the dialog that opened
    # it no longer took clicks and SV later died silently (Read SD-card opened
    # from Restore Work, 2026-10-20). A modal dialog belongs with its opener anyway.
    if widget.windowModality() != Qt.WindowModality.NonModal:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(widget.winId())
        GWL_EXSTYLE = -20
        WS_EX_APPWINDOW = 0x00040000
        exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if exstyle & WS_EX_APPWINDOW:
            return  # already set — e.g. a re-entrant show after our own hide/show below
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle | WS_EX_APPWINDOW)
        # The taskbar only re-evaluates grouping on a hide/show cycle after
        # the style changes — a bare SetWindowPos/SWP_FRAMECHANGED does not
        # trigger it in testing.
        widget.hide()
        widget.show()
    except Exception:
        pass


def restore_window(dialog, key, default_size=None):
    """Apply the saved geometry for `key` (falling back to default_size), clamp
    it onto a visible screen, and return the saved state dict so the caller can
    use state["cols"] before building its grid."""
    st = get_window_state(key)
    # A saved geometry small in BOTH dimensions vs the caller's intended default
    # is almost always a stray "make it a little floating window" that got
    # persisted — restoring it strands the user with no obvious way back. Fall
    # back to the default. A half-screen SNAP keeps full height, so h stays
    # large and the snap is respected; a deliberately small dialog has a small
    # default, so the ratio still passes.
    saved_ok = bool(st["w"] and st["h"])
    if saved_ok and default_size:
        dw, dh = default_size
        if st["w"] < 0.55 * dw and st["h"] < 0.62 * dh:
            saved_ok = False
    if saved_ok:
        x = st["x"] if st["x"] is not None else dialog.x()
        y = st["y"] if st["y"] is not None else dialog.y()
        dialog.setGeometry(x, y, st["w"], st["h"])
    elif default_size:
        dialog.resize(*default_size)
    clamp_to_visible_screen(dialog)
    return st


def save_window(dialog, key, cols=None, vsplit=None, hsplit=None, fsplit=None):
    g = dialog.geometry()
    save_window_state(key, x=g.x(), y=g.y(), w=g.width(), h=g.height(),
                      cols=cols, vsplit=vsplit, hsplit=hsplit, fsplit=fsplit)


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
