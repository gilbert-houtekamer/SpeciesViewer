import logging
import random
import sys
import threading
import time
from pathlib import Path

# Ensure both the project root and qt/ are on sys.path regardless of CWD
_qt_dir = Path(__file__).resolve().parent
_project_root = _qt_dir.parent
for _p in [str(_qt_dir), str(_project_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtWidgets import QApplication, QSplashScreen, QToolTip
from PyQt6.QtGui import QPixmap, QFont, QIcon, QPainter, QLinearGradient, QColor
from PyQt6.QtCore import Qt, QTimer, QSize

from utils.config import _config
from gui.theme import build_base_qss


def _warm_master_db_in_background(done_event: threading.Event) -> None:
    """Run MasterDB.ensure_fresh() on a daemon thread during splash so the
    cold-rebuild cost (~6s when the master CSVs have changed) is paid while
    the user is already waiting, instead of when they later open the Rating
    Editor's Trip+Master scope.

    The thread opens its own sqlite3 connection (sqlite3 connections are
    thread-affine) and closes it before returning; the Rating Editor's later
    open is then a warm <10ms path. Failures (missing master, permission
    errors, etc.) log a warning and otherwise pretend nothing happened — the
    lazy path in `_attach_master` still falls back cleanly.

    Concurrency with the lazy open is handled by MasterDB._build_lock + the
    double-checked is_stale() inside ensure_fresh().

    `done_event` is set in every exit path (success, skip or failure) so the
    splash can wait for the real warm-up instead of a guessed flat delay.
    """
    def _run():
        # Diagnostic prints go straight to stderr so they survive
        # init_logger()'s handler.clear() + mode="w" reset once the main
        # window starts; the post-init logger.info also writes to speciesviewer.log.
        def diag(msg):
            if _config.get("Global", "debug_tools", fallback="no") == "yes":
                print(f"[splash-warm] {msg}", file=sys.stderr, flush=True)
        logger = logging.getLogger("speciesviewer")
        try:
            master_root = _config.get(
                "master", "masterbiotadatabase", fallback="").strip()
            if not master_root:
                diag("master root not configured — skipping warm")
                return
            if not Path(master_root).exists():
                diag(f"master root missing: {master_root!r} — skipping warm")
                return
            diag(f"warming master DB from {master_root}")
            from db.master_db import MasterDB
            db = MasterDB(master_root)
            rebuilt = db.ensure_fresh()
            # Crash recovery: flush any master writes that were queued but never
            # checkpointed to the canonical CSVs before a prior crash/quit. Runs
            # off the GUI thread while the splash is up.
            recovered = db.recover_if_needed()
            db.close()
            diag(f"warm done (rebuilt={rebuilt}, recovered={recovered})")
            logger.info(
                f"Master DB warm complete (rebuilt={rebuilt}, recovered={recovered}).")
        except Exception as e:
            diag(f"warm FAILED: {e!r}")
            logger.warning(f"Splash-time master DB warm failed: {e}")
        finally:
            done_event.set()

    threading.Thread(target=_run, name="MasterDBWarm", daemon=True).start()

# Strong, module-level reference to the main window for the app's lifetime.
# Without it the window is created as a local inside launch_main_window and kept
# alive only by Qt (C++). Python's cyclic GC — fired by big allocations such as
# the Rating Editor's master CSV read (~260k dicts) — then reclaims the whole
# unrooted Python widget graph mid-operation, deleting live widgets and crashing
# with "wrapped C/C++ object ... has been deleted".
_main_window = None


SPLASH_SIZE = QSize(1200, 675)
SPLASH_FADE = 150      # 0 = no fade; up to 255 = black under the text


def _splash_scale() -> float:
    """1.0, or less when SPLASH_SIZE would not fit in 85% of the screen's free
    area: at 2560x1440 and 200% the screen is 720 high and the taskbar covered
    the subtitle at the bottom (2026-10-21)."""
    try:
        free = QApplication.primaryScreen().availableGeometry()
        return min(1.0, 0.85 * free.width() / SPLASH_SIZE.width(),
                   0.85 * free.height() / SPLASH_SIZE.height())
    except AttributeError:          # no screen yet (tests)
        return 1.0


def _splash_pixmap(path: Path | None = None, fade: int = SPLASH_FADE,
                   size: QSize = SPLASH_SIZE) -> QPixmap:
    """A random picture from assets/splash/ (any .jpg dropped there counts),
    cut to SPLASH_SIZE, darkened towards the bottom so the white text reads
    on light pictures too."""
    if path is None:
        pictures = sorted((Path(__file__).parent / "assets" / "splash").glob("*.jpg"))
        path = random.choice(pictures) if pictures else None
    pix = QPixmap(str(path)) if path else QPixmap()
    if pix.isNull():                     # no pictures: a plain dark splash
        pix = QPixmap(size)
        pix.fill(QColor(40, 44, 40))
    pix = pix.scaled(
        size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation)
    x = (pix.width() - size.width()) // 2
    y = (pix.height() - size.height()) // 2
    pix = pix.copy(x, y, size.width(), size.height())
    if fade:
        h = pix.height()
        grad = QLinearGradient(0, h * 0.55, 0, h)
        grad.setColorAt(0, QColor(0, 0, 0, 0))
        grad.setColorAt(1, QColor(0, 0, 0, fade))
        painter = QPainter(pix)
        painter.fillRect(0, int(h * 0.55), pix.width(), h, grad)
        painter.end()
    return pix


def start_main():
    # 🖼️ Splash screen setup
    # Smaller on a small screen — picture and text alike, so the subtitle
    # still fits on one line above the taskbar.
    k = _splash_scale()
    splash = QSplashScreen(_splash_pixmap(
        size=QSize(int(SPLASH_SIZE.width() * k), int(SPLASH_SIZE.height() * k))))
    splash.setWindowFlag(Qt.WindowType.FramelessWindowHint)

    font_title = QFont("Arial", max(10, round(20 * k)), QFont.Weight.Bold)
    splash.setFont(font_title)

    # Use HTML to style each line separately
    splash_text = (
        '<div style="text-align:center;">'
        f'<span style="font-size:{max(12, round(24 * k))}pt; font-weight:bold;color:white;">Welcome to SpeciesViewer</span><br>'
        f'<span style="font-size:{max(9, round(16 * k))}pt;color:white;">Organize species. Explore collections. Bring biodiversity to life.</span>'
        '</div>'
    )

    splash.showMessage(
        splash_text,
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
    )
    splash.show()

    # Warm the master SQLite cache in the background while the splash is up.
    # If the master CSVs changed since last launch this avoids paying the ~6s
    # rebuild later, when the user opens Rating Editor → Trip+Master.
    _warm_done = threading.Event()
    _warm_master_db_in_background(_warm_done)

    # 🪄 Launch main app window after splash
    def launch_main_window():
        from gui.mainwindow import MainWindow
        from gui.window_state import clamp_to_visible_screen
        from utils.logger_support import init_logger
        from gui.trip_overview import populate_trip_overview
        global _main_window
        _main_window = MainWindow()
        from utils import single_instance
        single_instance.set_window(_main_window)
        main_window = _main_window
        main_window.logger = init_logger(main_window.green_log_box)           # ✅ Set up logging
        from utils import masters
        main_window.logger.info(masters.describe())  # which master — first line, always
        from utils.config import MIGRATION_NOTES
        for note in MIGRATION_NOTES:          # settings moved out of the repo root
            main_window.logger.info(note)
        from utils import site_config
        if not site_config.publishing_set_up():
            main_window.logger.warning(site_config.NOT_SET_UP_MSG)
        from utils import local_web
        local_web.ensure_started()     # the local website, when that is the choice
        if site_config.is_local():     # a server is checked on Settings ▸ Save, not every start
            from engine.site_init import initialize_site
            initialize_site()          # starter pages of a new master / trip
        if _config.get("Global", "debug_tools", fallback="no") == "yes":
            print(f"✅ Logging configured, logger assigned: {main_window.logger}")

        main_window._report_drive_space()     # ✅ needs the logger, hence here
        main_window._report_master_snapshot()  # ✅ same reason
        populate_trip_overview(main_window)  # ✅ Now logger is ready
        # The two scan-backed lanes, so all four tabs exist before the mode is
        # restored — otherwise starting in Unfiled or Days would find no tab and
        # sync_yellow_to_mode would (correctly) complain.
        from gui.days_overview import populate_days_overview
        from gui.unfiled_overview import populate_unfiled_overview
        populate_days_overview(main_window)
        populate_unfiled_overview(main_window)
        # All overviews are now built; honour the Unfiled/Day/Trip/Album mode the
        # user last had active (this must run after the final populate, which
        # forces its own yellow tab selected).
        main_window._restore_active_left_mode()
        splash.finish(main_window)
        main_window.show()
        # Once in a while Windows made the taskbar button before it read the
        # window's icon and showed a generic one (2026-10-13/14, twice an
        # evening). Setting the icon again, right after the window appears,
        # makes Windows refresh the button.
        QTimer.singleShot(500, lambda: main_window.setWindowIcon(_app_icon()))
        # Content-driven splitter sizes (restored from [layout] in the per-machine
        # .ini) can ask for a wider/taller window than the current screen offers —
        # e.g. this machine's config carrying sizes saved on a bigger monitor.
        # Same off-screen guard the per-dialog geometry already gets.
        clamp_to_visible_screen(main_window)

        # A master without a collection can do almost nothing (a new user, a
        # new test master): show what to set up instead of a wall of grey.
        # No collection at all (a first start): offer to create one first.
        from gui.welcome import needs_welcome, show_welcome
        created = False
        if needs_welcome():
            show_welcome(main_window)
            created = not needs_welcome()     # then a restart is on its way
        from gui.setup_checklist import master_needs_setup, show_checklist
        if not created and master_needs_setup():
            show_checklist(main_window)

        # main_window.init_panels()  # ✅ Safe moment — QApplication fully live

    # Show the splash for at least MIN_SPLASH_MS (so it never just flashes),
    # then proceed as soon as the master DB warm-up actually finishes rather
    # than a flat, guessed delay. MAX_WAIT_MS is a safety net in case the
    # warm-up thread wedges — the app still starts, just without the head
    # start on the master DB rebuild.
    MIN_SPLASH_MS = 500
    MAX_WAIT_MS = 10_000
    _splash_start = time.monotonic()

    def _wait_for_warm():
        elapsed_ms = (time.monotonic() - _splash_start) * 1000
        ready = _warm_done.is_set() or elapsed_ms >= MAX_WAIT_MS
        if ready and elapsed_ms >= MIN_SPLASH_MS:
            launch_main_window()
        else:
            QTimer.singleShot(50, _wait_for_warm)

    QTimer.singleShot(MIN_SPLASH_MS, _wait_for_warm)

    # sys.exit(app.exec())  # 🧠 Start the Qt event loop

def _app_icon() -> QIcon:
    """The duck: one icon with every drawn size, so each place (title bar,
    taskbar, Alt-Tab, Dock) picks the sharpest one."""
    icon_dir = Path(__file__).parent / "assets" / "icon"
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        icon.addFile(str(icon_dir / f"speciesviewer_{size}.png"), QSize(size, size))
    return icon


def _set_windows_app_id() -> None:
    """Without its own AppUserModelID, Windows groups SpeciesViewer under
    python.exe and shows Python's icon in the taskbar, whatever the window
    icon says. Must run before the first window appears."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "SpeciesViewer.SpeciesViewer")
    except Exception:
        pass   # cosmetic only — the app works without it


def main():
    _set_windows_app_id()
    app = QApplication(sys.argv)
    from utils import single_instance
    if not single_instance.claim():
        sys.exit(0)                       # the running one comes to the front
    app.setWindowIcon(_app_icon())        # every window and dialog inherits it
    QToolTip.setFont(QFont("SansSerif", 12))
    from gui import theme
    if theme.is_dark():
        app.setStyle("Fusion")            # Fusion honours a custom QPalette everywhere
        app.setPalette(theme.build_palette())
    app.setStyleSheet(build_base_qss())  # ch.15 global QSS theme, Phase 1 — see gui/theme.py
    ex = start_main()
    # print("QApplication exists in TripCreationDialog?", QApplication.instance())
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
