import logging
import configparser
from pathlib import Path
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
        # support for cell update
        directory_name = Path(module_config.get("project", "PictureDirectory")).name
        expected_messages = {
            "ReadDirExif" : 'Read\nDirectory',
            "WritePictures" : 'Write\nSummary',
            "WriteBlog"  : 'Write\nBlog',
            "BuildWebsite" : 'Build\nWebsite',
            "Freeze" : 'Freeze',
            "WriteCommit" : 'Write\nCommit'
        }

        new_warnings = 0        # make it is initialized
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
                # track warning count
                logger = logging.getLogger("speciesviewer")
                before = logger.warning_counter.count
                # call batch module 
                self.logger.info(before_msg)
                module_called(args, module_config)
                # get update counter
                after = logger.warning_counter.count
                new_warnings = after - before                
        except Exception as e:
            errorstring = str(e)
            self.logger.warning(f"❌ {statusname} failed: {errorstring}")
            message_label.setText(f"<b>Task failed:</b><br>{errorstring}") 
            if statusname != "":           
                sm.updateState(statusname, False, errorstring)
                self.main_window.update_trip_cell(directory_name, expected_messages[statusname], "❌", f"{statusname} failed: {errorstring}")            
        else:
            if new_warnings > 0:
                self.logger.info(f"{after_msg} Please review the {new_warnings if new_warnings>1 else ''} warning{'s' if new_warnings>1 else ''}.")
            else:
                self.logger.info(after_msg)
            if statusname != "":      
                sm.updateState(statusname, True, "Successfully run.")
                try:
                    if new_warnings > 0:
                        self.main_window.update_trip_cell(directory_name, expected_messages[statusname], "✅", 
                                f"Successfully run, but review {new_warnings if new_warnings>1 else ''} warning{'s' if new_warnings>1 else ''}.")
                    else:
                        self.main_window.update_trip_cell(directory_name, expected_messages[statusname], "✅", "Successfully run.")
                except:
                    pass
        finally:
            if statusname != "":      
                if statusname != "MakeRepository": 
                    print(f'sm.updateState({statusname} , True, "Successfully run.")')
                    sm.writeState()
                self.main_window.rebuild_menu()                
            spinner.close()

        # Update UI
        if errorstring:
            self.status_label.setText(f"❌ {errorstring}")
            self.status_bar.showMessage(f"❌ {errorstring}")
        else:
            if new_warnings > 0:
                self.status_label.setText(f"{after_msg} Please review the {new_warnings if new_warnings>1 else ''} warning{'s' if new_warnings>1 else ''}.")
                self.status_bar.showMessage(f"{after_msg} Please review the {new_warnings if new_warnings>1 else ''} warning{'s' if new_warnings>1 else ''}.")                
            else:
                self.status_label.setText(after_msg)
                self.status_bar.showMessage(after_msg)

        self.main_window.green_log_box.verticalScrollBar().setValue(
            self.main_window.green_log_box.verticalScrollBar().maximum()
        )

        return errorstring