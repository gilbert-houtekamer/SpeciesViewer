import logging
from PyQt6.QtCore import QTimer

class SuppressExifToolOutput(logging.Filter):
    def filter(self, record):
        # first does not work, but leave it as a sample
        return not (
            record.name.startswith("qt.imageformats.tiff") or
            record.getMessage().startswith("ExifToolHelper.execute: OUT stdout =")
        )
    
class HighlightTyposFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return "may have a typo" not in msg or "correct it" not in msg or True
    
def console_filter(record):
    # Don't modify record.msg directly
    return True

class LogHandler(logging.Handler):
    def __init__(self, log_widget):
        super().__init__()
        self.log_widget = log_widget

    def emit(self, record):
        msg = self.format(record)

        # Apply HTML styling for typo warnings
        if "may have a typo" in msg or "correct it" in msg:
            msg = f'<span style="color:red;"><b>{msg}</b></span>'

        def append():
            # Use appendHtml if you're using QTextEdit
            try:
                self.log_widget.appendHtml(msg)
            except AttributeError:
                self.log_widget.append(msg)

        QTimer.singleShot(0, append)



def init_logger(log_widget):
    logger = logging.getLogger("speciesviewer")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()

    # File output
    fh = logging.FileHandler("studio.log", mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))
    logger.addHandler(fh)

    # Console (stdout)
    class ConsoleFormatter(logging.Formatter):
        def format(self, record):
            msg = super().format(record)
            if "may have a typo" in msg or "correct it" in msg:
                return f"\033[91m{msg}\033[0m"
            return msg

    sh = logging.StreamHandler()
    sh.setFormatter(ConsoleFormatter("%(message)s"))
    logger.addHandler(sh)

    # GUI output via custom handler
    # gui_handler = LogHandler(self.log_panel.log_output)  # Make sure this is a QTextEdit or QPlainTextEdit
    gui_handler = LogHandler(log_widget)  # No .log_output needed now    
    logger.addHandler(gui_handler)

    logger.addFilter(SuppressExifToolOutput())

    logger.info("Logging configured")
        
    return logger
