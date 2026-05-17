import logging
from PyQt6.QtCore import QObject, pyqtSignal

# Filter to suppress noisy ExifTool output
class SuppressExifToolOutput(logging.Filter):
    def filter(self, record):
        return not (
            record.name.startswith("qt.imageformats.tiff") or
            record.getMessage().startswith("ExifToolHelper.execute: OUT stdout =")
        )

# GUI handler for QTextEdit/QTextBrowser
# Inherits QObject so the signal/slot mechanism handles cross-thread delivery:
# when emit() is called from a worker thread, the queued connection automatically
# posts the message to the main thread's event loop — appearing in real-time.
class LogHandler(logging.Handler, QObject):
    _log_signal = pyqtSignal(str)

    def __init__(self, log_widget):
        logging.Handler.__init__(self)
        QObject.__init__(self)
        self._log_signal.connect(log_widget.appendHtml)

    def emit(self, record):
        msg = self.format(record)

        # Apply HTML styling based on content or level
        if "may have a typo" in msg or "correct it" in msg or "GPS missing" in msg:
            msg = f'<span style="color:red;"><b>{msg}</b></span>'
        elif record.levelno == logging.WARNING:
            msg = f'<span style="color:blue;"><b>{msg}</b></span>'

        self._log_signal.emit(msg)

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
