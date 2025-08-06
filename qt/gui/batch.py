import logging
import configparser
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
    QTabWidget, QWidget, QVBoxLayout, QComboBox, QLineEdit, QFormLayout, QGridLayout, QDateEdit, QTextEdit)
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QMovie

def run_batch_and_report(self, menu_spec):
    batch_parms = menu_spec.get_batch_parms()
    if not batch_parms:
        self.status_label.setText("❌ Missing batch parameters.")
        return

    batch = BatchModuleRunner(self.config, self, self.logger)
    errorstring = batch.run(
        batch_parms["module_called"],
        batch_parms["before_msg"],
        batch_parms["after_msg"],
        batch_parms["statusname"],
    )

    if errorstring == "":
        self.status_label.setText(batch_parms["after_msg"])
        self.passed_parent.status_bar.showMessage(batch_parms["after_msg"])
    else:
        self.status_label.setText(f"❌ {errorstring}")
        self.passed_parent.status_bar.showMessage(f"❌ {errorstring}")

class BatchModuleRunner:
    def __init__(self, config, parent_widget, logger):
        self.config = config
        self.parent = parent_widget
        self.logger = logger

    def run(self, module_called, before_msg, after_msg, statusname):
        from utils.batch_config import generate_svconfig_ini
        from sharedlib.statemachine import StateMachine

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

        # Status check — prerequisites missing
        if not sm.stateOKtorun(statusname):
            msg = sm.explainAbort(statusname, raiseSV=False)
            self.logger.warning(f"{statusname} cannot run: {msg}")
            message_label.setText(f"<b>Task cannot run:</b><br>{msg}")
            QApplication.processEvents()
        else:
            # Run module — catch execution errors
            self.logger.info(before_msg)
            module_called(args, module_config)
            rc, latest_state = sm.getState(statusname)
            if not rc:
                msg = f"<b>Task failed:</b><br>{latest_state}"
                message_label.setText(msg)
                self.logger.warning(msg)
                QApplication.processEvents()
            else:
                self.logger.info(after_msg)
                msg = ""
        
        spinner.close()
        return msg

