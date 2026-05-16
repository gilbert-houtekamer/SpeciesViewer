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
    "SyncSidecars":  'Sync\nSidecars',
    "ReadDirExif":   'Read\nDirectory',
    "WritePictures": 'Write\nSummary',
    "WriteBlog":     'Write\nBlog',
    "BuildWebsite":  'Build\nWebsite',
    "Freeze":        'Freeze',
    "WriteCommit":   'Write\nCommit',
}


class _BatchWorker(QObject):
    finished = pyqtSignal(str, str)  # (errorstring, result_msg) — both empty means clean success

    def __init__(self, module_called, args, module_config):
        super().__init__()
        self._module_called = module_called
        self._args = args
        self._module_config = module_config

    def run(self):
        try:
            result = self._module_called(self._args, self._module_config)
            self.finished.emit("", result or "")
        except Exception as e:
            self.finished.emit(str(e), "")


class BatchModuleRunner:
    def __init__(self, gui, main_window):
        self.parent = gui
        self.logger = gui.logger
        self.status_label = gui.status_label
        self.status_bar = gui.passed_parent.status_bar
        self.main_window = main_window

    def run(self, menu_spec, on_success=None, on_failure=None):
        batch_parms = menu_spec.get_batch_parms()
        if not batch_parms:
            self.status_label.setText("❌ Missing batch parameters.")
            return

        module_called = batch_parms["module_called"]
        before_msg = batch_parms["before_msg"]
        after_msg = batch_parms["after_msg"]
        statusname = batch_parms["statusname"]
        master_mode = batch_parms.get("master_mode", False)

        # Config prep — master_mode flips BiotaDatabase to point at
        # master so steps like sv2/sv3/sv5/sv6/sv7 can run against
        # master directly without a trip context.
        generate_svconfig_ini(master_mode=master_mode)
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

        def on_finished(errorstring, result_msg):
            self._thread.quit()
            self._thread.wait()
            spinner.close()

            after = logger.warning_counter.count
            new_warnings = after - before

            if errorstring:
                self.logger.warning(f"❌ {statusname} failed: {errorstring}")
                # Cap what we shove into the wordwrap labels — pyexiftool
                # and friends sometimes embed multi-MB stdout dumps in
                # CalledProcessError messages, which a wordwrap QLabel
                # would try to render as a single 2.5-million-pixel cell.
                short_err = errorstring if len(errorstring) <= 500 else (
                    errorstring[:500] + " […truncated; full error in log]"
                )
                if statusname and statusname != "MakeRepository":
                    sm.updateState(statusname, False, errorstring)
                    cell_key = expected_messages.get(statusname)
                    if cell_key:
                        self.main_window.update_trip_cell(directory_name, cell_key, "❌", f"{statusname} failed: {short_err}")
                self.status_label.setText(f"❌ {short_err}")
                self.status_bar.showMessage(f"❌ {short_err}")
                if on_failure:
                    on_failure(errorstring)
            else:
                warn_suffix = f" Please review the {new_warnings if new_warnings > 1 else ''} warning{'s' if new_warnings > 1 else ''}." if new_warnings > 0 else ""
                display_msg = result_msg if result_msg else after_msg
                self.logger.info(f"{display_msg}{warn_suffix}")
                if statusname and statusname != "MakeRepository":
                    sm.updateState(statusname, True, "Successfully run.")
                    cell_key = expected_messages.get(statusname)
                    if cell_key:
                        try:
                            cell_msg = f"Successfully run, but review {new_warnings} warning{'s' if new_warnings > 1 else ''}." if new_warnings > 0 else "Successfully run."
                            self.main_window.update_trip_cell(directory_name, cell_key, "✅", cell_msg)
                        except Exception:
                            pass
                self.status_label.setText(f"{display_msg}{warn_suffix}")
                self.status_bar.showMessage(f"{display_msg}{warn_suffix}")
                if on_success:
                    on_success()

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

        def on_done(errorstring, _result_msg, idx=index, sname=statusname, amsg=after_msg):
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


