from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QWidget, QVBoxLayout, QLineEdit, QFormLayout, QGridLayout, QDateEdit, QTextEdit)
from PyQt6.QtCore import Qt
import logging
import sys, os
from gui.batch import call_batch_module  # Assuming batch handler is here
import bin.sv1MakeReposity
import bin.sv2ReadDirExif
import bin.sv3WritePictures
import bin.sv4WriteBlog
import bin.sv5BuildWebsite
import bin.sv6Freeze
import bin.sv7WriteCommit
from utils.trip_config import read_trip_ini
from widgets.trip_widgets import (LabeledTextFieldQt, DateRangeFieldQt, DirectoryFieldQt, ImageFileFieldQt)
    # create_directory_field,
    # create_image_file_field)

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
        # print(f"📋 Logger passed to TripCreationDialog: {parent.logger}")    
        # print(f"📋 Logger in TripCreationDialogspec:    {self.logger}")        
        # self.logger.info(f"TripCreationDialog {self.logger}")

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

        # layout, self.from_date_edit, self.to_date_edit = create_date_inputs(trip_data)
        # layout2.addLayout(layout)
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

        # title_nl = create_labeled_text("Trip title (Dutch)", trip_data.get("title_nl", ""))
        # message_nl = create_labeled_text("Trip description (Dutch)", trip_data.get("message_nl", ""), multiline=True)
        # self.title_nl_field = title_nl["field"]
        # self.message_nl_field = message_nl["field"]

        dutch_layout.addWidget(self.title_nl_field)
        dutch_layout.addWidget(self.message_nl_field)        
        sub_tabs.addTab(dutch_tab, "Dutch Text")

        # Sub-tab: English
        english_tab = QWidget()
        english_layout = QVBoxLayout(english_tab)
        self.title_en_field = LabeledTextFieldQt("Trip title (English)", trip_data.get("title_en", ""))
        self.message_en_field = LabeledTextFieldQt("Trip description (English)", trip_data.get("message_en", ""), multiline=True)

        # title_en = create_labeled_text("Trip title (English)", trip_data.get("title_en", ""))
        # message_en = create_labeled_text("Trip description (English)", trip_data.get("message_en", ""), multiline=True)
        # self.title_en_field = title_en["field"]
        # self.message_en_field = message_en["field"]

        english_layout.addWidget(self.title_en_field)
        english_layout.addWidget(self.message_en_field)
        sub_tabs.addTab(english_tab, "English Text")

        # Sub-tab: Create
        create_tab = QWidget()
        create_layout = QVBoxLayout(create_tab)
        create_layout.addWidget(QPushButton("Generate trip.ini"))
        create_layout.addWidget(QPushButton("Create Trip Directories"))
        sub_tabs.addTab(create_tab, "Create Trip")

        layout3.addWidget(sub_tabs)
        tabs.addTab(tab3, "Trip Text && Actions")

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

    def on_write_tripini_clicked(self):
        self.create_trip_ini()        
        self.logger.info("Trip.ini created or updated successfully!")        

    def on_create_clicked(self):
        self.create_trip_ini()
        call_batch_module(
            self.config,
            self.passed_parent,
            self.logger,
            bin.sv1MakeReposity.main,
            "Creating trip data structures...",
            "✅ Trip creation complete. Please review the log messages."
        )
        self.logger.info("Trip created successfully!")

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

def show_read_dir_dialog(parent, config):
    print("✅ trip_read_dir triggered")
    logger = logging.getLogger("speciesviewer")

    dialog = QDialog(parent)
    dialog.setWindowTitle("Read Picture Directory")
    dialog.resize(300, 200)

    layout = QVBoxLayout(dialog)

    info_text = QLabel("Scan a directory with photo files and extract metadata (Exif tags) for each image.")
    info_text.setWordWrap(True)
    layout.addWidget(info_text)

    read_btn = QPushButton("Read Directory")
    layout.addWidget(read_btn, alignment=Qt.AlignmentFlag.AlignLeft)

    def on_read():
        call_batch_module(
            config,
            parent,
            logger,
            bin.sv2ReadDirExif.main,
            "Reading picture directory...",
            "✅ Directory scan complete. Review messages above."
        )
        dialog.accept()

    read_btn.clicked.connect(on_read)

    return dialog