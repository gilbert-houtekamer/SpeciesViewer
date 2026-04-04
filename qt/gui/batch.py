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

_EXPECTED_CELLS = {
    "ReadDirExif":   'Read\nDirectory',
    "WritePictures": 'Write\nSummary',
    "WriteBlog":     'Write\nBlog',
    "BuildWebsite":  'Build\nWebsite',
    "Freeze":        'Freeze',
    "WriteCommit":   'Write\nCommit',
}


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
        expected_messages = _EXPECTED_CELLS

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
                # Refresh the entire trip row so subsequent steps become eligible
                self.main_window.refresh_trip_row(sm, directory_name)
            self.main_window.rebuild_menu()
            self.main_window.green_log_box.verticalScrollBar().setValue(
                self.main_window.green_log_box.verticalScrollBar().maximum()
            )

        self._worker.finished.connect(on_finished)
        self._thread.start()


class ChainedBatchRunner:
    """Runs a sequence of batch steps one after another, sharing a single spinner.

    steps: list of (module_called, statusname, before_msg, after_msg)
    """

    def __init__(self, parent, main_window, steps, module_config, sm, directory_name, on_complete):
        self._parent = parent
        self._main_window = main_window
        self._steps = steps
        self._module_config = module_config
        self._sm = sm
        self._directory_name = directory_name
        self._on_complete = on_complete
        self._logger = logging.getLogger("speciesviewer")
        self._before_count = self._logger.warning_counter.count

        # Build shared spinner
        self._spinner = QDialog(parent)
        self._spinner.setWindowTitle("Even geduld...")
        self._spinner.setModal(True)
        self._spinner.setFixedSize(420, 170)

        layout = QVBoxLayout(self._spinner)
        self._step_label = QLabel("")
        self._step_label.setWordWrap(True)
        layout.addWidget(self._step_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._progress_label = QLabel("")
        layout.addWidget(self._progress_label, alignment=Qt.AlignmentFlag.AlignCenter)

        movie = QMovie("spinner.gif")
        spinner_icon = QLabel()
        spinner_icon.setMovie(movie)
        layout.addWidget(spinner_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        movie.start()

    def start(self):
        self._spinner.show()
        self._run_step(0)

    def _run_step(self, index):
        if index >= len(self._steps):
            self._all_done()
            return

        module_called, statusname, before_msg, after_msg = self._steps[index]
        total = len(self._steps)
        self._step_label.setText(f"Step {index + 1} of {total}: {before_msg}")
        self._progress_label.setText(", ".join(s for _, s, _, _ in self._steps[index:]))
        self._logger.info(before_msg)

        self._thread = QThread()
        self._worker = _BatchWorker(module_called, None, self._module_config)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)

        def on_done(errorstring, idx=index, sname=statusname, amsg=after_msg):
            self._thread.quit()
            self._thread.wait()
            cell_key = _EXPECTED_CELLS.get(sname)
            if errorstring:
                self._logger.warning(f"❌ {sname} failed: {errorstring}")
                self._sm.updateState(sname, False, errorstring)
                if cell_key:
                    self._main_window.update_trip_cell(self._directory_name, cell_key, "❌", f"{sname} failed: {errorstring}")
                self._sm.writeState()
                self._spinner.close()
                self._on_complete()
            else:
                self._logger.info(amsg)
                self._sm.updateState(sname, True, "Successfully run.")
                if cell_key:
                    self._main_window.update_trip_cell(self._directory_name, cell_key, "✅", "Successfully run.")
                self._run_step(idx + 1)

        self._worker.finished.connect(on_done)
        self._thread.start()

    def _all_done(self):
        after_count = self._logger.warning_counter.count
        new_warnings = after_count - self._before_count
        self._sm.writeState()
        self._main_window.refresh_trip_row(self._sm, self._directory_name)
        self._spinner.close()
        if new_warnings > 0:
            self._logger.info(f"All steps complete. Please review the {new_warnings} warning{'s' if new_warnings > 1 else ''}.")
        else:
            self._logger.info("All steps complete.")
        self._main_window.rebuild_menu()
        self._main_window.green_log_box.verticalScrollBar().setValue(
            self._main_window.green_log_box.verticalScrollBar().maximum()
        )
        self._on_complete()
