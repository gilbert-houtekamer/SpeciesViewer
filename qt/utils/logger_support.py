import logging
from PyQt6.QtCore import QTimer

class SuppressExifToolOutput(logging.Filter):
    def filter(self, record):
        # first does not work, but leave it as a sample
        return not (
            record.name.startswith("qt.imageformats.tiff") or
            record.getMessage().startswith("ExifToolHelper.execute: OUT stdout =")
        )

class LogHandler(logging.Handler):
    def __init__(self, log_widget):
        super().__init__()
        self.log_widget = log_widget

    def emit(self, record):
        msg = self.format(record)

        def append():
            self.log_widget.appendPlainText(msg)

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
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(sh)

    # GUI output via custom handler
    # gui_handler = LogHandler(self.log_panel.log_output)  # Make sure this is a QTextEdit or QPlainTextEdit
    gui_handler = LogHandler(log_widget)  # No .log_output needed now    
    logger.addHandler(gui_handler)

    logger.addFilter(SuppressExifToolOutput())

    logger.info("Logging configured")
        
    return logger
