import html
import logging
from PyQt6.QtCore import QObject, pyqtSignal

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

        if "may have a typo" in msg or "correct it" in msg or "GPS missing" in msg:
            safe = f'<span style="color:red;"><b>{safe}</b></span>'
        elif record.levelno == logging.WARNING:
            safe = f'<span style="color:blue;"><b>{safe}</b></span>'

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
    logger = logging.getLogger("speciesviewer")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()
    # access to global counter
    warning_counter = WarningCounter()
    logger.addFilter(warning_counter)
    logger.warning_counter = warning_counter  # Attach for easy access    

    # output handler
    # File output
    fh = logging.FileHandler("studio.log", mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))
    logger.addHandler(fh)

    # Console output
    sh = logging.StreamHandler()
    sh.setFormatter(ConsoleFormatter("%(message)s"))
    logger.addHandler(sh)

    # GUI output
    gui_handler = LogHandler(log_widget)
    logger.addHandler(gui_handler)

    # Global filters
    logger.addFilter(SuppressExifToolOutput())

    logger.info("Logging configured")

    return logger
