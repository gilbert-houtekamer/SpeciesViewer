from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QWidget, QVBoxLayout, QComboBox, QLineEdit, QFormLayout, QGridLayout, QDateEdit, QTextEdit)
from PyQt6.QtCore import Qt
import logging
import sys, os
from pathlib import Path
from gui.batch import BatchModuleRunner  # Assuming batch handler is here
import bin.sv1MakeReposity
import bin.sv2ReadDirExif
import bin.sv3WritePictures
import bin.sv4WriteBlog
import bin.sv5BuildWebsite
import bin.sv6Freeze
import bin.sv7WriteCommit
from utils.trip_config import read_trip_ini
from widgets.trip_widgets import (LabeledTextFieldQt, DateRangeFieldQt, DirectoryFieldQt, ImageFileFieldQt)
from sharedlib.translatelib import CachedTranslate

def generate_trip_ini(
    trip_root: str,
    trip_name: str,
    banner_filename: str,
    picture_filename: str,
    from_date: str,
    to_date: str,
    title_nl: str,
    message_nl: str,
    title_en: str,
    message_en: str,
    config,
    logger
):
    import os
    from datetime import datetime

    ini_path = os.path.join(trip_root, "trip.ini")

    master_web = config.get("master", "masterbiotawebroot", fallback="/nature/")
    master_disk = config.get("master", "masterbiotadatabase", fallback="E:\\Dropbox\\naturedb")
    trip_url_name = config.get("trip", "tripbiotawebroot", fallback=trip_name)

    ini_text = f"""\
[trip_config]
master_repository_web = {master_web}       
master_repository_disk = {master_disk}     
; the banner size is 2000x270 jpg, and the picture is 600x450 jpg
trip_banner = {banner_filename}             
trip_picture = {picture_filename}        
created = {datetime.now().strftime('%Y-%m-%d %H:%M')}           ; time this file is created/updated
trip_name = {trip_url_name}                 ; config: trip / tripbiotawebroot
fromdate = {from_date}
todate = {to_date}
oneblog = day                               ; hardcoded for now

[trip_text]
title_nl = {title_nl}
message_nl = {message_nl}
title_en = {title_en}
message_en = {message_en}
"""

    try:
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(ini_text)
            print(f"init_path={ini_path}\n{ini_text}")
        logger.info(f"✅ trip.ini written to {ini_path}")
        return
    except Exception as err:
        logger.info(f"❌ Failed to write trip.ini: {err}")
        return 

