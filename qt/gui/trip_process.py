from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,QCheckBox,
    QTabWidget, QWidget, QVBoxLayout, QComboBox, QLineEdit, QFormLayout, QSizePolicy, QFileDialog, QDateEdit, QTextEdit)
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtCore import Qt
import logging
import sys, os
from pathlib import Path
import webbrowser
from gui.batch import BatchModuleRunner  # Assuming batch handler is here

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
    def __init__(self, parent, config, menu_spec):
        super().__init__(parent)
        self.config = config
        self.menu_spec = menu_spec        
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

        # Status label
        self.status_label = QLabel("")
        main_layout.addWidget(self.status_label)

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
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)        

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
    def __init__(self, parent, config, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Read Picture Directory")
        self.resize(600, 250)

        self.logger = logging.getLogger("speciesviewer")
        self.config = config
        self.menu_spec = menu_spec               
        self.passed_parent = parent

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

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)

class WritePicturesDialog(QDialog):
    def __init__(self, parent, config, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Write Summary Data")
        self.resize(600, 250)

        self.logger = logging.getLogger("speciesviewer")
        self.config = config
        self.passed_parent = parent    
        self.menu_spec = menu_spec                   

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
        # Update config to point to selected metadata folder
        self.config["project"]["picturedirectory"] = os.path.join(self.base_path, selected_subdir)

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)        

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget, QWidget, QLabel, QLineEdit,
    QTextEdit, QFormLayout, QPushButton, QHBoxLayout, QSpinBox
)
from configparser import ConfigParser
from pathlib import Path

class CreateBlogDialog(QDialog):
    def __init__(self, parent, config, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Configure Blog Post")
        self.resize(600, 400)

        self.config = config
        self.menu_spec = menu_spec
        self.ini_path = Path(config["project"]["picturedirectory"]) / "blog.ini"
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
        base_url = self.config.get("websettings", "base_url", fallback="https://houtekamer.com/")
        trip_name = self.config.get("trip", "name", fallback="2017Oostzee")
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
        self.title_nl = QLineEdit(self.parser.get("blog_text", "titel_nl", fallback=""))
        self.message_nl = QTextEdit(self.parser.get("blog_text", "message_nl", fallback=""))

        form.addRow("", self.translate_button)
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
    def __init__(self, parent, config, menu_spec, title, info_text):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 250)

        self.logger = logging.getLogger("speciesviewer")
        self.config = config
        self.passed_parent = parent    
        self.menu_spec = menu_spec                   

        full_path = Path(config["project"]["picturedirectory"])
        self.base_path = full_path.parent
        self.default_subdir = full_path.name
        self.trip_root = config["trip"]["tripbiotadatabase"]

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Trip root folder"))
        self.root_label = QLineEdit(str(self.base_path))
        self.root_label.setReadOnly(True)
        layout.addWidget(self.root_label)

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
        selected_subdir = self.subdir_dropdown.currentText()
        self.config["project"]["picturedirectory"] = os.path.join(self.base_path, selected_subdir)

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)

class BuildWebsiteDialog(BaseTripModuleDialog):
    def __init__(self, parent, config, menu_spec):
        super().__init__(
            parent,
            config,
            menu_spec,
            title="Build Website",
            info_text="Prepare all web files and site structure for publication."
        )
        # self.dialog.exec()


class FreezeDialog(BaseTripModuleDialog):
    def __init__(self, parent, config, menu_spec):
        super().__init__(
            parent,
            config,
            menu_spec,
            title="Freeze Data",
            info_text="Finalize and lock down all collected information for archival or publishing."
        )
        # self.dialog.exec()


class WriteCommitDialog(BaseTripModuleDialog):
    def __init__(self, parent, config, menu_spec):
        super().__init__(
            parent,
            config,
            menu_spec,
            title="Write Website Commit",
            info_text="Finalize and lock down all collected information for archival or publishing."
        )
        # self.dialog.exec()

class CountSpeciesDialog(BaseTripModuleDialog):
    def __init__(self, parent, config, menu_spec):
        super().__init__(
            parent,
            config,
            menu_spec,
            title="Count Species",
            info_text="Count the different number of species picture in the trip."
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
        selected_subdir = self.subdir_dropdown.currentText()
        self.config["project"]["picturedirectory"] = os.path.join(self.base_path, selected_subdir)

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)

        try:
            reports_dir = Path(self.config.get("trip", "tripbiotadatabase")) / "metadata" / "reports"
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
