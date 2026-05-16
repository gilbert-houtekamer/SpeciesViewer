import flet as ft
import os
import asyncio
import logging



pause_toggle = ft.Switch(label="Pause Log Display", value=False)

SEVERITY_COLORS = {
    "INFO": ft.colors.ON_SURFACE,
    "WARNING": ft.colors.AMBER,
    "ERROR": ft.colors.RED,
    "DEBUG": ft.colors.BLUE_GREY
}

MAX_LOGS = 9999

class GUIHandler(logging.Handler):
    def __init__(self, log_box):
        super().__init__()
        self.log_box = log_box

    def emit(self, record):
        if pause_toggle.value:
            return  # Skip GUI update if paused        
      
        msg = self.format(record)
        color = SEVERITY_COLORS.get(record.levelname, ft.colors.ON_SURFACE)
        entry = ft.Text(
            msg,
            color=color,
            size=12,
            selectable=True,
            italic=(record.levelno == logging.DEBUG),
            weight=ft.FontWeight.BOLD if record.levelno >= logging.ERROR else ft.FontWeight.NORMAL
        )

        # 🧹 Truncate old logs if necessary
        lines = self.log_box.value.strip().splitlines()
        if len(lines) >= MAX_LOGS:
            self.log_box.value = "\n".join(lines[-MAX_LOGS+1:])

        self.log_box.value += msg + "\n"
        self.log_box.update()   

def setup_logger(gui_log_box=None):
    logger = logging.getLogger("speciesviewer")
    logger.setLevel(logging.INFO)

    # Remove all existing handlers — this is the critical fix
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler("speciesviewer.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if gui_log_box:
        gui_handler = GUIHandler(gui_log_box)
        gui_handler.setFormatter(formatter)
        logger.addHandler(gui_handler)

    return logger

# Central store for dialog-specific handlers
dialog_handlers = {}

def attach_gui_handler(logger, dialog_id, log_box, level=logging.INFO):
    # Prevent duplicate handlers for same dialog
    if dialog_id in dialog_handlers:
        return

    handler = GUIHandler(log_box)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
    dialog_handlers[dialog_id] = handler

def detach_gui_handler(logger, dialog_id):
    handler = dialog_handlers.get(dialog_id)
    if handler:
        logger.removeHandler(handler)
        handler.close()
        del dialog_handlers[dialog_id]