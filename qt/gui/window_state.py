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


def save_window(dialog, key, cols=None):
    g = dialog.geometry()
    save_window_state(key, x=g.x(), y=g.y(), w=g.width(), h=g.height(), cols=cols)


def clamp_to_visible_screen(dialog):
    frame = dialog.frameGeometry()
    for screen in QGuiApplication.screens():
        if screen.availableGeometry().intersects(frame):
            return  # at least partially on some screen — fine
    avail = (dialog.screen() or QGuiApplication.primaryScreen()).availableGeometry()
    dialog.move(avail.center().x() - dialog.width() // 2,
                avail.center().y() - dialog.height() // 2)