class TripCreationDialog(QDialog):
    def __init__(self, config, parent):
        super().__init__(parent)
        self.config = config
        self.logger = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Create Trip")
        self.resize(800, 600)

        trip_root = config.get("trip", "tripbiotadatabase", fallback="")
        trip_data = read_trip_ini(trip_root) if os.path.isdir(trip_root) else {}
  
        main_layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # Tab 1: Trip Folder
        tab1 = QWidget()
        layout1 = QVBoxLayout(tab1)
        self.root_field = DirectoryFieldQt("Trip root folder", trip_root)
        layout1.addWidget(self.root_field)
        tabs.addTab(tab1, "Select Trip Folder")

        # Tab 2: Images & Dates
        tab2 = QWidget()
        layout2 = QVBoxLayout(tab2)
        self.trip_img_field = ImageFileFieldQt("Trip Image", (600, 450), self.root_field.field)
        self.trip_img_field.field.setText(trip_data.get("trip_picture", ""))        
        self.topbar_img_field = ImageFileFieldQt("Top Web Bar", (2000, 270), self.root_field.field)
        self.topbar_img_field.field.setText(trip_data.get("trip_banner", ""))
        layout2.addWidget(self.trip_img_field)
        layout2.addWidget(self.topbar_img_field)
        self.date_field = DateRangeFieldQt()
        self.date_field.set_dates(trip_data.get("fromdate", ""), trip_data.get("todate", ""))
        layout2.addWidget(self.date_field)

        tabs.addTab(tab2, "Images && Dates")

        # Tab 3: Dutch, English, Create
        tab3 = QWidget()
        layout3 = QVBoxLayout(tab3)
        sub_tabs = QTabWidget()

        # Sub-tab: Dutch
        dutch_tab = QWidget()
        dutch_layout = QVBoxLayout(dutch_tab)
        self.title_nl_field = LabeledTextFieldQt("Trip title (Dutch)", trip_data.get("title_nl", ""))
        self.message_nl_field = LabeledTextFieldQt("Trip description (Dutch)", trip_data.get("message_nl", ""), multiline=True)
        translate_btn = QPushButton("Translate to English")
        translate_btn.clicked.connect(self.translate_dutch_to_english)

        dutch_layout.addWidget(translate_btn)
        dutch_layout.addWidget(self.title_nl_field)
        dutch_layout.addWidget(self.message_nl_field)        
        sub_tabs.addTab(dutch_tab, "Dutch Text")

        # Sub-tab: English
        english_tab = QWidget()
        english_layout = QVBoxLayout(english_tab)
        self.title_en_field = LabeledTextFieldQt("Trip title (English)", trip_data.get("title_en", ""))
        self.message_en_field = LabeledTextFieldQt("Trip description (English)", trip_data.get("message_en", ""), multiline=True)
        translate_btn_en = QPushButton("Translate to Dutch")
        translate_btn_en.clicked.connect(self.translate_english_to_dutch)        

        english_layout.addWidget(translate_btn_en)
        english_layout.addWidget(self.title_en_field)
        english_layout.addWidget(self.message_en_field)
        sub_tabs.addTab(english_tab, "English Text")

        layout3.addWidget(sub_tabs)
        tabs.addTab(tab3, "Trip Text")

        # Add all tabs to main layout
        main_layout.addWidget(tabs)

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        write_tripini_button = QPushButton("Write trip.ini")
        create_button = QPushButton("Create Trip")
        cancel_button = QPushButton("Cancel")

        # ✅ Add buttons to layout
        btn_layout.addWidget(write_tripini_button)
        btn_layout.addWidget(create_button)
        btn_layout.addWidget(cancel_button)

        main_layout.addLayout(btn_layout)

        # Hook up buttons
        write_tripini_button.clicked.connect(self.on_write_tripini_clicked)        
        create_button.clicked.connect(self.on_create_clicked)
        cancel_button.clicked.connect(self.reject)

    def closeEvent(self, event):
        self.passed_parent.translator.writeTranslationCache()
        super().closeEvent(event)

    def on_write_tripini_clicked(self):
        self.create_trip_ini()        
        self.logger.info("Trip.ini created or updated successfully!")        
        self.passed_parent.status_bar.showMessage("Trip.ini created or updated successfully!")

    def on_create_clicked(self):
        self.create_trip_ini()
        BatchModuleRunner(
            self.config,
            self.passed_parent,
            self.logger,
            bin.sv1MakeReposity.main,
            "Creating trip data structures...",
            "✅ Trip creation complete. Please review the log messages."
        )
        self.logger.info("Trip created successfully!")

    def translate_dutch_to_english(self):
        nl_title = self.title_nl_field.get_text()
        nl_message = self.message_nl_field.get_text()

        translated_title = self.passed_parent.translator.getOneLanguage(nl_title, languagein='en')
        translated_message = self.passed_parent.translator.getOneLanguage(nl_message, languagein='en')

        self.title_en_field.set_text(translated_title)
        self.message_en_field.set_text(translated_message)

        self.logger.info("✅ Translated Dutch to English using Google Translate.")

    def translate_english_to_dutch(self):
        en_title = self.title_en_field.get_text()
        en_message = self.message_en_field.get_text()

        translated_title = self.passed_parent.translator.getOneLanguage(en_title, languagein='nl')
        translated_message = self.passed_parent.translator.getOneLanguage(en_message, languagein='nl')

        self.title_nl_field.set_text(translated_title)
        self.message_nl_field.set_text(translated_message)

        self.logger.info("✅ Translated English to Dutch using Google Translate.")        

    def create_trip_ini(self):
        trip_root = self.root_field.get_path()
        banner_filename = self.topbar_img_field.get_filename()
        picture_filename = self.trip_img_field.get_filename()
        from_date, to_date = self.date_field.get_dates()

        title_nl = self.title_nl_field.get_text()
        message_nl = self.message_nl_field.get_text()
        title_en = self.title_en_field.get_text()
        message_en = self.message_en_field.get_text()

        if not trip_root or not os.path.isdir(trip_root):
            self.root_field.show_hint("Please select a valid trip folder.")
            return
        else:
            self.root_field.clear_hint()

        generate_trip_ini(
            trip_root,
            "tripname",  # You may want to make this dynamic later
            banner_filename,
            picture_filename,
            from_date,
            to_date,
            title_nl,
            message_nl,
            title_en,
            message_en,
            self.config,
            self.logger
    )

