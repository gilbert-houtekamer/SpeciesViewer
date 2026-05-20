from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,QCheckBox,
    QTabWidget, QWidget, QVBoxLayout, QComboBox, QLineEdit, QFormLayout, QSizePolicy, QFileDialog, QDateEdit, QTextEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QStyledItemDelegate, QMessageBox)
from PyQt6.QtGui import QFont, QGuiApplication, QColor
from PyQt6.QtCore import Qt
import logging
import sys, os, shutil
from datetime import datetime
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

    # Configparser only treats subsequent lines as part of a multi-line
    # value when they are indented. Without this escape, paragraph breaks
    # the user types into a description produce a file that re-reads as
    # a ParsingError (orphan unindented line after a blank line).
    def _ini_escape(text):
        if "\n" not in text:
            return text
        head, *rest = text.split("\n")
        return head + "\n" + "\n".join(("    " + l) if l.strip() else l
                                        for l in rest)

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
title_nl = {_ini_escape(title_nl)}
message_nl = {_ini_escape(message_nl)}
title_en = {_ini_escape(title_en)}
message_en = {_ini_escape(message_en)}
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
        trip_data = read_trip_ini(trip_root) or {}
  
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
        runner.run(self.menu_spec, on_success=lambda: populate_trip_overview(self.passed_parent))

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
    """Forces text to lowercase on commit; paints cell background from a color map."""

    def __init__(self, parent, cell_colors):
        super().__init__(parent)
        self.cell_colors = cell_colors          # dict[(row, col)] -> QColor

    def setModelData(self, editor, model, index):
        if isinstance(editor, QLineEdit):
            model.setData(index, editor.text().lower(), Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)

    def paint(self, painter, option, index):
        color = self.cell_colors.get((index.row(), index.column()))
        if color:
            painter.fillRect(option.rect, color)
        super().paint(painter, option, index)


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
    COLOR_MANUAL     = QColor(204, 229, 255)   # light blue   — user-edited
    COLOR_NBA        = QColor(204, 255, 204)   # light green  — NBA-filled
    COLOR_NEW        = QColor(255, 242, 204)   # light yellow — newly merged
    COLOR_BACKFILLED = QColor(252, 228, 214)   # pale orange  — name backfilled

    def __init__(self, parent, csv_path,
                 highlight_rows_from=None,
                 highlight_changed_from_path=None,
                 title=None):
        super().__init__(parent)
        self.setWindowTitle(title or "Provide English and/or Dutch species names")
        self.resize(750, 500)
        self.csv_path = csv_path
        # When set, every all_rows row whose csv index is >= this threshold
        # is painted yellow — used by the trip→master merge review flow to
        # call out newly-merged species (always appended to the file end).
        self.highlight_rows_from = highlight_rows_from
        # When set, rows whose DutchName/EnglishName differ from the
        # snapshot at this path are painted orange — the merge-review
        # flow uses this to call out existing master rows whose blank
        # vernacular names were just backfilled from the trip.
        self.highlight_changed_from_path = highlight_changed_from_path
        self._backfilled_csv_idxs = set()
        # Review mode is auto-detected: when a pre-merge snapshot path
        # was supplied, the dialog is wrapping a merge review and
        # exposes explicit Keep/Restore buttons. The orchestrator reads
        # `self.verdict` after exec() — None means the user closed via
        # X without picking, and the orchestrator falls back to a small
        # confirm prompt.
        self.review_mode = highlight_changed_from_path is not None
        self.verdict = None
        self.all_rows = []
        self.filtered_indices = []  # csv indices to page through; equals all rows when no filter
        self.filter_text = ""
        self.FILTER_MIN_CHARS = 3
        self.dirty = False
        self.cell_colors = {}   # (row, visual_col) -> QColor
        self.PAGE_SIZE = 50

        layout = QVBoxLayout(self)

        # Navigation bar
        nav_layout = QHBoxLayout()
        self.prev_btn = QPushButton("<")
        self.prev_btn.setFixedWidth(30)
        self.prev_btn.clicked.connect(self.on_prev_page)
        nav_layout.addWidget(self.prev_btn)
        self.range_label = QLabel("")
        self.range_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav_layout.addWidget(self.range_label)
        self.next_btn = QPushButton(">")
        self.next_btn.setFixedWidth(30)
        self.next_btn.clicked.connect(self.on_next_page)
        nav_layout.addWidget(self.next_btn)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(
            f"Filter: scientific name starts with… (≥{self.FILTER_MIN_CHARS} chars)")
        self.filter_edit.setFixedWidth(280)
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        nav_layout.addWidget(self.filter_edit)
        layout.addLayout(nav_layout)

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

        # Lowercase delegate handles both per-cell colour painting and
        # forcing lowercase on edit-commit. Attach it to every data column
        # (not just editable ones) so cell_colors paints uniformly across
        # the whole row — needed for the merge-review yellow highlight.
        # The lowercase effect is harmless on non-editable columns since
        # their items have ItemIsEditable cleared.
        self._lc_delegate = LowerCaseDelegate(self.table, self.cell_colors)
        for vcol in range(self.table.columnCount()):
            if vcol == self.COPY_VISUAL_COL:
                continue  # copy-button widget column
            self.table.setItemDelegateForColumn(vcol, self._lc_delegate)

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

        if self.review_mode:
            # Explicit terminal actions for the merge-review flow. Keep
            # is the default so Enter accepts the merge.
            restore_btn = QPushButton("Restore previous master")
            restore_btn.setToolTip("Discard the merge — and any name edits made during this review.")
            restore_btn.clicked.connect(lambda: self._end_review("restore"))
            btn_layout.addWidget(restore_btn)
            keep_btn = QPushButton("Keep merged master")
            keep_btn.setToolTip("Accept the merged master and discard the .csvold backup.")
            keep_btn.setDefault(True)
            keep_btn.clicked.connect(lambda: self._end_review("keep"))
            btn_layout.addWidget(keep_btn)
        else:
            quit_btn = QPushButton("Quit")
            quit_btn.clicked.connect(self.close)
            btn_layout.addWidget(quit_btn)
        layout.addLayout(btn_layout)

        self._load_csv(csv_path)
        self.table.cellChanged.connect(self._on_cell_changed)

    def _end_review(self, verdict):
        """Set the merge-review verdict and close the dialog. Pending
        edits are flushed first so a Keep doesn't lose them; a Restore
        will be undone by the orchestrator's file swap regardless."""
        if self.dirty:
            self.on_save()
        self.verdict = verdict
        self.accept()

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
        fill_eng = eng and not csv_row.get("EnglishName", "").strip()
        fill_nld = nld and not csv_row.get("DutchName", "").strip()
        if not fill_eng and not fill_nld:
            return
        self.table.blockSignals(True)
        if fill_eng:
            csv_row["EnglishName"] = eng
            for j, (key, _) in enumerate(self.COLUMN_MAP):
                if key == "EnglishName":
                    vcol = self._visual_col(j)
                    self.table.item(display_row, vcol).setText(eng)
                    self.cell_colors[(display_row, vcol)] = self.COLOR_NBA
        if fill_nld:
            csv_row["DutchName"] = nld
            for j, (key, _) in enumerate(self.COLUMN_MAP):
                if key == "DutchName":
                    vcol = self._visual_col(j)
                    self.table.item(display_row, vcol).setText(nld)
                    self.cell_colors[(display_row, vcol)] = self.COLOR_NBA
        self.table.blockSignals(False)
        self.dirty = True
        self.save_btn.setEnabled(True)

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

        # Diff against the pre-merge snapshot so backfilled vernacular
        # names can be highlighted in the table. Only relevant for the
        # merge-review flow; otherwise leaves the set empty.
        self._compute_backfilled_idxs()

        total = len(self.all_rows)
        self.filtered_indices = list(range(total))
        # Default lands on the last page (where merge-newly-added rows
        # sit). If the merge also backfilled names on existing rows,
        # those are scattered earlier in the file and easy to miss —
        # so jump to the page containing the first backfilled row.
        self.page_start = max(0, total - self.PAGE_SIZE)
        if self._backfilled_csv_idxs:
            first = min(self._backfilled_csv_idxs)
            self.page_start = (first // self.PAGE_SIZE) * self.PAGE_SIZE
        self._show_page()

    def _compute_backfilled_idxs(self):
        """Build the set of csv indices in self.all_rows whose vernacular
        names differ from the pre-merge snapshot at
        highlight_changed_from_path. Newly-added rows (not present in
        the snapshot at all) are excluded — those are covered by the
        yellow highlight via highlight_rows_from."""
        self._backfilled_csv_idxs = set()
        path = self.highlight_changed_from_path
        if not path or not os.path.isfile(path):
            return
        import csv as _csv
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = _csv.DictReader(f, delimiter=";")
                old_by_sci = {(r.get("ScientificName") or "").strip(): r
                              for r in reader}
        except OSError:
            return
        for idx, row in enumerate(self.all_rows):
            sci = (row.get("ScientificName") or "").strip()
            old = old_by_sci.get(sci)
            if old is None:
                continue  # new row — handled by yellow highlight
            if (row.get("DutchName", "") != old.get("DutchName", "")
                    or row.get("EnglishName", "") != old.get("EnglishName", "")):
                self._backfilled_csv_idxs.add(idx)

    def _show_page(self):
        """Display current page of rows in the table."""
        self.table.blockSignals(True)
        self.cell_colors.clear()

        total = len(self.filtered_indices)
        page_end = min(self.page_start + self.PAGE_SIZE, total)
        # csv indices for the displayed slice (reversed for display)
        page_slice = self.filtered_indices[self.page_start:page_end]
        self.display_indices = list(reversed(page_slice))
        self.table.setRowCount(len(self.display_indices))

        # Pre-seed merge-review highlights before per-cell styling can
        # overwrite them. New (added) rows are painted yellow; rows
        # whose vernacular names were backfilled by the merge are
        # painted orange. The two sets are disjoint by construction
        # (added rows live at csv_idx >= threshold; backfills only
        # apply to rows that existed in the pre-merge snapshot).
        cols_total = self.table.columnCount()
        for visual_row, csv_idx in enumerate(self.display_indices):
            if (self.highlight_rows_from is not None
                    and csv_idx >= self.highlight_rows_from):
                colour = self.COLOR_NEW
            elif csv_idx in self._backfilled_csv_idxs:
                colour = self.COLOR_BACKFILLED
            else:
                continue
            for vcol in range(cols_total):
                self.cell_colors[(visual_row, vcol)] = colour

        # Update navigation. In the merge-review flow also surface the
        # counts of new + backfilled rows so the user knows up-front how
        # much the merge actually did.
        merge_note = ""
        if self.highlight_rows_from is not None:
            added = max(0, len(self.all_rows) - self.highlight_rows_from)
            backfilled = len(self._backfilled_csv_idxs)
            merge_note = f"  [merge: +{added} new, {backfilled} names filled]"
        filter_note = ""
        if self.filter_text:
            filter_note = f"  [filter \"{self.filter_text}\": {total} of {len(self.all_rows)}]"
        # Empty filtered set still needs sensible "0 of 0" rather than "1\u20130 of 0"
        start_display = self.page_start + 1 if total else 0
        self.range_label.setText(
            f"{start_display}\u2013{page_end} of {total}{filter_note}{merge_note}"
            f"  ({self.csv_path})")
        self.prev_btn.setEnabled(self.page_start > 0)
        self.next_btn.setEnabled(page_end < total)

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

        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(self.COPY_VISUAL_COL, 30)

    def _save_before_navigate(self):
        """Auto-save if there are unsaved changes before switching page."""
        if self.dirty:
            self.on_save()

    def on_prev_page(self):
        self._save_before_navigate()
        self.page_start = max(0, self.page_start - self.PAGE_SIZE)
        self._show_page()

    def on_next_page(self):
        self._save_before_navigate()
        total = len(self.filtered_indices)
        if total:
            self.page_start = min(total - 1, self.page_start + self.PAGE_SIZE)
        self._show_page()

    def _on_filter_changed(self, text):
        """Rebuild filtered_indices on filter input. Below FILTER_MIN_CHARS the
        filter is treated as inactive — typing 'p' shouldn't match every
        passerine in the master collection on the way to 'papio'."""
        self._save_before_navigate()
        text = text.strip().lower()
        if len(text) < self.FILTER_MIN_CHARS:
            self.filter_text = ""
            self.filtered_indices = list(range(len(self.all_rows)))
        else:
            self.filter_text = text
            self.filtered_indices = [
                i for i, r in enumerate(self.all_rows)
                if (r.get("ScientificName") or "").lower().startswith(text)
            ]
        self.page_start = 0
        self._show_page()

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
        csv_row[key] = new_value
        self.dirty = True
        self.save_btn.setEnabled(True)
        self.cell_colors[(row, col)] = self.COLOR_MANUAL

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
from configparser import ConfigParser, RawConfigParser
from pathlib import Path

class CreateBlogDialog(QDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Configure Blog Post")
        self.resize(600, 400)

        self.menu_spec = menu_spec
        self.ini_path = Path(_config["project"]["picturedirectory"]) / "blog.ini"
        # RawConfigParser disables %-interpolation so prose like "40% less"
        # in blog text doesn't trip InvalidInterpolationError.
        self.parser = RawConfigParser()
        self.passed_parent = parent 
        self.logger = parent.logger

        self.title_nl_field = ""
        self.message_nl_field  = ""
        self.title_en_field  = ""
        self.message_en_field   = ""      

        # Load or create default ini
        if self.ini_path.exists():
            try:
                self.parser.read(self.ini_path, encoding="utf-8")
            except UnicodeDecodeError:
                self.parser.read(self.ini_path, encoding="latin-1")
        else:
            self._create_default_ini()
            with open(self.ini_path, "w", encoding="utf-8") as f:
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
        try:
            with open("last_blog_url.txt", "r") as f:
                relative_url = f.read().strip()
            # relative_url is e.g. "/trips/kreta/biotablog/Blog Pictures ...-nl.html"
            return base_url.rstrip("/") + relative_url
        except FileNotFoundError:
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
        self.message_nl.setAcceptRichText(False)  # plain text — stored in blog.ini

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
        self.message_en.setAcceptRichText(False)  # plain text — stored in blog.ini

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

        with open(self.ini_path, "w", encoding="utf-8") as f:
            self.parser.write(f)

        self.logger.info("✅ blog.ini written successfully.")
        self.passed_parent.status_bar.showMessage("blog.ini written successfully.")

    def on_create_blog(self):
        try:
            self.save_ini()  # Ensure latest edits are saved

            def open_blog_in_browser():
                if self.open_after_checkbox.isChecked():
                    webbrowser.open(self.get_blog_url())

            runner = BatchModuleRunner(self, self.passed_parent)
            runner.run(self.menu_spec, on_success=open_blog_in_browser)
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
        self.status_label.setWordWrap(True)
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


class SyncSidecarsDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Sync Sidecars",
            info_text="Synchronize crop and exposure edits between DxO PhotoLab (.dop) and Lightroom (.xmp) sidecar files."
        )


class TestbedDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Call Testbed for trip day",
            info_text=(
                "Run bin/svu7Testbed.py against the active trip and selected day. "
                "Edit the script's testbed block to exercise whatever check you need — "
                "e.g. inspecting the species tree behind the all-biota summaries page."
            )
        )


class EmptyTripDataDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Empty Trip Data",
            info_text="Delete all day directories and reset active data CSVs to empty templates. Use this to get a clean slate before re-running the pipeline.",
            subdirectory=False
        )

    def on_write(self):
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=lambda: populate_trip_overview(self.passed_parent))


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

        def load_report():
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

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=load_report)

    def save_report(self):
        pic_dir = _config.get("project", "picturedirectory", fallback="").strip()
        default_dir = str(Path(pic_dir).parent) if pic_dir else ""
        default_path = str(Path(default_dir) / "Species overview.txt") if default_dir else ""
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Report As", default_path, "Text Files (*.txt);;All Files (*)"
        )
        if filename:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(self.report_viewer.toPlainText())

    def copy_to_clipboard(self):
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.report_viewer.toPlainText())

class BlogAdminDialog(QDialog):
    """Manage the trip's blog_admin.csv: review and delete blog entries.

    Each row in admin/blog_admin.csv corresponds to a blog whose rendered
    artifacts live under web/biotablog/<key>{,-en,-nl,-slider-en,-slider-nl}.
    Deleting a row also removes those four HTML files and the picture
    folder so the local web tree is clean. The user is expected to re-run
    BuildWebsite/Freeze/WriteCommit after pruning to regenerate the index.
    """

    COLUMNS = [
        ("blog",        "Blog"),
        ("title_nl",    "Title (NL)"),
        ("CreatedTime", "Created"),
        ("_pic_count",  "Pictures"),
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Manage Blogs")
        self.resize(900, 500)
        self.logger = logging.getLogger("speciesviewer")
        self.passed_parent = parent

        trip_root = _config["trip"]["tripbiotadatabase"]
        self.csv_path = os.path.join(trip_root, "admin", "blog_admin.csv")
        self.biotablog_dir = os.path.join(trip_root, "web", "biotablog")
        self.rows = []   # list of dict, original CSV order
        self.fieldnames = []

        layout = QVBoxLayout(self)

        info = QLabel(
            "Blogs registered in <code>admin/blog_admin.csv</code> for this trip. "
            "Select rows to remove — both the CSV entry and the rendered HTML "
            "files / picture folder under <code>web/biotablog/</code> are deleted. "
            "Re-run Build Website / Freeze / Write Commit afterwards."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.path_label = QLabel(self.csv_path)
        self.path_label.setStyleSheet("color: #666;")
        layout.addWidget(self.path_label)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([h for _, h in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(False)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self.on_delete)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self._load()

    def _load(self):
        import csv, ast
        if not os.path.isfile(self.csv_path):
            self.status_label.setText(f"blog_admin.csv not found: {self.csv_path}")
            self.delete_btn.setEnabled(False)
            return
        with open(self.csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            self.fieldnames = reader.fieldnames or []
            self.rows = list(reader)

        # Augment each row with a derived picture count for display.
        for row in self.rows:
            try:
                pics = ast.literal_eval(row.get("BestJPGs", "") or "[]")
                row["_pic_count"] = str(len(pics)) if isinstance(pics, list) else "?"
            except (ValueError, SyntaxError):
                row["_pic_count"] = "?"

        # Match the website's index ordering (Blog.getBlogDictSortedKeys
        # returns sorted(keys), which is chronological because the YYYY-MM-DD
        # date prefix dominates the sort).
        self.rows.sort(key=lambda r: r.get("blog", ""))

        self._render_table()

    def _render_table(self):
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            for j, (key, _) in enumerate(self.COLUMNS):
                item = QTableWidgetItem(row.get(key, ""))
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()
        self.status_label.setText(f"{len(self.rows)} blog entries.")

    def on_delete(self):
        sel_rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not sel_rows:
            self.status_label.setText("Nothing selected.")
            return

        keys = [self.rows[r]["blog"] for r in sel_rows]
        preview = "\n".join(f"  • {k}" for k in keys[:10])
        if len(keys) > 10:
            preview += f"\n  … and {len(keys) - 10} more"
        reply = QMessageBox.question(
            self, "Delete blog entries",
            f"Delete {len(keys)} blog entr{'ies' if len(keys) != 1 else 'y'}?\n\n"
            f"{preview}\n\n"
            f"This removes the row from blog_admin.csv and the rendered "
            f"HTML files + picture folder under web/biotablog/. The action "
            f"is not reversible from this dialog.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted_files = 0
        for key in keys:
            deleted_files += self._delete_web_artifacts(key)

        # Rewrite CSV without the selected rows. Drop derived columns from
        # the output — fieldnames is the original on-disk header.
        keep = [r for i, r in enumerate(self.rows) if i not in set(sel_rows)]
        self._write_csv(keep)
        self.rows = keep
        self._render_table()

        self.logger.info(
            f"Manage Blogs: removed {len(keys)} entr"
            f"{'ies' if len(keys) != 1 else 'y'} from blog_admin.csv "
            f"and {deleted_files} web artifacts."
        )
        self.status_label.setText(
            f"Deleted {len(keys)} entries, {deleted_files} web file/folder removals."
        )

        # Locked days won't go through Write Blog again, so the published
        # site still references the deleted blogs. Offer to clean the
        # server now: remove each pruned key's remote artifacts, then
        # regenerate and upload the index + admin CSV.
        prompt = QMessageBox.question(
            self, "Update online site?",
            f"Local files updated. The published site at "
            f"{_config.get('repository', 'BiotaWebRoot', fallback='(unknown)')} "
            f"still has the {len(keys)} pruned blog{'s' if len(keys) != 1 else ''} "
            f"and an index referencing them.\n\n"
            f"Delete the remote artifacts and rebuild the online index now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if prompt == QMessageBox.StandardButton.Yes:
            self._start_server_cleanup(keys)

    def _start_server_cleanup(self, pruned_keys):
        """Run server-side cleanup + index rebuild on a worker thread."""
        from PyQt6.QtWidgets import QDialog
        from PyQt6.QtGui import QMovie
        from PyQt6.QtCore import QThread, pyqtSignal, QObject

        # Spinner dialog — same look as the batch runner.
        spinner = QDialog(self)
        spinner.setWindowTitle("Updating online site...")
        spinner.setModal(True)
        spinner.setFixedSize(420, 170)
        sl = QVBoxLayout(spinner)
        msg = QLabel("Connecting to server, removing pruned blogs, rebuilding index...")
        msg.setWordWrap(True)
        sl.addWidget(msg, alignment=Qt.AlignmentFlag.AlignCenter)
        movie = QMovie("spinner.gif")
        spinner_icon = QLabel()
        spinner_icon.setMovie(movie)
        sl.addWidget(spinner_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        movie.start()

        # Make sure svconfig.ini reflects the current trip — the worker
        # reads from it just like every other batch step.
        from utils.batch_config import generate_svconfig_ini
        generate_svconfig_ini()

        class _CleanupWorker(QObject):
            finished = pyqtSignal(str)  # error string, empty on success

            def __init__(self, keys):
                super().__init__()
                self._keys = keys

            def run(self):
                try:
                    self._do_work()
                    self.finished.emit("")
                except Exception as e:
                    self.finished.emit(str(e))

            def _do_work(self):
                import configparser as _cp
                import ghexif.projectlib
                import ghexif.speciesclassification
                import ghexif.specieslib
                import ghexif.bloglib
                worker_logger = logging.getLogger("speciesviewer")

                cfg = _cp.ConfigParser()
                cfg.read("svconfig.ini")

                worker_logger.info("Manage Blogs: initializing project for index rebuild...")
                project = ghexif.projectlib.Project(
                    cfg.get("repository", "BiotaDatabase"),
                    cfg.get("project", "PictureDirectory"),
                    cfg.get("repository", "BiotaWebRoot"))
                project.initMasterAndTrip(cfg, inittrip=True)
                # Index template renders need species/classification objects
                # registered (token resolution touches them even when no
                # species data is loaded).
                sc = ghexif.speciesclassification.SpeciesClassification(project)
                project.registerSpeciesClassification(sc)
                sp = ghexif.specieslib.Species(project)
                project.registerSpecies(sp)

                blog = ghexif.bloglib.Blog(project)
                blog.read_BlogAdmin()
                worker_logger.info(
                    f"Manage Blogs: blog_admin.csv has {len(blog.blog_dict)} entries after prune."
                )
                blog.password = cfg.get("websettings", "Sftppwd").strip("'")
                sftp = blog.openSFTP(blog.password)
                cache = os.path.join(project.getRepositoryPath(), "admin/FTPcache.json")
                sftp.openFTPCache(cache)

                worker_logger.info(
                    f"Manage Blogs: removing remote artifacts for {len(self._keys)} pruned blog(s)..."
                )
                for key in self._keys:
                    blog.removeBlogArtifacts(sftp, key)

                worker_logger.info("Manage Blogs: regenerating local blog index...")
                blog.updateIndexPage()
                worker_logger.info("Manage Blogs: uploading blog index-nl/en.html...")
                blog.uploadBlogIndex(sftp)
                worker_logger.info("Manage Blogs: uploading blog_admin.csv...")
                blog.uploadBlogAdmin(sftp)

                sftp.closeFTPCache(cache)
                sftp.disconnect()
                worker_logger.info("Manage Blogs: done.")

        self._cleanup_thread = QThread()
        self._cleanup_worker = _CleanupWorker(pruned_keys)
        self._cleanup_worker.moveToThread(self._cleanup_thread)
        self._cleanup_thread.started.connect(self._cleanup_worker.run)

        def on_done(err):
            self._cleanup_thread.quit()
            self._cleanup_thread.wait()
            spinner.close()
            if err:
                self.logger.warning(f"Manage Blogs: server cleanup failed: {err}")
                self.status_label.setText(f"Server cleanup failed: {err}")
            else:
                self.logger.info(
                    f"Manage Blogs: server cleanup succeeded — "
                    f"{len(pruned_keys)} blog(s) removed remotely, index updated."
                )
                self.status_label.setText("Server cleanup succeeded.")

        self._cleanup_worker.finished.connect(on_done)
        self._cleanup_thread.start()
        spinner.show()

    def _delete_web_artifacts(self, blog_key):
        """Remove the four HTML files and the picture folder for one blog
        key. Returns the count of successful filesystem removals."""
        count = 0
        suffixes = ["-nl.html", "-en.html", "-slider-nl.html", "-slider-en.html"]
        for suffix in suffixes:
            target = os.path.join(self.biotablog_dir, blog_key + suffix)
            if os.path.isfile(target):
                try:
                    os.remove(target)
                    count += 1
                except OSError as e:
                    self.logger.warning(f"Could not remove {target}: {e}")
        folder = os.path.join(self.biotablog_dir, blog_key)
        if os.path.isdir(folder):
            try:
                shutil.rmtree(folder)
                count += 1
            except OSError as e:
                self.logger.warning(f"Could not remove folder {folder}: {e}")
        return count

    def _write_csv(self, rows):
        import csv
        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames, delimiter=";",
                                    extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            writer.writerows(rows)


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

class AddTripToMasterDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Add trip website to master",
            info_text=("Add (or refresh) the active trip's row in the master "
                       "trip admin CSV, regenerate the master tripindex pages, "
                       "and upload them — together with the trip admin CSV — "
                       "to the website. Other trips still in draft on disk "
                       "are not touched."),
            subdirectory=False
        )


class MasterPipelineDialog(QDialog):
    """Drive the master-side database merge — three progressive steps
    (ingest → build+freeze → commit) inside one dialog. Each step's
    button enables only after the previous turns green; failures keep
    the current step enabled for retry. Step 3 confirms re-commit if
    the picture directory is already in the master commit marker."""

    PIP = {
        'pending': '⬜',
        'running': '⏳',
        'done':    '✅',
        'failed':  '❌',
    }

    def __init__(self, parent, step_specs):
        super().__init__(parent)
        self.setWindowTitle("Add Trip Pictures to Master")
        self.resize(740, 380)

        self.logger = logging.getLogger("speciesviewer")
        self.passed_parent = parent
        self.step_specs = step_specs  # list of (label, MenuSpec)

        layout = QVBoxLayout(self)

        # The trip root = parent of the configured day directory. Master
        # ingest reads every day-subdir under it (sv2 walks recursively).
        configured_pic = _config.get('project', 'picturedirectory', fallback='')
        trip_root = str(Path(configured_pic).parent) if configured_pic else '?'
        info = HTMLLabel(
            "<p><b>Add the active trip's pictures to the master database.</b></p>"
            "<p>Run the three steps in order. Each step enables the next on "
            "success; failures stay open for retry. Step&nbsp;1 reads photo "
            "metadata and identifies species. Step&nbsp;2 builds the master "
            "species pages (so existing pages pick up the new pictures) and "
            "freezes the result. Step&nbsp;3 commits the frozen artifact to "
            "master's active CSVs and copies pictures into master's biota tree.</p>"
            f"<p>Trip root (reads all day subdirectories): "
            f"<code>{trip_root}</code></p>"
        )
        layout.addWidget(info)

        self.rows = []
        for idx, (label, _spec) in enumerate(step_specs):
            row = self._make_step_row(layout, label)
            row['button'].clicked.connect(
                lambda _checked=False, i=idx: self._run_step(i))
            self.rows.append(row)

        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet("color: #B45309;")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        # BatchModuleRunner expects gui.status_label / gui.logger /
        # gui.passed_parent.status_bar. Keep the surface compatible.
        # Cap the height so a future runaway error string (huge stdout
        # dumps in CalledProcessError, etc.) can't blow up the layout.
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setMaximumHeight(80)
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # Probe artifacts on disk and seed pip + timestamp + button
        # enablement from whatever survives from prior runs. After this
        # the dialog is in a "ready to either resume or restart" state.
        self._initial_probe()
        self._refresh_warning()

    def _make_step_row(self, parent_layout, label):
        row = QHBoxLayout()
        pip = QLabel(self.PIP['pending'])
        font = pip.font()
        font.setPointSize(font.pointSize() + 4)
        pip.setFont(font)
        pip.setFixedWidth(36)
        pip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(pip)

        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        text_box.addWidget(QLabel(label))
        timestamp = QLabel("")
        ts_font = timestamp.font()
        ts_font.setPointSize(max(ts_font.pointSize() - 1, 8))
        timestamp.setFont(ts_font)
        timestamp.setStyleSheet("color: #666;")
        text_box.addWidget(timestamp)
        row.addLayout(text_box)

        row.addStretch()
        button = QPushButton("Run")
        row.addWidget(button)
        parent_layout.addLayout(row)
        return {'pip': pip, 'button': button, 'timestamp': timestamp,
                'state': 'pending'}

    def _set_pip(self, idx, status):
        self.rows[idx]['state'] = status
        self.rows[idx]['pip'].setText(self.PIP[status])

    # ── Artifact probing & step state ───────────────────────────────────
    def _trip_review_dir(self):
        """<master>/metadata/review/<trip-name>/ — where sv2/sv6 write
        their per-trip artifacts in master mode."""
        master = _config.get("master", "masterbiotadatabase", fallback="")
        pic_dir = self._picture_dir()
        if not (master and pic_dir):
            return None
        return os.path.join(master, "metadata", "review", Path(pic_dir).name)

    def _artifact_mtime(self, idx):
        """Return the datetime that proves step idx ran for this trip,
        or None if there's no evidence on disk."""
        if idx == 2:
            # Step 3's "artifact" is the marker entry we write after sv7.
            when = self._committed_at()
            if not when:
                return None
            try:
                return datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        review = self._trip_review_dir()
        if not review:
            return None
        path = (os.path.join(review, "data_files.csv") if idx == 0
                else os.path.join(review, "frozen", "frozen_data_files.csv"))
        if not os.path.isfile(path):
            return None
        try:
            return datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            return None

    def _set_timestamp(self, idx, mtime):
        if mtime is None:
            self.rows[idx]['timestamp'].setText("")
        else:
            self.rows[idx]['timestamp'].setText(
                f"as of {mtime.strftime('%Y-%m-%d %H:%M')}"
            )

    def _initial_probe(self):
        for idx in range(len(self.rows)):
            mtime = self._artifact_mtime(idx)
            self._set_pip(idx, 'done' if mtime else 'pending')
            self._set_timestamp(idx, mtime)
        self._refresh_buttons()

    def _refresh_buttons(self):
        """Step 0's Run is always enabled. Step N>0 is enabled when the
        previous step is currently green (either from this session or
        from a prior-run artifact found on disk)."""
        for idx, row in enumerate(self.rows):
            if idx == 0:
                row['button'].setEnabled(True)
            else:
                row['button'].setEnabled(
                    self.rows[idx - 1]['state'] == 'done'
                )

    # ── Marker-file plumbing ────────────────────────────────────────────
    def _master_commits_path(self):
        master = _config.get("master", "masterbiotadatabase", fallback="")
        if not master:
            return None
        return os.path.join(master, "admin", "master_commits.csv")

    def _picture_dir(self):
        # Use the trip root for marker keying — same scope sv2 actually
        # ingests in master mode. Keeps the warning meaningful even if
        # picturedirectory points at a different day on the next open.
        configured = _config.get("project", "picturedirectory", fallback="").strip()
        if not configured:
            return ""
        return str(Path(configured).parent)

    def _read_commit_records(self):
        import csv as _csv
        path = self._master_commits_path()
        if not path or not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return list(_csv.DictReader(f, delimiter=";"))
        except OSError:
            return []

    def _committed_at(self):
        target = self._picture_dir().lower()
        if not target:
            return None
        for r in self._read_commit_records():
            if (r.get("picture_directory") or "").strip().lower() == target:
                return r.get("committed_at", "")
        return None

    def _refresh_warning(self):
        when = self._committed_at()
        if when:
            self.warning_label.setText(
                "⚠️  These pictures were already committed to master "
                f"on {when}. Step 3 will warn before re-committing."
            )
        else:
            self.warning_label.setText("")

    def _record_commit(self):
        import csv as _csv
        path = self._master_commits_path()
        pic_dir = self._picture_dir()
        if not (path and pic_dir):
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        records = [r for r in self._read_commit_records()
                   if (r.get("picture_directory") or "").strip().lower()
                   != pic_dir.lower()]
        records.append({
            "picture_directory": pic_dir,
            "committed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                writer = _csv.DictWriter(
                    f, fieldnames=["picture_directory", "committed_at"],
                    delimiter=";")
                writer.writeheader()
                writer.writerows(records)
        except OSError as exc:
            self.logger.warning(f"Could not write master commit marker: {exc}")

    # ── Step execution ──────────────────────────────────────────────────
    def _run_step(self, idx):
        # Step 3 (last) — confirm re-commit on a previously-committed
        # picture directory before letting sv7 append duplicates.
        if idx == len(self.rows) - 1 and self._committed_at():
            ans = QMessageBox.question(
                self, "Re-commit?",
                "These pictures were already committed to master.\n"
                "Running this step again will append duplicate rows to "
                "master's data_files.csv / data_pictures.csv.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        self._set_pip(idx, 'running')
        for r in self.rows:
            r['button'].setEnabled(False)

        spec = self.step_specs[idx][1]
        is_last_step = idx == len(self.rows) - 1

        def on_success():
            now = datetime.now()
            self._set_pip(idx, 'done')
            self._set_timestamp(idx, now)
            # A fresh run of step N invalidates anything downstream:
            # the artifacts for steps N+1..end are now stale w.r.t.
            # the new step N output, so reset their pip + timestamp.
            for m in range(idx + 1, len(self.rows)):
                self._set_pip(m, 'pending')
                self._set_timestamp(m, None)
            if is_last_step:
                self._record_commit()
                self._refresh_warning()
            self._refresh_buttons()

        def on_failure(_err):
            # Failure didn't touch downstream artifacts — leave their
            # state alone. Just flip this row to red and let the user
            # retry. Re-probe in case the script wrote partial output
            # (so the timestamp reflects truth either way).
            mtime = self._artifact_mtime(idx)
            self._set_pip(idx, 'done' if mtime else 'failed')
            self._set_timestamp(idx, mtime)
            self._refresh_buttons()
            # On failure, keep current row's button enabled regardless.
            self.rows[idx]['button'].setEnabled(True)

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(spec, on_success=on_success, on_failure=on_failure)


class MergeTripDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Merge Trip Species with master",
            info_text=("Merge the trip's species into the master collection, "
                       "then review the result in place. Newly-merged rows "
                       "are highlighted yellow. You can keep the merged "
                       "master, continue reviewing, or restore the previous "
                       "master from the transient backup."),
            subdirectory=False
        )

    def on_write(self):
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=self._review_merged_master)

    def _review_merged_master(self):
        master_dir = _config.get("master", "masterbiotadatabase", fallback="")
        if not master_dir:
            QMessageBox.warning(self, "Merge Species",
                                "Master database location is not configured.")
            return
        master_csv = os.path.join(master_dir, "metadata", "active",
                                  "speciescollection.csv")
        backup_csv = master_csv + "old"  # updateSpeciesCollection's convention
        if not os.path.isfile(master_csv):
            QMessageBox.warning(self, "Merge Species",
                                f"Merged master file not found:\n{master_csv}")
            return

        # Row count of master *before* the merge — anything in the merged
        # file with a csv index >= this is newly added and should be
        # highlighted in the review viewer.
        pre_merge_count = (self._count_csv_data_rows(backup_csv)
                           if os.path.isfile(backup_csv) else None)

        while True:
            dlg = SpeciesCollectionDialog(
                self, master_csv,
                highlight_rows_from=pre_merge_count,
                highlight_changed_from_path=(backup_csv
                                             if os.path.isfile(backup_csv)
                                             else None),
                title="Review merged master species collection",
            )
            dlg.exec()

            # The dialog exposes Keep / Restore buttons in review mode
            # and sets self.verdict accordingly. None means the user
            # closed via X without picking — fall back to the safety
            # prompt so a stray Esc/X never silently finalizes anything.
            choice = dlg.verdict or self._prompt_keep_restore_continue()
            if choice == "continue":
                continue
            if choice == "keep":
                if os.path.isfile(backup_csv):
                    try:
                        os.remove(backup_csv)
                    except OSError as exc:
                        self.logger.warning(
                            f"Could not delete merge backup {backup_csv}: {exc}")
                break
            if choice == "restore":
                if os.path.isfile(backup_csv):
                    try:
                        os.replace(backup_csv, master_csv)
                    except OSError as exc:
                        QMessageBox.warning(
                            self, "Merge Species",
                            f"Could not restore previous master:\n{exc}")
                break

    @staticmethod
    def _count_csv_data_rows(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                # Subtract one for the header row; clamp at 0 for empty files.
                return max(sum(1 for _ in f) - 1, 0)
        except OSError:
            return None

    def _prompt_keep_restore_continue(self):
        box = QMessageBox(self)
        box.setWindowTitle("Merge Species")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("How do you want to finalize the merge?")
        box.setInformativeText(
            "Keep merged master — accept the merge and discard the backup.\n"
            "Continue reviewing — reopen the species collection viewer.\n"
            "Restore previous master — discard the merge (and any name "
            "edits you made during this review)."
        )
        keep_btn    = box.addButton("Keep merged master",
                                    QMessageBox.ButtonRole.AcceptRole)
        cont_btn    = box.addButton("Continue reviewing",
                                    QMessageBox.ButtonRole.ActionRole)
        restore_btn = box.addButton("Restore previous master",
                                    QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(keep_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is keep_btn:
            return "keep"
        if clicked is restore_btn:
            return "restore"
        # Continue reviewing — also covers closing the prompt via X, which
        # forces the user back to the viewer instead of accidentally
        # accepting or discarding.
        return "continue"

class ConvertAvibaseDialog(BaseTripModuleDialog):
    READER_OPTIONS = [
        ("avibase",       "AviBase  (English in column 0)"),
        ("birdingbreaks", "Birding Breaks  (sequence number in column 0)"),
    ]
    LANG_OPTIONS = [
        ("nl", "Dutch"),
        ("en", "English"),
    ]

    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Create an Excel to keep track of birds seen.",
            info_text="Create an Excel to keep track of birds seen.",
            subdirectory=False
        )
        # Source-list format dropdown.
        self.reader_dropdown = QComboBox()
        for _, label in self.READER_OPTIONS:
            self.reader_dropdown.addItem(label)
        saved_reader = _config.get("avibase", "source_format", fallback="avibase")
        for idx, (key, _) in enumerate(self.READER_OPTIONS):
            if key == saved_reader:
                self.reader_dropdown.setCurrentIndex(idx)
                break

        # Phone-friendly layout toggle.
        self.phone_layout_checkbox = QCheckBox(
            "Phone-friendly layout (single name column, dates next, the rest at the end)"
        )
        self.phone_layout_checkbox.setChecked(
            _config.getboolean("avibase", "phone_layout", fallback=False)
        )

        # First-column language dropdown — only meaningful for the phone layout.
        self.first_lang_dropdown = QComboBox()
        for _, label in self.LANG_OPTIONS:
            self.first_lang_dropdown.addItem(label)
        saved_lang = _config.get("avibase", "first_column_lang", fallback="nl").strip().lower()
        for idx, (key, _) in enumerate(self.LANG_OPTIONS):
            if key == saved_lang:
                self.first_lang_dropdown.setCurrentIndex(idx)
                break

        self.first_lang_label = QLabel("First column language (phone layout)")
        self._sync_first_lang_enabled()
        self.phone_layout_checkbox.toggled.connect(lambda _checked: self._sync_first_lang_enabled())

        layout = self.layout()
        anchor = layout.indexOf(self.info_text)
        layout.insertWidget(anchor, QLabel("Source list format"))
        layout.insertWidget(anchor + 1, self.reader_dropdown)
        layout.insertWidget(anchor + 2, self.phone_layout_checkbox)
        layout.insertWidget(anchor + 3, self.first_lang_label)
        layout.insertWidget(anchor + 4, self.first_lang_dropdown)

    def _sync_first_lang_enabled(self):
        enabled = self.phone_layout_checkbox.isChecked()
        self.first_lang_dropdown.setEnabled(enabled)
        self.first_lang_label.setEnabled(enabled)

    def on_write(self):
        if not _config.has_section("avibase"):
            _config.add_section("avibase")
        _config["avibase"]["source_format"] = self.READER_OPTIONS[self.reader_dropdown.currentIndex()][0]
        _config["avibase"]["phone_layout"] = "true" if self.phone_layout_checkbox.isChecked() else "false"
        _config["avibase"]["first_column_lang"] = self.LANG_OPTIONS[self.first_lang_dropdown.currentIndex()][0]
        super().on_write()


class PhotoMechanicDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Create PhotoMechanic keyword files for next trip.",
            info_text=(
                "Generates two PhotoMechanic .snap keyword files from the AviBase species list: "
                "one with Dutch names and one with English names. "
                "Files are written to the PhotoMechanic IPTCKeywordsSettings folder."
            ),
            subdirectory=False
        )