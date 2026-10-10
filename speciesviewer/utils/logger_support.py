import html
import logging
import logging.handlers
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

# How long previous sessions of studio.log are kept alongside the current one.
# One file per run, not per size: away from home the interesting log is usually
# the one from the run BEFORE the one you are staring at.
#
# By AGE, not by count (2026-09-29). It used to be the last 5 runs, and a
# morning of test restarts pushed out the one log that recorded every colour
# click of the evening the colours were lost — the only record there was.
# LOG_MIN_SESSIONS are kept whatever their age, so an idle machine still has
# its last runs.
LOG_KEEP_DAYS = 14
LOG_MIN_SESSIONS = 5


def _archive_previous_log(path: Path) -> None:
    """Move the previous session's studio.log aside as studio-<end time>.log,
    then delete archived sessions older than LOG_KEEP_DAYS (always keeping the
    newest LOG_MIN_SESSIONS). Also sweeps the old studio.log.N rotation files.
    Never raises: a log that cannot be archived must not stop the program."""
    import time
    try:
        if path.exists() and path.stat().st_size:
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S",
                                  time.localtime(path.stat().st_mtime))
            dest = path.with_name(f"studio-{stamp}.log")
            n = 1
            while dest.exists():
                dest = path.with_name(f"studio-{stamp}-{n}.log")
                n += 1
            path.rename(dest)
    except OSError:
        return      # a second instance holding the file: leave it, append below
    try:
        old = [p for p in path.parent.iterdir()
               if p.name != path.name and p.name.startswith("studio")
               and (p.suffix == ".log" or ".log." in p.name)]
        old.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        cutoff = time.time() - LOG_KEEP_DAYS * 86400
        for p in old[LOG_MIN_SESSIONS:]:
            if p.stat().st_mtime < cutoff:
                p.unlink()
    except OSError:
        pass


def log_path() -> Path:
    """Absolute path of studio.log.

    It used to be the bare relative name "studio.log", which meant the file
    landed wherever SpeciesViewer happened to be launched from — the repo root
    from VS Code, somewhere else entirely from a shortcut — so there was no
    answer to "where is the log?". It now lives beside the other per-machine
    state, under the same per-OS app-data root the caches use.

    Falls back to the working directory if that root cannot be created, because
    a program that will not start because it cannot open its log file is worse
    than one with a log in an awkward place."""
    try:
        from utils.cache_paths import app_data_dir
        d = app_data_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d / "studio.log"
    except OSError:
        return Path("studio.log").resolve()

def log_file(name: str) -> Path:
    """Absolute path for an extra log or debug dump `name` (e.g. the Freeze
    "Species print.txt"), in the same logs folder as studio.log — never the
    working directory, which put these files in the source tree."""
    return log_path().parent / name

# Filter to suppress noisy ExifTool output
class SuppressExifToolOutput(logging.Filter):
    def filter(self, record):
        return not (
            record.name.startswith("qt.imageformats.tiff") or
            record.getMessage().startswith("ExifToolHelper.execute: OUT stdout =")
        )

# Cross-thread signal carrier for the GUI handler. Kept as a SEPARATE QObject
# (composition, not inheritance) so that LogHandler itself stays a pure-Python
# logging.Handler. At interpreter exit the stdlib's logging.shutdown walks every
# handler and does getattr(h, 'flushOnClose', True); if the handler were a
# QObject whose C++ half Qt already deleted, that getattr raises
# "wrapped C/C++ object ... has been deleted". With the QObject tucked inside,
# shutdown only ever touches the Python handler — no crash on close.
class _LogEmitter(QObject):
    sig = pyqtSignal(str)


# GUI handler for QTextEdit/QTextBrowser.
# The queued signal/slot connection handles cross-thread delivery: when emit()
# is called from a worker thread, the message is posted to the main thread's
# event loop and appears in real-time.
# The log console paints on LOG_BG (a green panel), so the highlight colours have
# to come from the theme: literal "blue" on the dark theme's LOG_BG was unreadable.
try:
    from gui.theme import WARN as _WARN, ALERT as _ALERT
except Exception:      # theme unavailable (headless batch run) — sane fallbacks
    _WARN, _ALERT = "#b45309", "#b91c1c"


class LogHandler(logging.Handler):
    def __init__(self, log_widget):
        super().__init__()
        self._emitter = _LogEmitter()
        self._emitter.sig.connect(log_widget.appendHtml)

    def emit(self, record):
        msg = self.format(record)
        # Escape so literal "<-" etc. in messages don't get parsed as malformed
        # HTML tags by appendHtml (Qt's parser can silently swallow them).
        safe = html.escape(msg)

        if ("may have a typo" in msg or "correct it" in msg or "GPS missing" in msg
                or record.levelno >= logging.ERROR):
            # ERROR was the one level with no colour at all: it printed in the
            # same ink as a routine INFO line, which is the exact failure this
            # panel exists to prevent.
            safe = f'<span style="color:{_ALERT};"><b>{safe}</b></span>'
        elif record.levelno == logging.WARNING:
            safe = f'<span style="color:{_WARN};"><b>{safe}</b></span>'

        try:
            self._emitter.sig.emit(safe)
        except RuntimeError:
            # Widget/emitter torn down during shutdown — drop the late record.
            pass

# Console formatter with ANSI coloring
class ConsoleFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)

        if "may have a typo" in msg or "correct it" in msg or "GPS missing" in msg:
            return f"\033[91m{msg}\033[0m"  # Red
        elif record.levelno == logging.WARNING:
            return f"\033[94m{msg}\033[0m"  # Blue
        return msg
    
class WarningCounter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.count = 0

    def filter(self, record):
        if record.levelno == logging.WARNING:
            self.count += 1
        return True  # Always allow the record through


# Logger initializer
def init_logger(log_widget):
    from utils.config import debug_enabled
    logger = logging.getLogger("speciesviewer")
    logger.setLevel(logging.DEBUG if debug_enabled() else logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    # access to global counter
    warning_counter = WarningCounter()
    logger.addFilter(warning_counter)
    logger.warning_counter = warning_counter  # Attach for easy access    

    # File output. One log file per session: studio.log is always the current
    # run, and each earlier run is kept beside it as studio-<end time>.log (see
    # _archive_previous_log). The old mode="w" wiped the file on every start, so
    # the moment you restarted to look at what went wrong, the evidence was gone.
    path = log_path()
    _archive_previous_log(path)
    fh = logging.FileHandler(str(path), mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))
    logger.addHandler(fh)

    # Console output. The default console encoding on Windows is cp1252, which
    # raises on any emoji in a log message and prints a whole "--- Logging error
    # ---" traceback (the file handler above is already utf-8). Make the stream
    # escape un-encodable characters instead of crashing.
    import sys
    console_stream = sys.stderr
    try:
        console_stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    sh = logging.StreamHandler(console_stream)
    sh.setFormatter(ConsoleFormatter("%(message)s"))
    logger.addHandler(sh)

    # GUI output
    gui_handler = LogHandler(log_widget)
    logger.addHandler(gui_handler)

    # Say where the file is, every run. The log moved out of the launch
    # directory, so this line is the only thing that makes it findable — and it
    # is the first thing you want when asking someone to send you their log.
    logger.info(f"Log file: {path}  (earlier runs kept alongside as "
                f"studio-<date>.log for {LOG_KEEP_DAYS} days)")

    # Global filters
    logger.addFilter(SuppressExifToolOutput())

    logger.info("Logging configured")

    return logger
