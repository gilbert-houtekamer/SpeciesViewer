from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,QCheckBox,
    QTabWidget, QWidget, QVBoxLayout, QComboBox, QLineEdit, QFormLayout, QSizePolicy, QFileDialog, QDateEdit, QTextEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QStyledItemDelegate, QMessageBox)
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtCore import Qt
import logging
import sys, os, shutil
from pathlib import Path
import webbrowser
from gui.batch import BatchModuleRunner  # Assuming batch handler is here

from utils.config import set_trip_settings, _config
from utils.trip_config import read_trip_ini
from widgets.small_widgets import HTMLLabel
from widgets.trip_widgets import (LabeledTextFieldQt, DateRangeFieldQt, DirectoryFieldQt, RecentDirectoryFieldQt, ImageFileFieldQt)
from sharedlib.translatelib import CachedTranslate
from utils.gui_config import update_cache_gui_section, get_cache_gui_section
from gui.trip_overview import populate_trip_overview

def generate_trip_ini(
    trip_root: str,
    trip_name_path: str,
    banner_filename: str,
    picture_filename: str,
    from_date: str,
    to_date: str,
    title_nl: str,
    message_nl: str,
    title_en: str,
    message_en: str,
    logger
):
    import os
    from datetime import datetime

    ini_path = os.path.join(trip_root, "trip.ini")

    master_web = _config.get("master", "masterbiotawebroot", fallback="/nature/")
    master_disk = _config.get("master", "masterbiotadatabase", fallback="E:\\Dropbox\\naturedb")
    trip_name = Path(trip_name_path).name

    ini_text = f"""\
[trip_config]
master_repository_web = {master_web}       
master_repository_disk = {master_disk}     
; the banner size is 2000x270 jpg, and the picture is 600x450 jpg
trip_banner = {banner_filename}             
trip_picture = {picture_filename}        
created = {datetime.now().strftime('%Y-%m-%d %H:%M')}
trip_name = {trip_name}
fromdate = {from_date}
todate = {to_date}
oneblog = day                      

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
    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.menu_spec = menu_spec        
        self.logger = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Create Trip")
        self.resize(800, 600)

        trip_root = _config.get("trip", "tripbiotadatabase", fallback="")
        trip_data = read_trip_ini(trip_root) if os.path.isdir(trip_root) else {}
  
        main_layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # Tab 1: Trip Folder
        tab1 = QWidget()
        layout1 = QVBoxLayout(tab1)
        info_label = HTMLLabel(
            "<p><b>What is a trip?</b><br>"
            "A <b>trip</b> represents a collection of pictures from a vacation, field activity, or any event. "
            "Typically, a trip consists of multiple <b>days</b>, each stored in its own directory. "
            "SpeciesViewer helps validate this data and organizes it into a structured trip database — which is simply a well-organized directory tree.</p>"
            "<p><b>Creating a new trip</b><br>"
            "To begin, enter a folder name (e.g. <i>kretadb</i>) and click <b>Create Folder</b> — this creates the directory and copies placeholder images. "
            "You can later replace the placeholder <i>trip picture</i> (600×450) and <i>banner</i> (2000×270) with real photos.</p>"
            "<p>Once the folder is created, fill in the dates and trip description, then click <b>Create Trip</b> to generate the full directory structure.</p>"
            "<p><b>This tab:</b> Select the root folder for your trip to begin the setup process.</p>"
            # "For more information about birds, visit <a href='https://avibase.bsc-eoc.org/avibase.jsp?lang=EN'>Avibase World Bird Database</a>.</p>"
        )
        layout1.addWidget(info_label)
        self.root_field = RecentDirectoryFieldQt("Trip Root folder", trip_root)
        self.root_field.textChanged(self.on_trip_root_changed)
        layout1.addWidget(self.root_field)

        # New folder creation row — wrapped in QWidget so margins match other field widgets
        new_folder_widget = QWidget()
        new_folder_row = QHBoxLayout(new_folder_widget)
        new_folder_row.setContentsMargins(0, 0, 0, 0)
        new_folder_row.addWidget(QLabel("New trip folder"))
        self.new_folder_name = QLineEdit()
        self.new_folder_name.setPlaceholderText("e.g. kretadb")
        self.new_folder_name.setMaximumWidth(300)
        new_folder_row.addWidget(self.new_folder_name)
        create_folder_btn = QPushButton("Create Folder")
        create_folder_btn.setFixedWidth(110)
        create_folder_btn.clicked.connect(self.create_new_trip_folder)
        new_folder_row.addWidget(create_folder_btn)
        new_folder_row.addStretch()
        layout1.addWidget(new_folder_widget)

        self.trip_web = LabeledTextFieldQt("Trip Web root", _config.get("trip", "tripbiotawebroot", fallback=""), multiline=False, inline=True)
        self.trip_web.field.setMaximumWidth(300)
        trip_web_row = QWidget()
        trip_web_layout = QHBoxLayout(trip_web_row)
        trip_web_layout.setContentsMargins(0, 0, 0, 0)
        trip_web_layout.addWidget(self.trip_web)
        trip_web_layout.addStretch()
        layout1.addWidget(trip_web_row)
        self.project_path = DirectoryFieldQt("Project pictures", _config.get("project", "picturedirectory", fallback=""))
        layout1.addWidget(self.project_path)
        tabs.addTab(tab1, "Select Trip Folder")

        # Tab 2: Images & Dates
        tab2 = QWidget()
        layout2 = QVBoxLayout(tab2)
        info_label21 = HTMLLabel(
            "<p><b>Trip image</b><br>"
            "This image appears in trip listings and should visually represent the trip.<br>"
            "You can update it later if a better photo becomes available.<br>"
            "Required dimensions: <b>600×450 pixels</b>.</p>"
        )

        layout2.addWidget(info_label21)        
        self.trip_img_field = ImageFileFieldQt("Trip Image", (600, 450), self.root_field)
        self.trip_img_field.field.setText(trip_data.get("trip_picture", "")) 
        layout2.addWidget(self.trip_img_field)

        info_label22 = HTMLLabel(
            "<p><b>Web banner</b><br>"
            "This banner is displayed at the top of all blog and species pages for this trip.<br>"
            "It sets the visual tone for the trip’s web presence.<br>"
            "Required dimensions: <b>2000×270 pixels</b>.</p>"
        )

        layout2.addWidget(info_label22)                  
        self.topbar_img_field = ImageFileFieldQt("Top Web Bar", (2000, 270), self.root_field)
        self.topbar_img_field.field.setText(trip_data.get("trip_banner", ""))
        layout2.addWidget(self.topbar_img_field)

        info_label23 = HTMLLabel(
            "<p><b>Date range</b><br>"
            "Defines the expected days of the trip and helps generate accurate lists for the website.<br>"
            "You can adjust this range later to include additional days or refine the trip timeline.</p>"
        )

        layout2.addWidget(info_label23)           
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
        info_label31 = HTMLLabel(
            "<p><b>Trip title and description (Dutch)</b><br>"
            "The trip title is used throughout the interface and on the website. This page contains the Dutch version of the trip description.<br>"
            "You can use the <i>Translate</i> button to generate an English version automatically.<br>"
            "Feel free to override the translation by entering your own text. Note: species names are often mistranslated or not translated at all.</p>"
        )

        dutch_layout.addWidget(info_label31)           
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
        info_label32 = HTMLLabel(
            "<p><b>Trip title and description (English)</b><br>"
            "The trip title appears throughout the interface and on the website. This page contains the English version of the trip description.<br>"
            "You can use the <i>Translate</i> button to generate a Dutch version automatically.<br>"
            "Manual edits are welcome — especially for species names, which are often translated incorrectly or not at all.</p>"
        )

        english_layout.addWidget(info_label32)         
        english_layout.addWidget(self.title_en_field)
        english_layout.addWidget(self.message_en_field)
        sub_tabs.addTab(english_tab, "English Text")

        layout3.addWidget(sub_tabs)
        tabs.addTab(tab3, "Trip Text")

        # Add all tabs to main layout
        main_layout.addWidget(tabs)

        # Status label
        self.status_label = QLabel("")
        main_layout.addWidget(self.status_label)

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        write_tripini_button = QPushButton("Write trip.ini")
        create_button = QPushButton("Create Trip")
        select_button = QPushButton("Select")

        # ✅ Add buttons to layout
        btn_layout.addWidget(write_tripini_button)
        btn_layout.addWidget(create_button)
        btn_layout.addWidget(select_button)

        main_layout.addLayout(btn_layout)

        # Hook up buttons
        write_tripini_button.clicked.connect(self.on_write_tripini_clicked)        
        create_button.clicked.connect(self.on_create_clicked)
        select_button.clicked.connect(self.on_select)

    def closeEvent(self, event):
        self.passed_parent.translator.writeTranslationCache()
        super().closeEvent(event)

    def create_new_trip_folder(self):
        folder_name = self.new_folder_name.text().strip()
        if not folder_name:
            self.status_label.setText("❌ Enter a folder name first.")
            return

        trips_parent = Path(_config.get("trip", "tripbiotadatabase", fallback="")).parent
        if not trips_parent.is_dir():
            self.status_label.setText(f"❌ Cannot find trips parent directory: {trips_parent}")
            return

        new_trip_path = trips_parent / folder_name
        try:
            new_trip_path.mkdir(exist_ok=True)
        except Exception as e:
            self.status_label.setText(f"❌ Could not create folder: {e}")
            return

        # Copy default images from dist/includes web/
        dist_dir = Path(__file__).resolve().parent.parent.parent / "dist" / "includes web"
        for src_name, dest_name in [("tripbanner.jpg", "tripbanner.jpg"), ("tripimage.jpg", "tripimage.jpg")]:
            src = dist_dir / src_name
            dest = new_trip_path / dest_name
            if src.exists() and not dest.exists():
                shutil.copy(src, dest)

        # Update the trip root field and pre-fill image fields
        self.root_field.set_text(str(new_trip_path))
        self.topbar_img_field.field.setText("tripbanner.jpg")
        self.trip_img_field.field.setText("tripimage.jpg")
        self.trip_web.set_text(f"/trips/{folder_name}")
        self.status_label.setText(f"✅ Created: {new_trip_path}")
        self.logger.info(f"Created new trip folder: {new_trip_path}")

    def on_trip_root_changed(self, new_path):
        trip_data = read_trip_ini(new_path)
        if trip_data:
            print("✅ trip.ini loaded:", new_path)
            # You can now update fields like:
            trip_name = trip_data['trip_name']
            if "trips" not in trip_name: trip_name = "/trips/" + trip_name 
            self.trip_web.set_text(trip_name)
            self.trip_img_field.field.setText(trip_data['trip_picture'])
            self.topbar_img_field.field.setText(trip_data['trip_banner'])
            self.date_field.set_dates(trip_data["fromdate"],trip_data["todate"])
            self.title_nl_field.field.setText(trip_data['title_nl'])
            self.message_nl_field.field.setText(trip_data['message_nl'])
            self.title_en_field.field.setText(trip_data['title_en'])
            self.message_en_field.field.setText(trip_data['message_en'])
            # do we have the corresponding projects
            pictdir = get_cache_gui_section("trip_project_map", trip_name)
            if pictdir != "":
                _config["project"]["picturedirectory"] = pictdir
                self.project_path.field.setText(pictdir)
        else:
            print("⚠️ No trip.ini found at", new_path, ", be sure to complete all tabs.")

    def on_write_tripini_clicked(self):
        self.create_trip_ini()        
        self.logger.info("Trip.ini created or updated successfully!")        
        self.passed_parent.status_bar.showMessage("Trip.ini created or updated successfully!")

    def on_create_clicked(self):
        self.create_trip_ini()
        # make sure new structure is created
        set_trip_settings(self.root_field.get_text(), self.trip_web.field.text(), self.project_path.field.text())
        # Also save trip/project pair
        update_cache_gui_section("trip_project_map", self.trip_web.field.text(), self.project_path.field.text() )   
        # now do the actual work     
        runner = BatchModuleRunner(self, self.passed_parent)
        errorstr = runner.run(self.menu_spec)             

    def on_select(self):
        # save settings
        set_trip_settings(self.root_field.get_text(), self.trip_web.field.text(), self.project_path.field.text())
        update_cache_gui_section("trip_project_map", self.trip_web.field.text(), self.project_path.field.text() )   
        populate_trip_overview(self.passed_parent)        
        self.accept()  # ✅ Actually closes the dialog

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
        trip_root = self.root_field.get_text()
        trip_name = self.trip_web.get_text()
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
            trip_name,
            banner_filename,
            picture_filename,
            from_date,
            to_date,
            title_nl,
            message_nl,
            title_en,
            message_en,
            self.logger
    )

class ReadDirDialog(QDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Read Picture Directory")
        self.resize(600, 300)

        self.logger = logging.getLogger("speciesviewer")
        self.menu_spec = menu_spec               
        self.passed_parent = parent

        full_path = Path(_config["project"]["picturedirectory"])
        self.base_path = full_path.parent
        self.subdir_name = full_path.name

        layout = QVBoxLayout(self)

        # Info text
        info_html = HTMLLabel(
            "<p><b>Scan a folder with pictures</b><br>"
            "This first step scans a directory containing photo files and extracts metadata (Exif tags) for each image.<br>"
            "It uses <b>ExifTool</b> to process raw files, HTML metadata, and auxiliary data. "
            "Errors may occur if files contain unknown characters or unsupported formats, which can cause ExifTool to crash.<br>"
            "If the trip is already defined or selected in the GUI, you typically do <b>not</b> need to change the project folder or subdirectory."
            "</p>"
        )
        layout.addWidget(info_html)        

        # Base directory field
        self.base_dir_field = DirectoryFieldQt("Project root folder", str(self.base_path))
        layout.addWidget(self.base_dir_field)

        # Subdirectory dropdown
        self.subdir_dropdown = QComboBox()
        self.subdir_dropdown.setEditable(False)
        layout.addWidget(QLabel("Select subdirectory"))
        layout.addWidget(self.subdir_dropdown)

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

        _config["project"]["picturedirectory"] = str(full_selected_path)
        self.logger.info(f"📁 Selected picture directory: {full_selected_path}")

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)

class WritePicturesDialog(QDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Write Summary Data")
        self.resize(600, 400)

        self.logger = logging.getLogger("speciesviewer")
        self.passed_parent = parent    
        self.menu_spec = menu_spec                   

        # Extract trip root and default subdir
        full_path = Path(_config["project"]["picturedirectory"])
        self.base_path = full_path.parent
        self.default_subdir = full_path.name   # e.g. 2017-08-09
        self.trip_root = _config["trip"]["tripbiotadatabase"] # E:\Dropbox\trips\2017 oostzeedb

        layout = QVBoxLayout(self)

        # Info text
        info_html = HTMLLabel(
            "<p><b>Write summary data for a project day</b></p>"
            "<p>This step analyzes metadata extracted during the 'Read Directory' phase and enriches it with species information.<br>"
            "It checks whether each species name is found in the internal species table, which includes almost all known birds.<br>"
            "If a species is not found, it may be a new discovery or a mistyped name. "
            "Unknown species are automatically searched in the <b>GBIF</b> online database. If found, they are added to the internal table. "
            "If not, a warning is generated.</p>"
            "<p>You can correct species names in tools like <b>Lightroom</b> or <b>DxO PhotoLab</b>. "
            "After corrections, both the 'Read Directory' and 'Write Summary' steps should be re-run.<br>"
            "Alternatively, use the Trip Overview GUI: double-click the 'Total Files' column to edit directly.</p>"
            "<p>Normally, the project folder and subdirectory do not need to be changed.</p>"
        )

        layout.addWidget(info_html)  
        # Show root folder (non-editable)
        
        self.root_label = QLineEdit(str(self.base_path))
        self.root_label.setReadOnly(True)
        layout.addWidget(self.root_label)

        # Subdirectory dropdown
        layout.addWidget(QLabel("Select subdirectory with metadata"))
        self.subdir_dropdown = QComboBox()
        self.subdir_dropdown.setEditable(False)
        layout.addWidget(self.subdir_dropdown)

        # Status label
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Buttons layout
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.species_btn = QPushButton("Edit/Add Species Names")
        self.write_btn = QPushButton("Write Summary")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.species_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.write_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        # Signals
        self.species_btn.clicked.connect(self.on_species_collection)
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

    def on_species_collection(self):
        csv_path = os.path.join(self.trip_root, "metadata", "active", "speciescollection.csv")
        dlg = SpeciesCollectionDialog(self, csv_path)
        dlg.exec()

    def on_write(self):
        selected_subdir = self.subdir_dropdown.currentText()
        # Update config to point to selected metadata folder
        _config["project"]["picturedirectory"] = os.path.join(self.base_path, selected_subdir)

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)


class LowerCaseDelegate(QStyledItemDelegate):
    """Forces text to lowercase on commit."""
    def setModelData(self, editor, model, index):
        if isinstance(editor, QLineEdit):
            model.setData(index, editor.text().lower(), Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)


class SpeciesCollectionDialog(QDialog):
    # CSV key -> display header; order defines column order
    COLUMN_MAP = [
        ("Level",          "Level"),
        ("ScientificName", "Scientific Name"),
        ("EnglishName",    "English Name"),
        ("DutchName",      "Dutch Name"),
    ]
    EDITABLE_KEYS = {"DutchName", "EnglishName"}
    COPY_COL = 1   # ScientificName column index

    def __init__(self, parent, csv_path):
        super().__init__(parent)
        self.setWindowTitle("Provide English and/or Dutch species names")
        self.resize(750, 500)
        self.csv_path = csv_path
        self.all_rows = []
        self.dirty = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Last 50 entries from: {csv_path}"))

        # Columns: Level, ScientificName, [copy btn], EnglishName, DutchName
        self.COPY_VISUAL_COL = 2  # copy button inserted after ScientificName
        col_count = len(self.COLUMN_MAP) + 1
        self.table = QTableWidget()
        self.table.setColumnCount(col_count)
        headers = [h for _, h in self.COLUMN_MAP]
        headers.insert(self.COPY_VISUAL_COL, "")
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(self.COPY_VISUAL_COL, 30)
        layout.addWidget(self.table)

        # Lowercase delegate for editable name columns
        self._lc_delegate = LowerCaseDelegate(self.table)
        for j, (key, _) in enumerate(self.COLUMN_MAP):
            visual = j if j < self.COPY_VISUAL_COL else j + 1
            if key in self.EDITABLE_KEYS:
                self.table.setItemDelegateForColumn(visual, self._lc_delegate)

        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.on_save)
        btn_layout.addWidget(self.save_btn)

        self.lookup_btn = QPushButton("Lookup NBA")
        self.lookup_btn.setToolTip("Fill missing English/Dutch names from the Netherlands Biodiversity API")
        self.lookup_btn.clicked.connect(self.on_lookup_nba)
        btn_layout.addWidget(self.lookup_btn)

        btn_layout.addStretch()

        self.status_label = QLabel("")
        btn_layout.addWidget(self.status_label)
        btn_layout.addStretch()

        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(self.close)
        btn_layout.addWidget(quit_btn)
        layout.addLayout(btn_layout)

        self._load_csv(csv_path)
        self.table.cellChanged.connect(self._on_cell_changed)

    @staticmethod
    def _format_level(raw):
        """Convert 'Level.SPECIES' to 'Species'."""
        if raw.startswith("Level."):
            raw = raw[len("Level."):]
        return raw.capitalize()

    def _copy_scientific_name(self, row):
        # ScientificName is visual column 1
        item = self.table.item(row, 1)
        if item:
            QGuiApplication.clipboard().setText(item.text())

    def on_lookup_nba(self):
        """Look up missing English/Dutch names via the NBA API."""
        from ghexif.nabortalib import NBASpecies
        from PyQt6.QtCore import QThread, pyqtSignal

        # Find rows with missing names among displayed rows
        rows_to_lookup = []
        for i, csv_idx in enumerate(self.display_indices):
            csv_row = self.all_rows[csv_idx]
            eng = csv_row.get("EnglishName", "").strip()
            nld = csv_row.get("DutchName", "").strip()
            sci = csv_row.get("ScientificName", "").strip()
            if sci and (not eng or not nld):
                rows_to_lookup.append((i, csv_idx, sci, eng, nld))

        if not rows_to_lookup:
            self.status_label.setText("No missing names to look up.")
            return

        self.lookup_btn.setEnabled(False)
        self.status_label.setText(f"Looking up {len(rows_to_lookup)} species...")

        class NBAWorker(QThread):
            result_ready = pyqtSignal(int, int, str, str)  # display_row, csv_idx, eng, nld
            finished_all = pyqtSignal(int, int)             # looked_up, filled

            def __init__(self, rows):
                super().__init__()
                self.rows = rows

            def run(self):
                nba = NBASpecies()
                filled = 0
                for display_row, csv_idx, sci, existing_eng, existing_nld in self.rows:
                    eng, nld = nba.get_vernacular_names(sci)
                    new_eng = eng if (eng and not existing_eng) else existing_eng
                    new_nld = nld if (nld and not existing_nld) else existing_nld
                    if new_eng != existing_eng or new_nld != existing_nld:
                        filled += 1
                    self.result_ready.emit(display_row, csv_idx, new_eng, new_nld)
                self.finished_all.emit(len(self.rows), filled)

        self._nba_worker = NBAWorker(rows_to_lookup)
        self._nba_worker.result_ready.connect(self._on_nba_result)
        self._nba_worker.finished_all.connect(self._on_nba_finished)
        self._nba_worker.start()

    def _on_nba_result(self, display_row, csv_idx, eng, nld):
        csv_row = self.all_rows[csv_idx]
        changed = False
        if eng and not csv_row.get("EnglishName", "").strip():
            csv_row["EnglishName"] = eng
            changed = True
        if nld and not csv_row.get("DutchName", "").strip():
            csv_row["DutchName"] = nld
            changed = True
        if changed:
            self.dirty = True
            self.save_btn.setEnabled(True)
            # Update the visible table cells
            self.table.blockSignals(True)
            for j, (key, _) in enumerate(self.COLUMN_MAP):
                if key == "EnglishName":
                    self.table.item(display_row, self._visual_col(j)).setText(eng)
                elif key == "DutchName":
                    self.table.item(display_row, self._visual_col(j)).setText(nld)
            self.table.blockSignals(False)

    def _on_nba_finished(self, looked_up, filled):
        self.lookup_btn.setEnabled(True)
        self.status_label.setText(f"Netherlands Biodiversity API (NBA): {filled} of {looked_up} species updated.")

    def _visual_col(self, data_col):
        """Map data column index to visual column, accounting for copy button."""
        return data_col if data_col < self.COPY_VISUAL_COL else data_col + 1

    def _load_csv(self, csv_path):
        if not os.path.isfile(csv_path):
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem(f"File not found: {csv_path}"))
            return

        import csv
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            self.all_rows = list(reader)

        display_rows = self.all_rows[-50:]
        # indices into all_rows for the displayed slice (reversed for display)
        self.display_indices = list(range(len(self.all_rows) - len(display_rows), len(self.all_rows)))
        self.display_indices.reverse()
        self.table.setRowCount(len(self.display_indices))

        # Set vertical headers to actual CSV line numbers (1-based, +1 for header row)
        v_headers = [str(idx + 2) for idx in self.display_indices]
        self.table.setVerticalHeaderLabels(v_headers)

        for i, csv_idx in enumerate(self.display_indices):
            row = self.all_rows[csv_idx]
            for j, (key, _) in enumerate(self.COLUMN_MAP):
                value = row.get(key, "")
                if key == "Level":
                    value = self._format_level(value)
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                if key not in self.EDITABLE_KEYS:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, self._visual_col(j), item)

            # Copy button right after Scientific Name
            copy_btn = QPushButton("\u2398")  # ⎘ copy icon
            copy_btn.setFixedSize(24, 24)
            copy_btn.setToolTip("Copy scientific name")
            copy_btn.clicked.connect(lambda checked, r=i: self._copy_scientific_name(r))
            self.table.setCellWidget(i, self.COPY_VISUAL_COL, copy_btn)

        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(self.COPY_VISUAL_COL, 30)

    def _on_cell_changed(self, row, col):
        if col == self.COPY_VISUAL_COL:
            return
        data_col = col if col < self.COPY_VISUAL_COL else col - 1
        if data_col >= len(self.COLUMN_MAP):
            return
        key = self.COLUMN_MAP[data_col][0]
        if key not in self.EDITABLE_KEYS:
            return
        new_value = self.table.item(row, col).text()
        csv_index = self.display_indices[row]
        csv_row = self.all_rows[csv_index]
        if csv_row.get(key, "") != new_value:
            csv_row[key] = new_value
            self.dirty = True
            self.save_btn.setEnabled(True)

    def on_save(self):
        import csv
        headers = ['Level', 'ScientificName', 'DutchName', 'EnglishName', 'Up', 'Leaves']
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers, quoting=csv.QUOTE_MINIMAL, delimiter=";")
            writer.writeheader()
            writer.writerows(self.all_rows)
        self.dirty = False
        self.save_btn.setEnabled(False)

    def closeEvent(self, event):
        if self.dirty:
            reply = QMessageBox.warning(
                self, "Unsaved changes",
                "You have unsaved changes. Do you want to save before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel)
            if reply == QMessageBox.StandardButton.Save:
                self.on_save()
                event.accept()
            elif reply == QMessageBox.StandardButton.Discard:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget, QWidget, QLabel, QLineEdit,
    QTextEdit, QFormLayout, QPushButton, QHBoxLayout, QSpinBox
)
from configparser import ConfigParser
from pathlib import Path

class CreateBlogDialog(QDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Configure Blog Post")
        self.resize(600, 400)

        self.menu_spec = menu_spec
        self.ini_path = Path(_config["project"]["picturedirectory"]) / "blog.ini"
        self.parser = ConfigParser()
        self.passed_parent = parent 
        self.logger = parent.logger

        self.title_nl_field = ""
        self.message_nl_field  = ""
        self.title_en_field  = ""
        self.message_en_field   = ""      

        # Load or create default ini
        if self.ini_path.exists():
            self.parser.read(self.ini_path)
        else:
            self._create_default_ini()
            with open(self.ini_path, "w") as f:
                self.parser.write(f)

        # Main layout
        layout = QVBoxLayout(self)

        # Tabs
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self._create_dutch_tab()
        self._create_english_tab()
        self._create_config_tab()
        #
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        # checkbox
        self.open_after_checkbox = QCheckBox("Open blog in browser after creation")
        self.open_after_checkbox.setChecked(True)  # Default to checked
        layout.addWidget(self.open_after_checkbox)
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("Save blog.ini")
        create_btn = QPushButton("Create Blog")
        close_btn = QPushButton("Close")

        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(create_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        save_btn.clicked.connect(self.save_ini)
        create_btn.clicked.connect(self.on_create_blog)
        close_btn.clicked.connect(self.close)

    def get_blog_url(self):
        base_url = _config.get("websettings", "base_url", fallback="https://houtekamer.com/")
        trip_name = Path(_config.get("trip", "tripbiotawebroot", fallback="")).name
        return f"{base_url}trips/{trip_name}/biotablog/blog%20index-nl.html"


    def _create_default_ini(self):
        self.parser["blog_config"] = {
            "only_new": "False",
            "minimum_score": "25",
            "column_width": "300"
        }
        self.parser["blog_selection"] = {
            "species": "biota"
        }
        self.parser["blog_text"] = {
            "titel_nl": "Dag 2: Nederlandse Titel",
            "message_nl": "Nederlandse omschrijving van de post",
            "titel_en": "Day 2: English title",
            "message_en": "English description for the post"
        }

    def translate_dutch_to_english(self):
        try:
            nl_title = self.title_nl.text().strip()
            nl_message = self.message_nl.toPlainText().strip()

            translated_title = self.passed_parent.translator.getOneLanguage(nl_title, languagein='en')
            translated_message = self.passed_parent.translator.getOneLanguage(nl_message, languagein='en')

            self.title_en.setText(translated_title)
            self.message_en.setPlainText(translated_message)

            self.logger.info("✅ Translated Dutch to English using Google Translate.")
        except Exception as e:
            self.logger.error(f"❌ Translation failed: {e}")

    def translate_english_to_dutch(self):
        try:
            en_title = self.title_en.text().strip()
            en_message = self.message_en.toPlainText().strip()

            translated_title = self.passed_parent.translator.getOneLanguage(en_title, languagein='nl')
            translated_message = self.passed_parent.translator.getOneLanguage(en_message, languagein='nl')

            self.title_nl.setText(translated_title)
            self.message_nl.setPlainText(translated_message)

            self.logger.info("✅ Translated English to Dutch using Google Translate.")
        except Exception as e:
            self.logger.error(f"❌ Translation failed: {e}")


    def _create_dutch_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)
    
        self.translate_button = QPushButton("Translate to English")  # "Translate to English"
        self.translate_button.clicked.connect(self.translate_dutch_to_english)
        self.info_html = HTMLLabel(
            "<p><b>Text for blog entry</b><br>"
            "A blog entry is a presentation of the different species seen on a day on a web page. " 
            "The text entered here is shown on the Dutch version of web site.<br>"
            "The text can be translated to English, and verified on the English tab.  Especially species names and titles need to reviewed.<br> "
            "Once entered for a blog, it is saved in the picture directory and uses on later usage.<br>"
            "</p>"
        )
        self.title_nl = QLineEdit(self.parser.get("blog_text", "titel_nl", fallback=""))
        self.message_nl = QTextEdit(self.parser.get("blog_text", "message_nl", fallback=""))

        form.addRow("", self.translate_button)
        form.addRow("", self.info_html)
        form.addRow("Titel (NL):", self.title_nl)
        form.addRow("Beschrijving (NL):", self.message_nl)


        self.tabs.addTab(tab, "Dutch Text")

    def _create_english_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)

        self.translate_button = QPushButton("Translate to Dutch")  # "Translate to Dutch"
        self.translate_button.clicked.connect(self.translate_dutch_to_english)        
        self.title_en = QLineEdit(self.parser.get("blog_text", "titel_en", fallback=""))
        self.message_en = QTextEdit(self.parser.get("blog_text", "message_en", fallback=""))

        form.addRow("", self.translate_button)
        form.addRow("Title (EN):", self.title_en)
        form.addRow("Description (EN):", self.message_en)

        self.tabs.addTab(tab, "English Text")

    def _create_config_tab(self):
        tab = QWidget()
        form = QFormLayout(tab)

        self.minimum_score = QSpinBox()
        self.minimum_score.setRange(1, 200)
        self.minimum_score.setValue(self.parser.getint("blog_config", "minimum_score", fallback=25))

        self.column_width = QSpinBox()
        self.column_width.setRange(200, 500)
        self.column_width.setValue(self.parser.getint("blog_config", "column_width", fallback=300))

        form.addRow("Minimum picture rating (1–200):", self.minimum_score)
        form.addRow("Minimum picture width (px):", self.column_width)

        self.tabs.addTab(tab, "Configuration")

    def save_ini(self):
        self.parser["blog_text"]["titel_nl"] = self.title_nl.text()
        self.parser["blog_text"]["message_nl"] = self.message_nl.toPlainText()
        self.parser["blog_text"]["titel_en"] = self.title_en.text()
        self.parser["blog_text"]["message_en"] = self.message_en.toPlainText()

        self.parser["blog_config"]["minimum_score"] = str(self.minimum_score.value())
        self.parser["blog_config"]["column_width"] = str(self.column_width.value())

        with open(self.ini_path, "w") as f:
            self.parser.write(f)

        self.logger.info("✅ blog.ini written successfully.")
        self.passed_parent.status_bar.showMessage("blog.ini written successfully.")

    def on_create_blog(self):
        try:
            self.save_ini()  # Ensure latest edits are saved

            runner = BatchModuleRunner(self, self.passed_parent)
            errorstring = runner.run(self.menu_spec)

            if errorstring == "" and self.open_after_checkbox.isChecked():
                webbrowser.open(self.get_blog_url())
        except Exception as e:
            self.logger.error(f"❌ Blog creation failed: {e}")
            self.status_label.setText("❌ Blog creation failed.")
            self.passed_parent.status_bar.showMessage("Blog creation failed.")        


class BaseTripModuleDialog(QDialog):
    def __init__(self, parent, menu_spec, title, info_text, subdirectory=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 250)

        self.logger = logging.getLogger("speciesviewer")
        self.passed_parent = parent    
        self.menu_spec = menu_spec      
        self.subdirectory = subdirectory             

        full_path = Path(_config["project"]["picturedirectory"])
        self.base_path = full_path.parent
        self.default_subdir = full_path.name
        self.trip_root = _config["trip"]["tripbiotadatabase"]

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Trip root folder"))
        self.root_label = QLineEdit(str(self.base_path))
        self.root_label.setReadOnly(True)
        layout.addWidget(self.root_label)

        if subdirectory:
            layout.addWidget(QLabel("Select subdirectory with metadata"))
            self.subdir_dropdown = QComboBox()
            self.subdir_dropdown.setEditable(False)
            layout.addWidget(self.subdir_dropdown)

        self.info_text = QLabel(info_text)
        self.info_text.setWordWrap(True)
        layout.addWidget(self.info_text)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.write_btn = QPushButton("Run Step")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.write_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.write_btn.clicked.connect(self.on_write)
        self.close_btn.clicked.connect(self.close)

        if self.subdirectory:
            self.update_subdirs()

    def update_subdirs(self):
        metadata_path = Path(self.trip_root) / "metadata" / "review"
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
        if self.subdirectory:        
            selected_subdir = self.subdir_dropdown.currentText()
            _config["project"]["picturedirectory"] = os.path.join(self.base_path, selected_subdir)

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)

class BuildWebsiteDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Build Website",
            info_text="Prepare all web files and site structure for publication."
        )
        # self.dialog.exec()


class FreezeDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Freeze Data",
            info_text="Finalize and lock down all collected information for archival or publishing."
        )
        # self.dialog.exec()


class WriteCommitDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Write Website Commit",
            info_text="Finalize and lock down all collected information for archival or publishing."
        )
        # self.dialog.exec()

class CountSpeciesDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Count Species",
            info_text="Count the different number of species picture in the trip.",
            subdirectory=False
        )

        self.resize(1100, 750)
        # Report viewer setup
        self.report_viewer = QTextEdit()
        self.report_viewer.setReadOnly(True)
        font = QFont("Courier New")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.report_viewer.setFont(font)
        self.report_viewer.setMinimumHeight(500)
        self.report_viewer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Add widgets to layout
        self.layout().addWidget(QLabel("Species Overview Report"))
        self.layout().addWidget(self.report_viewer)

        # Add buttons
        button_layout = QHBoxLayout()
        save_button = QPushButton("Save As")
        copy_button = QPushButton("Copy to Clipboard")
        save_button.clicked.connect(self.save_report)
        copy_button.clicked.connect(self.copy_to_clipboard)
        button_layout.addWidget(save_button)
        button_layout.addWidget(copy_button)
        self.layout().addLayout(button_layout)

    def on_write(self):
        if self.subdirectory:
            selected_subdir = self.subdir_dropdown.currentText()
            _config["project"]["picturedirectory"] = os.path.join(self.base_path, selected_subdir)

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)

        try:
            reports_dir = Path(_config.get("trip", "tripbiotadatabase")) / "metadata" / "reports"
            report_path = reports_dir / "Species overview.txt"
            if report_path.exists():
                with open(report_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    self.report_viewer.setPlainText(content)
            else:
                self.report_viewer.setPlainText("⚠️ Report file not found.")
        except Exception as e:
            self.report_viewer.setPlainText(f"❌ Error loading report: {e}")

    def save_report(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Report As", "", "Text Files (*.txt);;All Files (*)"
        )
        if filename:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(self.report_viewer.toPlainText())

    def copy_to_clipboard(self):
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.report_viewer.toPlainText())

class RedoTripBlogsDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Recreate Blogs for trip",
            info_text="Recreate Blogs for trip for all committed days.",
            subdirectory=False            
        )

class RedoTripRepoDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Recreate Repository for trip",
            info_text="Recreate Repository for trip for all days.",
            subdirectory=False            
        )

class MergeTripDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Merge Trip Species with master",
            info_text="List and merge species lists from master and trip.",
            subdirectory=False
        )        

class ConvertAvibaseDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Create an Excel to keep track of birds seen.",
            info_text="Create an Excel to keep track of birds seen.",
            subdirectory=False
        ) 