class ReadDirDialog(QDialog):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.setWindowTitle("Read Picture Directory")
        self.resize(600, 250)

        self.logger = logging.getLogger("speciesviewer")
        self.config = config
        self.main_window = parent

        full_path = Path(config["project"]["picturedirectory"])
        self.base_path = full_path.parent
        self.subdir_name = full_path.name

        layout = QVBoxLayout(self)

        # Base directory field
        self.base_dir_field = DirectoryFieldQt("Project root folder", str(self.base_path))
        layout.addWidget(self.base_dir_field)

        # Subdirectory dropdown
        self.subdir_dropdown = QComboBox()
        self.subdir_dropdown.setEditable(False)
        layout.addWidget(QLabel("Select subdirectory"))
        layout.addWidget(self.subdir_dropdown)

        # Info text
        self.info_text = QLabel("Scan a directory with photo files and extract metadata (Exif tags) for each image.")
        self.info_text.setWordWrap(True)
        layout.addWidget(self.info_text)

        # Status label
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Buttons layout
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.read_btn = QPushButton("Read Directory")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.read_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        # Signals
        self.base_dir_field.field.textChanged.connect(self.update_subdirs)
        self.read_btn.clicked.connect(self.on_read)
        self.close_btn.clicked.connect(self.close)

        self.update_subdirs()

    def update_subdirs(self):
        base_path = Path(self.base_dir_field.get_path())
        if base_path.is_dir():
            subdirs = [f.name for f in base_path.iterdir() if f.is_dir()]
            self.subdir_dropdown.clear()
            self.subdir_dropdown.addItems(subdirs)
            if self.subdir_name in subdirs:
                self.subdir_dropdown.setCurrentText(self.subdir_name)
        else:
            self.subdir_dropdown.clear()

    def on_read(self):
        selected_base = Path(self.base_dir_field.get_path())
        selected_subdir = self.subdir_dropdown.currentText()
        full_selected_path = selected_base / selected_subdir

        if not selected_base.is_dir():
            self.base_dir_field.show_hint("Invalid base directory.")
            return
        if not full_selected_path.is_dir():
            self.base_dir_field.show_hint("Selected subdirectory does not exist.")
            return

        self.config["project"]["picturedirectory"] = str(full_selected_path)
        self.logger.info(f"📁 Selected picture directory: {full_selected_path}")

        # Call batch with spinner
        batch = BatchModuleRunner(self.config, self, self.logger)
        batch.run(bin.sv2ReadDirExif.main, "Reading picture directory...", "✅ Directory scan complete.")
        self.status_label.setText("✅ Directory scan complete.")
        self.main_window.status_bar.showMessage("Directory scan complete.")

class WritePicturesDialog(QDialog):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.setWindowTitle("Write Summary Data")
        self.resize(600, 250)

        self.logger = logging.getLogger("speciesviewer")
        self.config = config
        self.main_window = parent        

        # Extract trip root and default subdir
        full_path = Path(config["project"]["picturedirectory"])
        self.base_path = full_path.parent
        self.default_subdir = full_path.name   # e.g. 2017-08-09
        self.trip_root = config["trip"]["tripbiotadatabase"] # E:\Dropbox\trips\2017 oostzeedb

        layout = QVBoxLayout(self)

        # Show root folder (non-editable)
        layout.addWidget(QLabel("Trip root folder"))
        self.root_label = QLineEdit(str(self.base_path))
        self.root_label.setReadOnly(True)
        layout.addWidget(self.root_label)

        # Subdirectory dropdown
        layout.addWidget(QLabel("Select subdirectory with metadata"))
        self.subdir_dropdown = QComboBox()
        self.subdir_dropdown.setEditable(False)
        layout.addWidget(self.subdir_dropdown)

        # Info text
        self.info_text = QLabel("Write summary data for a trip based on metadata files.")
        self.info_text.setWordWrap(True)
        layout.addWidget(self.info_text)

        # Status label
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Buttons layout
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.write_btn = QPushButton("Write Summary")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.write_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        # Signals
        self.write_btn.clicked.connect(self.on_write)
        self.close_btn.clicked.connect(self.close)

        # Populate dropdown
        self.update_subdirs()

    def update_subdirs(self):
        metadata_path = Path(os.path.join(self.trip_root, "metadata" , "review"))
        valid_subdirs = []

        if metadata_path.is_dir():
            for sub in metadata_path.iterdir():
                if sub.is_dir() and (sub / "data_files.csv").is_file():
                    valid_subdirs.append(sub.name)

        self.subdir_dropdown.clear()
        self.subdir_dropdown.addItems(valid_subdirs)

        if self.default_subdir in valid_subdirs:
            self.subdir_dropdown.setCurrentText(self.default_subdir)

    def on_write(self):
        selected_subdir = self.subdir_dropdown.currentText()
        metadata_dir = os.path.join(self.trip_root, "metadata" , "review", selected_subdir)
        data_file = Path(os.path.join(metadata_dir ,  "data_files.csv"))

        if not data_file.is_file():
            self.status_label.setText("❌ Selected subdirectory has not yet been processed by Read Directory.")
            return

        # Update config to point to selected metadata folder
        self.config["project"]["picturedirectory"] = os.path.join(self.base_path, selected_subdir)

        # Run batch module
        batch = BatchModuleRunner(
            self.config,
            self,
            self.logger
        )
        batch.run(
            bin.sv3WritePictures.main,
            "Reading meta data for all pictures ...",
            "✅ Writing Summary data. Review messages above."
        )
        self.status_label.setText("✅ Summary written successfully.")
        self.main_window.status_bar.showMessage("Summary written successfully.")
