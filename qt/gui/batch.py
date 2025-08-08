import logging
import configparser
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QApplication
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QMovie

from utils.batch_config import generate_svconfig_ini
from sharedlib.statemachine import StateMachine

class BatchModuleRunner:
    def __init__(self, gui, main_window):
        self.config = gui.config
        self.parent = gui
        self.logger = gui.logger
        self.status_label = gui.status_label
        self.status_bar = gui.passed_parent.status_bar
        self.main_window = main_window  # ✅ Save reference

    def run(self, menu_spec):
        batch_parms = menu_spec.get_batch_parms()
        if not batch_parms:
            self.status_label.setText("❌ Missing batch parameters.")
            return

        module_called = batch_parms["module_called"]
        before_msg = batch_parms["before_msg"]
        after_msg = batch_parms["after_msg"]
        statusname = batch_parms["statusname"]

        # Spinner setup
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
        QApplication.processEvents()

        # Config prep
        generate_svconfig_ini(self.config)
        args = None
        module_config = configparser.ConfigParser()
        module_config.read("svconfig.ini")

        sm = StateMachine(
            module_config.get("repository", "BiotaDatabase"),
            module_config.get("project", "PictureDirectory"),
            readState=True
        )

        errorstring = ""

        try:
            if not sm.stateOKtorun(statusname):
                msg = sm.explainAbort(statusname, raiseSV=False)
                self.logger.warning(f"❌ {statusname} cannot run: {msg}")
                message_label.setText(f"<b>Task cannot run:</b><br>{msg}")
                errorstring = msg
                spinner.close()

                # Update UI
                self.status_label.setText(f"❌ {errorstring}")
                self.status_bar.showMessage(f"❌ {errorstring}")
                return  # ✅ Prevent further execution

            else:
                self.logger.info(before_msg)
                module_called(args, module_config)
        except Exception as e:
            errorstring = str(e)
            self.logger.warning(f"❌ {statusname} failed: {errorstring}")
            sm.updateState(statusname, False, errorstring)
            message_label.setText(f"<b>Task failed:</b><br>{errorstring}")
        else:
            self.logger.info(after_msg)
            sm.updateState(statusname, True, "Successfully run.")
            self.main_window.rebuild_menu()
        finally:
            if statusname != "MakeRepository": sm.writeState()
            spinner.close()

        # Update UI
        if errorstring:
            self.status_label.setText(f"❌ {errorstring}")
            self.status_bar.showMessage(f"❌ {errorstring}")
        else:
            self.status_label.setText(after_msg)
            self.status_bar.showMessage(after_msg)

        return errorstring