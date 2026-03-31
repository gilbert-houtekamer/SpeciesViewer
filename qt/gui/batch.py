import logging
import configparser
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QApplication
)
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal
from PyQt6.QtGui import QMovie

from utils.batch_config import generate_svconfig_ini
from sharedlib.statemachine import StateMachine


class _BatchWorker(QObject):
    finished = pyqtSignal(str)  # errorstring — empty means success

    def __init__(self, module_called, args, module_config):
        super().__init__()
        self._module_called = module_called
        self._args = args
        self._module_config = module_config

    def run(self):
        try:
            self._module_called(self._args, self._module_config)
            self.finished.emit("")
        except Exception as e:
            self.finished.emit(str(e))


class BatchModuleRunner:
    def __init__(self, gui, main_window):
        self.parent = gui
        self.logger = gui.logger
        self.status_label = gui.status_label
        self.status_bar = gui.passed_parent.status_bar
        self.main_window = main_window

    def run(self, menu_spec):
        batch_parms = menu_spec.get_batch_parms()
        if not batch_parms:
            self.status_label.setText("❌ Missing batch parameters.")
            return

        module_called = batch_parms["module_called"]
        before_msg = batch_parms["before_msg"]
        after_msg = batch_parms["after_msg"]
        statusname = batch_parms["statusname"]

        # Config prep
        generate_svconfig_ini()
        args = None
        module_config = configparser.ConfigParser()
        module_config.read("svconfig.ini")

        sm = StateMachine(
            module_config.get("repository", "BiotaDatabase"),
            module_config.get("project", "PictureDirectory"),
            readState=True
        )

        directory_name = Path(module_config.get("project", "PictureDirectory")).name
        expected_messages = {
            "ReadDirExif":   'Read\nDirectory',
            "WritePictures": 'Write\nSummary',
            "WriteBlog":     'Write\nBlog',
            "BuildWebsite":  'Build\nWebsite',
            "Freeze":        'Freeze',
            "WriteCommit":   'Write\nCommit'
        }

        if statusname and not sm.stateOKtorun(statusname):
            msg = sm.explainAbort(statusname, raiseSV=False)
            self.logger.warning(f"❌ {statusname} cannot run: {msg}")
            self.status_label.setText(f"❌ {msg}")
            self.status_bar.showMessage(f"❌ {msg}")
            return

        # Spinner — stays visible and animated while worker thread runs
        spinner = QDialog(self.parent)
        spinner.setWindowTitle("Even geduld...")
        spinner.setModal(True)
        spinner.setFixedSize(400, 150)

        layout = QVBoxLayout(spinner)
        label = QLabel(before_msg)
        layout.addWidget(label, alignment=Qt.AlignmentFlag.AlignCenter)

        message_label = QLabel("")
        message_label.setWordWrap(True)
        layout.addWidget(message_label, alignment=Qt.AlignmentFlag.AlignCenter)

        movie = QMovie("spinner.gif")
        spinner_label = QLabel()
        spinner_label.setMovie(movie)
        layout.addWidget(spinner_label, alignment=Qt.AlignmentFlag.AlignCenter)
        movie.start()

        spinner.show()

        logger = logging.getLogger("speciesviewer")
        before = logger.warning_counter.count
        self.logger.info(before_msg)

        # Worker thread — keeps the event loop free so the UI stays responsive
        self._thread = QThread()
        self._worker = _BatchWorker(module_called, args, module_config)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)

        def on_finished(errorstring):
            self._thread.quit()
            self._thread.wait()
            spinner.close()

            after = logger.warning_counter.count
            new_warnings = after - before

            if errorstring:
                self.logger.warning(f"❌ {statusname} failed: {errorstring}")
                if statusname and statusname != "MakeRepository":
                    sm.updateState(statusname, False, errorstring)
                    cell_key = expected_messages.get(statusname)
                    if cell_key:
                        self.main_window.update_trip_cell(directory_name, cell_key, "❌", f"{statusname} failed: {errorstring}")
                self.status_label.setText(f"❌ {errorstring}")
                self.status_bar.showMessage(f"❌ {errorstring}")
            else:
                warn_suffix = f" Please review the {new_warnings if new_warnings > 1 else ''} warning{'s' if new_warnings > 1 else ''}." if new_warnings > 0 else ""
                self.logger.info(f"{after_msg}{warn_suffix}")
                if statusname and statusname != "MakeRepository":
                    sm.updateState(statusname, True, "Successfully run.")
                    cell_key = expected_messages.get(statusname)
                    if cell_key:
                        try:
                            cell_msg = f"Successfully run, but review {new_warnings} warning{'s' if new_warnings > 1 else ''}." if new_warnings > 0 else "Successfully run."
                            self.main_window.update_trip_cell(directory_name, cell_key, "✅", cell_msg)
                        except Exception:
                            pass
                self.status_label.setText(f"{after_msg}{warn_suffix}")
                self.status_bar.showMessage(f"{after_msg}{warn_suffix}")

            if statusname and statusname != "MakeRepository":
                sm.writeState()
            self.main_window.rebuild_menu()
            self.main_window.green_log_box.verticalScrollBar().setValue(
                self.main_window.green_log_box.verticalScrollBar().maximum()
            )

        self._worker.finished.connect(on_finished)
        self._thread.start()
