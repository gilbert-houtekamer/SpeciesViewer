import html
import logging
import logging.handlers
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

# How long previous sessions of speciesviewer.log are kept alongside the current one.
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


# The log was "studio.log" until 0.5.0 (a name from the first Qt version);
# files by the old name are archived and swept the same way.
LOG_STEM = "speciesviewer"
_OLD_STEMS = ("studio",)


def _archive_one(path: Path, stem: str) -> None:
    import time
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(path.stat().st_mtime))
    dest = path.with_name(f"{stem}-{stamp}.log")
    n = 1
    while dest.exists():
        dest = path.with_name(f"{stem}-{stamp}-{n}.log")
        n += 1
    path.rename(dest)


def _archive_previous_log(path: Path) -> None:
    """Move the previous session's speciesviewer.log aside as
    speciesviewer-<end time>.log, then delete archived sessions older than
    LOG_KEEP_DAYS (always keeping the newest LOG_MIN_SESSIONS). Also sweeps the
    old studio.log / studio-*.log / .log.N files.
    Never raises: a log that cannot be archived must not stop the program."""
    import time
    try:
        if path.exists() and path.stat().st_size:
            _archive_one(path, LOG_STEM)
    except OSError:
        return      # a second instance holding the file: leave it, append below
    for stem in _OLD_STEMS:
        try:
            old_current = path.with_name(f"{stem}.log")
            if old_current.exists():
                _archive_one(old_current, stem)
        except OSError:
            pass
    try:
        old = [p for p in path.parent.iterdir()
               if p.name != path.name
               and p.name.startswith((LOG_STEM,) + _OLD_STEMS)
               and (p.suffix == ".log" or ".log." in p.name)]
        old.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        cutoff = time.time() - LOG_KEEP_DAYS * 86400
        for p in old[LOG_MIN_SESSIONS:]:
            if p.stat().st_mtime < cutoff:
                p.unlink()
    except OSError:
        pass


def log_path() -> Path:
    """Absolute path of speciesviewer.log.

    It used to be the bare relative name "speciesviewer.log", which meant the file
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
        return d / f"{LOG_STEM}.log"
    except OSError:
        return Path(f"{LOG_STEM}.log").resolve()

def log_file(name: str) -> Path:
    """Absolute path for an extra log or debug dump `name` (e.g. the Freeze
    "Species print.txt"), in the same logs folder as speciesviewer.log — never the
    working directory, which put these files in the source tree."""
    return log_path().parent / name

# Filter to suppress noisy ExifTool output
class SuppressExifToolOutput(logging.Filter):
    # pyexiftool's own chatter when it is handed our logger: start-up properties,
    # every command sent, the process start and stop. Kept with debug on.
    _CHATTER = ("Property '", "Method '")

    def filter(self, record):
        msg = record.getMessage()
        if record.name.startswith("qt.imageformats.tiff") or \
                msg.startswith("ExifToolHelper.execute: OUT stdout ="):
            return False
        if record.levelno <= logging.INFO and msg.startswith(self._CHATTER):
            return logging.getLogger("speciesviewer").isEnabledFor(logging.DEBUG)
        return True

class CapitalFirst(logging.Filter):
    """Every message the user reads starts with a capital, like a sentence
    ("northern nutcracker (…) is selected" → "Northern nutcracker …"). One rule
    here instead of a hunt through every log call. Left alone: "iPhone"-style
    words (second letter upper case) and a lower-case drive ("c:\…")."""

    def filter(self, record):
        if record.levelno < logging.INFO:
            return True
        msg = record.getMessage()
        i = len(msg) - len(msg.lstrip())       # an indented line ("   not validated: …")
        if (len(msg) > i + 1 and msg[i].islower() and not msg[i + 1].isupper()
                and msg[i + 1] != ":"):
            record.msg, record.args = msg[:i] + msg[i].upper() + msg[i + 1:], None
        return True


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


# Kept open for the whole run: faulthandler writes to the raw file descriptor
# at the moment of the crash, so the file must outlive every handler reset.
_crash_trace_file = None


def _enable_crash_trace(path: Path) -> None:
    """Write a Python stack trace into speciesviewer.log when SV dies below Python.

    A crash inside Qt (2026-10-20: a stale modal window after Read SD-card)
    ends the process with no exception and no log line — the log simply stops.
    faulthandler turns that into a "Fatal Python error" plus the stack of every
    thread, in the same file. Opened once; never raises."""
    global _crash_trace_file
    if _crash_trace_file is not None:
        return
    try:
        import faulthandler
        _crash_trace_file = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=_crash_trace_file, all_threads=True)
    except Exception:
        _crash_trace_file = None


# Logger initializer
def init_logger(log_widget):
    from utils.config import debug_enabled
    logger = logging.getLogger("speciesviewer")
    logger.setLevel(logging.DEBUG if debug_enabled() else logging.INFO)
    # Qt's own JPEG decoder complains on the terminal about pictures it still
    # shows fine ("Corrupt JPEG data: premature end of data segment"). Nothing
    # to act on, so only with debug on.
    # The SFTP library's "[chan 0] Opened sftp connection" likewise.
    if not debug_enabled():
        from PyQt6.QtCore import QLoggingCategory
        QLoggingCategory.setFilterRules("qt.gui.imageio.jpeg.warning=false")
        logging.getLogger("paramiko").setLevel(logging.WARNING)
    logger.propagate = False
    logger.handlers.clear()
    # access to global counter
    warning_counter = WarningCounter()
    logger.addFilter(warning_counter)
    logger.warning_counter = warning_counter  # Attach for easy access    

    # File output. One log file per session: speciesviewer.log is always the current
    # run, and each earlier run is kept beside it as speciesviewer-<end time>.log (see
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

    _enable_crash_trace(path)

    # GUI output
    gui_handler = LogHandler(log_widget)
    logger.addHandler(gui_handler)

    # Say where the file is, every run. The log moved out of the launch
    # directory, so this line is the only thing that makes it findable — and it
    # is the first thing you want when asking someone to send you their log.
    logger.info(f"Log file: {path}  (earlier runs kept alongside as "
                f"{LOG_STEM}-<date>.log for {LOG_KEEP_DAYS} days)")

    # Global filters
    logger.addFilter(SuppressExifToolOutput())
    logger.addFilter(CapitalFirst())

    logger.debug("Logging configured")

    return logger