class ColumnBatchRunner:
    """Runs one batch step across multiple days sequentially, continuing on errors.

    day_steps: list of dicts with keys:
        day_str        - folder name for this day
        project_root   - Path to image root (parent of day folders)
        trip_root      - Path to trip biota database
        module_called  - callable for the batch module
        statusname     - StateMachine key (e.g. 'ReadDirExif')
        before_msg     - spinner label shown before running
        after_msg      - log message on success
        needs_reset    - bool: True if step is already ✅ and must be reset first
    """

    def __init__(self, parent, main_window, action_label, day_steps, on_complete):
        self._parent = parent
        self._main_window = main_window
        self._action_label = action_label
        self._day_steps = day_steps
        self._on_complete = on_complete
        self._logger = logging.getLogger("speciesviewer")
        self._before_count = self._logger.warning_counter.count
        self._errors = 0
        self._successes = 0

        total = len(day_steps)
        self._spinner = QDialog(parent)
        self._spinner.setWindowTitle("Even geduld...")
        self._spinner.setModal(True)
        self._spinner.setFixedSize(460, 220)

        layout = QVBoxLayout(self._spinner)
        layout.setContentsMargins(20, 16, 20, 16)

        self._action_label_widget = QLabel(f"Running '{action_label}' for all days...")
        self._action_label_widget.setWordWrap(True)
        self._action_label_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._action_label_widget)

        self._step_label = QLabel("")
        self._step_label.setWordWrap(True)
        self._step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._step_label)

        self._progress_label = QLabel(f"0 / {total}")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._progress_label)

        movie = QMovie("spinner.gif")
        spinner_icon = QLabel()
        spinner_icon.setMovie(movie)
        layout.addWidget(spinner_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        movie.start()

    def start(self):
        self._spinner.show()
        self._run_step(0)

    def _run_step(self, index):
        if index >= len(self._day_steps):
            self._all_done()
            return

        step = self._day_steps[index]
        day_str = step["day_str"]
        project_root = step["project_root"]
        trip_root = step["trip_root"]
        module_called = step["module_called"]
        statusname = step["statusname"]
        before_msg = step["before_msg"]
        after_msg = step["after_msg"]
        needs_reset = step["needs_reset"]

        total = len(self._day_steps)
        self._step_label.setText(f"{before_msg} ({day_str})")
        self._progress_label.setText(f"{index + 1} / {total}")
        self._logger.info(f"{before_msg} — {day_str}")

        # Point config at this day and regenerate svconfig.ini
        from utils.config import _config
        new_dir = project_root / day_str
        _config.set("project", "picturedirectory", str(new_dir))
        generate_svconfig_ini()

        module_config = configparser.ConfigParser()
        module_config.read("svconfig.ini")

        review_day = trip_root / "metadata" / "review" / day_str
        sm = StateMachine(str(trip_root), str(review_day), readState=True)

        if needs_reset and statusname in sm.Taskstate:
            sm.Taskstate[statusname][0] = False
            sm.Taskstate[statusname][1] = ""
            sm.writeState()

        self._thread = QThread()
        self._worker = _BatchWorker(module_called, None, module_config)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)

        def on_done(errorstring, result_msg, idx=index, sname=statusname, amsg=after_msg,
                    d_str=day_str, sm_ref=sm, trip=trip_root, review=review_day):
            self._thread.quit()
            self._thread.wait()
            cell_key = _EXPECTED_CELLS.get(sname)
            if errorstring:
                self._errors += 1
                self._logger.warning(f"❌ {d_str} — {sname} failed: {errorstring}")
                sm_ref.updateState(sname, False, errorstring)
                sm_ref.writeState()
                if cell_key:
                    self._main_window.update_trip_cell(d_str, cell_key, "❌", f"{sname} failed: {errorstring}")
            else:
                self._successes += 1
                self._logger.info(f"{amsg} — {d_str}")
                sm_ref.updateState(sname, True, "Successfully run.")
                sm_ref.writeState()
                if cell_key:
                    self._main_window.update_trip_cell(d_str, cell_key, "✅", "Successfully run.")
            # Refresh the full row so downstream steps update eligibility
            sm_ref2 = StateMachine(str(trip), str(review), readState=True)
            self._main_window.refresh_trip_row(sm_ref2, d_str)
            # Continue with next day regardless of error
            self._run_step(idx + 1)

        self._worker.finished.connect(on_done)
        self._thread.start()

    def _all_done(self):
        self._spinner.close()
        after_count = self._logger.warning_counter.count
        new_warnings = after_count - self._before_count
        summary = f"'{self._action_label}' complete: {self._successes} succeeded, {self._errors} failed."
        if new_warnings > 0:
            summary += f" Please review the {new_warnings} warning{'s' if new_warnings > 1 else ''}."
        self._logger.info(summary)
        self._main_window.rebuild_menu()
        self._main_window.green_log_box.verticalScrollBar().setValue(
            self._main_window.green_log_box.verticalScrollBar().maximum()
        )
        self._on_complete()
