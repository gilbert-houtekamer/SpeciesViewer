import logging
import sys
import threading
from pathlib import Path

# Ensure both the project root and qt/ are on sys.path regardless of CWD
_qt_dir = Path(__file__).resolve().parent
_project_root = _qt_dir.parent
for _p in [str(_qt_dir), str(_project_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtWidgets import QApplication, QSplashScreen, QToolTip
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtCore import Qt, QTimer

from utils.config import _config


def _warm_master_db_in_background() -> None:
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
    """
    def _run():
        # Diagnostic prints go straight to stderr so they survive
        # init_logger()'s handler.clear() + mode="w" reset at t=2s; the
        # post-init logger.info also writes to studio.log.
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

    threading.Thread(target=_run, name="MasterDBWarm", daemon=True).start()

# Strong, module-level reference to the main window for the app's lifetime.
# Without it the window is created as a local inside launch_main_window and kept
# alive only by Qt (C++). Python's cyclic GC — fired by big allocations such as
# the Rating Editor's master CSV read (~260k dicts) — then reclaims the whole
# unrooted Python widget graph mid-operation, deleting live widgets and crashing
# with "wrapped C/C++ object ... has been deleted".
_main_window = None


def start_main():
    # 🖼️ Splash screen setup
    splash_pix = QPixmap(str(Path(__file__).parent / "assets" / "svsplash.jpg"))
    splash = QSplashScreen(splash_pix)
    splash.setWindowFlag(Qt.WindowType.FramelessWindowHint)

    font_title = QFont("Arial", 20, QFont.Weight.Bold)
    splash.setFont(font_title)

    # Use HTML to style each line separately
    splash_text = (
        '<div style="text-align:center;">'
        '<span style="font-size:24pt; font-weight:bold;color:white;">Welcome to Species Viewer</span><br>'
        '<span style="font-size:16pt;color:white;">Organize species. Explore collections. Bring biodiversity to life.</span>'
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
    _warm_master_db_in_background()

    # 🪄 Launch main app window after splash
    def launch_main_window():
        from gui.mainwindow import MainWindow
        from gui.window_state import clamp_to_visible_screen
        from utils.logger_support import init_logger
        from gui.trip_overview import populate_trip_overview
        global _main_window
        _main_window = MainWindow()
        main_window = _main_window
        main_window.logger = init_logger(main_window.green_log_box)           # ✅ Set up logging
        if _config.get("Global", "debug_tools", fallback="no") == "yes":
            print(f"✅ Logging configured, logger assigned: {main_window.logger}")

        populate_trip_overview(main_window)  # ✅ Now logger is ready
        # Both overviews are now built; honour the tab the user last chose (this must
        # run after the final populate, which forces the Active Trip tab selected).
        main_window._restore_active_yellow_tab()
        splash.finish(main_window)
        main_window.show()
        # Content-driven splitter sizes (restored from [layout] in the per-machine
        # .ini) can ask for a wider/taller window than the current screen offers —
        # e.g. this machine's config carrying sizes saved on a bigger monitor.
        # Same off-screen guard the per-dialog geometry already gets.
        clamp_to_visible_screen(main_window)
       
        # main_window.init_panels()  # ✅ Safe moment — QApplication fully live        

    QTimer.singleShot(2000, launch_main_window)  # ⏱️ Delay splash by 2 sec

    # sys.exit(app.exec())  # 🧠 Start the Qt event loop

def main():
    app = QApplication(sys.argv)
    QToolTip.setFont(QFont("SansSerif", 12))
    app.setStyleSheet("""
        QToolTip {
            background-color: white;
            color: #333;
            border: 1px solid #ccc;
            padding: 4px;
        }
    """)    
    ex = start_main()
    # print("QApplication exists in TripCreationDialog?", QApplication.instance()) 
    sys.exit(app.exec())    

if __name__ == "__main__":
    main()
