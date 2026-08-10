from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTabWidget, QWidget, QVBoxLayout, QComboBox, QLineEdit, QFormLayout, QSizePolicy, QFileDialog, QDateEdit, QTextEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QStyledItemDelegate, QMessageBox, QFrame, QApplication)
from PyQt6.QtGui import QFont, QGuiApplication, QColor
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal
import logging
import re
import sys, os, shutil
from datetime import datetime
from pathlib import Path
import webbrowser
from gui.batch import BatchModuleRunner  # Assuming batch handler is here

from utils.config import set_trip_settings, _config, get_trips_root, persist_config
from utils.trip_config import read_trip_ini
from widgets.small_widgets import HTMLLabel
from widgets.trip_widgets import (LabeledTextFieldQt, DateRangeFieldQt, DirectoryFieldQt, RecentDirectoryFieldQt, ImageFileFieldQt)
from engine.translatelib import CachedTranslate
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
    logger,
    picture_root_abs: str = "",
    name_profile: str = "",
):
    import os
    from datetime import datetime
    from utils.config import get_currentpictures_root
    from utils.trip_identity import to_stored_picture_root

    ini_path = os.path.join(trip_root, "trip.ini")

    master_web = _config.get("master", "masterbiotawebroot", fallback="/nature/")
    master_disk = _config.get("master", "masterbiotadatabase", fallback="E:\\Dropbox\\naturedb")
    trip_name = Path(trip_name_path).name
    # WP4: the three identities, stored explicitly so nothing has to re-derive
    # them. trip_id = on-disk folder basename; web_slug = URL/registry key.
    trip_id = os.path.basename(os.path.normpath(trip_root))
    web_slug = trip_name
    # WP8: the trip's picture root, stored RELATIVE to the per-machine
    # CurrentPictures root so the shared trip.ini stays portable.
    picture_root = ""
    if picture_root_abs:
        picture_root, is_rel = to_stored_picture_root(picture_root_abs, get_currentpictures_root())
        if not is_rel:
            logger.warning(
                f"trip.ini picture_root stored as an absolute path ({picture_root}) — it is "
                f"outside the CurrentPictures root, or that root isn't set. Configure "
                f"[currentpictures] rootdirectory in Settings for a portable trip.ini.")

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
trip_id = {trip_id}
web_slug = {web_slug}
picture_root = {picture_root}
fromdate = {from_date}
todate = {to_date}
name_profile = {name_profile}
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
        # ⚠ The initial value goes in through the CONSTRUCTOR, before this signal is
        # connected, so on_trip_root_changed never ran for it: the dialog opened
        # showing trip A's FOLDER with trip B's web root, name, dates and texts
        # (those come from _config / the previously active trip). And because
        # trip_name is DERIVED from the web-root field, saving then wrote B's
        # identity into A — that is how greecedb became "Nederland 2026".
        # Re-selecting the same folder does not help: textChanged only fires on a
        # CHANGE, so the stale web root cannot be cleared by hand at all.
        # _sync_fields_to_trip_root() at the end of __init__ closes it (it needs
        # every field to exist first, so it cannot move up here).
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
        create_folder_btn.setStyleSheet("QPushButton { padding: 4px 0px; }")
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
        self.project_path = DirectoryFieldQt("Trip pictures folder (contains the day subfolders)", _config.get("project", "picturedirectory", fallback=""))
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
        # A new trip is planned ahead: start two weeks out, run for two weeks —
        # and moving the start drags the end along so the length is kept. An
        # existing trip's saved range is restored below without re-triggering that.
        self.date_field = DateRangeFieldQt(lead_days=14, span_days=14, follow_end=True)
        self.date_field.set_dates(trip_data.get("fromdate", ""), trip_data.get("todate", ""))
        layout2.addWidget(self.date_field)

        info_label24 = HTMLLabel(
            "<p><b>Preferred names</b><br>"
            "Which of Dutch / English / Scientific reads out as the label under each "
            "picture in this trip's viewers (a fallback chain). Leave on "
            "<i>Default</i> to follow the global setting — handy to pick "
            "<i>English / Scientific</i> for a trip outside Europe.</p>")
        layout2.addWidget(info_label24)
        from utils.name_profile import profile_keys, profile_label
        self.name_profile_combo = QComboBox()
        self.name_profile_combo.addItem("Default (global setting)", "")
        for _k in profile_keys():
            self.name_profile_combo.addItem(profile_label(_k), _k)
        _np = (trip_data.get("name_profile") or "").strip()
        _npi = self.name_profile_combo.findData(_np)
        self.name_profile_combo.setCurrentIndex(_npi if _npi >= 0 else 0)
        layout2.addWidget(self.name_profile_combo)

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
        write_tripini_button = QPushButton("Update Trip Settings")
        write_tripini_button.setToolTip(
            "Save this trip's settings AND text (both this page and Trip Text) to "
            "trip.ini — WITHOUT rebuilding the repository. Use this to update an "
            "existing trip (e.g. its Preferred names). 'Create Trip' is only for "
            "building a brand-new trip's structure.")
        create_button = QPushButton("Create Trip")
        select_button = QPushButton("Select")

        # Create (build a brand-new trip's structure) comes first; Update Trip
        # Settings (the everyday save for an existing trip) sits after it.
        btn_layout.addWidget(create_button)
        btn_layout.addWidget(write_tripini_button)
        btn_layout.addWidget(select_button)

        main_layout.addLayout(btn_layout)

        # Hook up buttons
        write_tripini_button.clicked.connect(self.on_write_tripini_clicked)
        create_button.clicked.connect(self.on_create_clicked)
        select_button.clicked.connect(self.on_select)

        # Every field now exists → load them FROM the folder the dialog is showing,
        # so what you read belongs to one trip. See the note at root_field.
        self._sync_fields_to_trip_root()

    def _sync_fields_to_trip_root(self):
        """Fill the form from the trip.ini in the currently shown Trip Root folder.

        Runs once at open (the constructor-set root never fired textChanged) and is
        the same work the signal does — one path, so the two cannot drift."""
        root = self.root_field.get_text()
        if root:
            self.on_trip_root_changed(root)

    def closeEvent(self, event):
        from gui.translation_validate import prompt_translation_locked
        self.passed_parent.translator.writeTranslationCache(
            on_locked=lambda e: prompt_translation_locked(self, e))
        super().closeEvent(event)

    def create_new_trip_folder(self):
        folder_name = self.new_folder_name.text().strip()
        if not folder_name:
            self.status_label.setText("❌ Enter a folder name first.")
            return

        trips_parent = Path(get_trips_root())
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
        dist_dir = Path(__file__).resolve().parent.parent / "dist" / "includes web"
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
            # .get(), not [] — this now runs for EVERY trip the dialog opens on,
            # and an older or hand-edited trip.ini missing one key would otherwise
            # KeyError before the dialog is even on screen.
            trip_name = (trip_data.get('trip_name') or "").strip()
            if trip_name:
                if "trips" not in trip_name:
                    trip_name = "/trips/" + trip_name
                self.trip_web.set_text(trip_name)
            self.trip_img_field.field.setText(trip_data.get('trip_picture', ""))
            self.topbar_img_field.field.setText(trip_data.get('trip_banner', ""))
            if trip_data.get("fromdate") and trip_data.get("todate"):
                self.date_field.set_dates(trip_data["fromdate"], trip_data["todate"])
            self.title_nl_field.field.setText(trip_data.get('title_nl', ""))
            self.message_nl_field.field.setText(trip_data.get('message_nl', ""))
            self.title_en_field.field.setText(trip_data.get('title_en', ""))
            self.message_en_field.field.setText(trip_data.get('message_en', ""))
            # do we have the corresponding projects
            pictdir = get_cache_gui_section("trip_project_map", trip_name)
            if pictdir != "":
                _config["project"]["picturedirectory"] = pictdir
                self.project_path.field.setText(pictdir)
        else:
            print("⚠️ No trip.ini found at", new_path, ", be sure to complete all tabs.")

    def on_write_tripini_clicked(self):
        if not self.create_trip_ini():
            return
        self.logger.info("Trip settings updated (trip.ini written; repository not rebuilt).")
        self.passed_parent.status_bar.showMessage("Trip settings updated.")

    def _picturedirectory_for_config(self) -> str:
        """The value to store in [project] picturedirectory — always a DAY folder.
        The 'Trip pictures folder' field may now hold the trip root, so keep it
        only if it is already a day, otherwise map it to the first day under it
        (falling back to the field value if no day exists yet). WP8."""
        field = self.project_path.get_path()
        if field and _DAY_RE.match(Path(field).name):
            return field
        return _first_day_under(field) or field

    def on_create_clicked(self):
        # Abort if trip root or project pictures folder is invalid — otherwise the
        # batch below would build a trip pointing at a non-existent picture dir.
        if not self.create_trip_ini():
            return
        # make sure new structure is created
        pd = self._picturedirectory_for_config()
        _config.set("trip", "tripsrootdirectory", str(Path(self.root_field.get_text()).parent))
        set_trip_settings(self.root_field.get_text(), self.trip_web.field.text(), pd)
        # Also save trip/project pair (the per-machine day cache)
        update_cache_gui_section("trip_project_map", self.trip_web.field.text(), pd)
        # now do the actual work
        def _on_created():
            populate_trip_overview(self.passed_parent)
            # A brand-new trip folder doesn't exist yet in the sidebar tree
            # (TripInfoPanel scans disk once at startup) — rescan it now so the
            # trip just created is actually selectable.
            self.passed_parent.trip_panel.rebuild()
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=_on_created)

    def on_select(self):
        # save settings
        pd = self._picturedirectory_for_config()
        _config.set("trip", "tripsrootdirectory", str(Path(self.root_field.get_text()).parent))
        set_trip_settings(self.root_field.get_text(), self.trip_web.field.text(), pd)
        update_cache_gui_section("trip_project_map", self.trip_web.field.text(), pd)
        populate_trip_overview(self.passed_parent)
        self.accept()  # ✅ Actually closes the dialog

    def translate_dutch_to_english(self):
        nl_title = self.title_nl_field.get_text()
        nl_message = self.message_nl_field.get_text()

        # Force NL->EN; auto-detection misfires on short proper-noun titles.
        translated_title = self.passed_parent.translator.cachedTranslate(nl_title, language='nl')
        translated_message = self.passed_parent.translator.cachedTranslate(nl_message, language='nl')

        self.title_en_field.set_text(translated_title)
        self.message_en_field.set_text(translated_message)

        self.logger.info("✅ Translated Dutch to English using Google Translate.")

    def translate_english_to_dutch(self):
        en_title = self.title_en_field.get_text()
        en_message = self.message_en_field.get_text()

        # Force EN->NL; auto-detection misfires on short proper-noun titles.
        translated_title = self.passed_parent.translator.cachedTranslate(en_title, language='en')
        translated_message = self.passed_parent.translator.cachedTranslate(en_message, language='en')

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
            return False
        else:
            self.root_field.clear_hint()

        # ⚠ The Trip Root field opens pre-filled with the trip you are working on,
        # and "Create Folder" (which re-points it at a NEW folder) is a separate
        # step that nothing forces you to take. Fill this form top to bottom on a
        # freshly opened dialog and Create Trip lands on the ACTIVE trip, replacing
        # its identity — name, dates, picture root and description — silently,
        # because generate_trip_ini writes the whole file. That happened for real
        # (greecedb became "Nederland 2026"). Existence of the folder is not
        # consent: if it already holds a DIFFERENT trip, ask.
        existing = read_trip_ini(trip_root) or {}
        existing_name = (existing.get("trip_name") or "").strip()
        new_name = Path(trip_name).name
        if existing_name and existing_name != new_name:
            shown = (existing.get("title_nl") or "").strip() or existing_name
            folder = os.path.basename(os.path.normpath(trip_root))
            if QMessageBox.question(
                    self, "This folder already holds a trip",
                    f"“{folder}” is the trip “{shown}” ({existing_name}).\n\n"
                    f"Saving here replaces its settings with “{new_name}” — the "
                    f"name, the dates, the pictures folder and the description. "
                    f"The trip's pictures and blogs are not touched.\n\n"
                    f"To make a NEW trip instead: close this, type a folder name "
                    f"next to “New trip folder”, press “Create Folder”, and fill "
                    f"the form again.\n\n"
                    f"Replace the settings of “{shown}”?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                    ) != QMessageBox.StandardButton.Yes:
                self.root_field.show_hint(
                    "Pick an empty folder, or press “Create Folder” to make one.")
                return False
            self.logger.warning(
                f"Trip settings of '{existing_name}' in {folder} replaced by "
                f"'{new_name}' (confirmed by the user).")

        project_dir = self.project_path.get_path()
        if not project_dir or not os.path.isdir(project_dir):
            self.project_path.show_hint("Please select a valid trip pictures folder.")
            return False
        else:
            self.project_path.clear_hint()

        # WP8: record the trip's picture ROOT in trip.ini. The field may point at
        # a day folder (historic) or the root; strip a trailing day/cache segment
        # so we always store the root that holds the YYYY-MM-DD subdirs.
        picture_root_abs = _strip_to_project_root(project_dir)

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
            self.logger,
            picture_root_abs=picture_root_abs,
            name_profile=self.name_profile_combo.currentData() or "",
        )
        return True

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _strip_to_project_root(path: str) -> str:
    """Walk a project picture path up past any trailing day folder (YYYY-MM-DD)
    and/or tool-cache folder (.preview_cache etc.) so the SD-card target defaults
    to the project root — never a specific day or a cache subdir."""
    if not path:
        return path
    p = Path(path)
    while p.name and (p.name.startswith(".") or _DAY_RE.match(p.name)):
        if p.parent == p:
            break
        p = p.parent
    return str(p)


def _first_day_under(root: str) -> str:
    """The first YYYY-MM-DD day folder directly under `root`, or '' if none.
    WP8: [project] picturedirectory must stay a DAY (day-navigation relies on
    parent()==trip root), so when the user selects a trip ROOT we map it to a
    day here rather than storing the root itself."""
    try:
        days = sorted(d for d in os.listdir(root)
                      if _DAY_RE.match(d) and os.path.isdir(os.path.join(root, d)))
    except OSError:
        return ""
    return os.path.join(root, days[0]) if days else ""


def _default_year(project_root: str) -> str:
    """Year for the default filename prefix: the leading year of the project-root
    folder name (e.g. '2026 Zeeland' -> '2026'), else the current year."""
    name = Path(project_root).name if project_root else ""
    m = re.match(r"(\d{4})", name)
    return m.group(1) if m else str(datetime.now().year)


class _SDCardCopyWorker(QObject):
    """Runs copy_card on a worker thread so a multi-GB card read keeps the UI
    responsive. Progress/finished are queued back to the GUI thread via signals."""
    progress = pyqtSignal(int, int, str)   # done, total, filename
    finished = pyqtSignal(dict, str)       # summary, errorstring ("" == clean)

    def __init__(self, source, dest, prefix="", bucket_by_date=False, tz_offset_hours=0.0,
                 write_sidecars=True, include_dates=None):
        super().__init__()
        self._source = source
        self._dest = dest
        self._prefix = prefix
        self._bucket_by_date = bucket_by_date
        self._tz_offset_hours = tz_offset_hours
        self._write_sidecars = write_sidecars
        self._include_dates = include_dates
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            from batch.sv0ReadSDCard import copy_card
            summary = copy_card(
                self._source, self._dest, prefix=self._prefix,
                bucket_by_date=self._bucket_by_date, tz_offset_hours=self._tz_offset_hours,
                write_sidecars=self._write_sidecars, include_dates=self._include_dates,
                progress=lambda done, total, name: self.progress.emit(done, total, name),
                should_cancel=lambda: self._cancel,
            )
            self.finished.emit(summary, "")
        except Exception as e:
            self.finished.emit({}, str(e))


class _SDCardScanWorker(QObject):
    """Groups the card's pictures by capture day (no copy) on a worker thread so
    the dialog stays responsive while the card is walked / dates are read."""
    finished = pyqtSignal(list, int, str)   # days [(date, count)], total, error

    def __init__(self, source, tz_offset_hours=0.0):
        super().__init__()
        self._source = source
        self._tz_offset_hours = tz_offset_hours

    def run(self):
        try:
            from batch.sv0ReadSDCard import scan_days
            days, total = scan_days(self._source, tz_offset_hours=self._tz_offset_hours)
            self.finished.emit(days, total, "")
        except Exception as e:
            self.finished.emit([], 0, str(e))


# RAW extensions PhotoLab opens — used to pick a sensible file to hand it when
# opening a freshly read day (a JPG would work too, but the RAW is the picture
# that will get the .dop recipe).
_PL_RAW_EXTS = {".orf", ".nef", ".cr2", ".cr3", ".arw", ".rw2", ".raf", ".dng"}


class ReadSDCardDialog(QDialog):
    """Front-stage ingest (M0): copy pictures from a folder (e.g. a mounted
    SD-card) into the project pictures directory. No rename, no date-bucketing,
    no deletion from the card — pictures already copied are skipped so re-runs
    are safe."""
    def __init__(self, parent, menu_spec, days_mode=False, unfiled_mode=False):
        super().__init__(parent)
        self.menu_spec = menu_spec
        self.passed_parent = parent
        self.days_mode = days_mode   # Days lane: copy into a YYYY-MM-DD folder + register
        # Unfiled lane: copy into <CurrentPictures>/<year>/<year> unfiled/ and claim
        # NOTHING — no trip, no Days registry entry. Emptying a stack of cards and
        # deciding which trip each day belongs to are separate decisions; this lane
        # exists so the first one asks no questions. See engine/unfiled.py.
        self.unfiled_mode = unfiled_mode
        self.logger = parent.logger
        self.setWindowTitle("Read card into unfiled" if unfiled_mode else
                            "Copy Day from Card" if days_mode else "Read SD-cards")
        self.resize(700, 320)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.addWidget(HTMLLabel(
            "<p><b>Read a card into the unfiled inbox</b><br>"
            "Copy pictures from a card into <i>&lt;year&gt; unfiled</i>, sorted into "
            "one folder per day. <b>No trip is chosen and nothing is claimed</b> — "
            "empty card after card now, and file the days under a trip later from "
            "<i>Unfiled pictures</i>. Nothing is ever changed or deleted on the card, "
            "each copy is verified, and pictures already copied are skipped, so you "
            "can re-run this safely.</p>"
            if unfiled_mode else
            "<p><b>Read SD-cards</b><br>"
            "Copy pictures from a folder — typically a mounted SD-card — into the "
            "project pictures directory. The filename prefix is added to each picture "
            "(e.g. <i>2026 Zeeland - IMG_1234.CR3</i>); leave it empty to keep names as-is. "
            "Nothing is ever changed or deleted on the card, each copy is verified, and "
            "pictures already copied are skipped, so you can re-run this safely.</p>"
        ))

        # ── Source & options (framed, so it reads as one block separate from the
        # day selection below) ────────────────────────────────────────────────
        self.input_frame = QFrame()
        self.input_frame.setFrameShape(QFrame.Shape.StyledPanel)
        input_layout = QVBoxLayout(self.input_frame)
        input_layout.setContentsMargins(12, 12, 12, 12)
        input_layout.setSpacing(10)

        # Card source + prefix are remembered between runs (usually the same card
        # and the same trip), so the dialog reopens pre-filled.
        saved_source = get_cache_gui_section("sdcard_ingest", "source")
        saved_prefix = get_cache_gui_section("sdcard_ingest", "prefix")

        self.source_field = DirectoryFieldQt("Card / source folder", saved_source)
        input_layout.addWidget(self.source_field)

        # picturedirectory points at a day folder (.../<project>/YYYY-MM-DD) for
        # historic reasons; the card target is the project root, so strip a
        # trailing date component for the default.
        dest_default = _strip_to_project_root(_config.get("project", "picturedirectory", fallback=""))
        if days_mode:
            dest_default = _config.get("days", "location", fallback="") or dest_default
        if unfiled_mode:
            # Derived, not chosen: the whole point of this lane is that there is no
            # target to get wrong. Defaults to the CURRENT year and is re-pointed at
            # the year the card actually holds once "Scan days" has read the dates.
            from engine.unfiled import unfiled_root, currentpictures_root
            base = currentpictures_root(dest_default)
            dest_default = unfiled_root(base=base) if base else dest_default
        self.dest_field = DirectoryFieldQt(
            "Unfiled inbox (a YYYY-MM-DD folder is created here)" if unfiled_mode else
            "Days location (a YYYY-MM-DD folder is created here)" if days_mode else
            "Project pictures",
            dest_default)
        input_layout.addWidget(self.dest_field)

        # Prefix default = the year (e.g. '2026') unless a previous run saved one;
        # the user typically extends it to '2026 Zeeland'. Year is taken from the
        # project-root name when it starts with one, else the current year.
        self.prefix_field = LabeledTextFieldQt(
            "Filename prefix", saved_prefix or _default_year(dest_default), inline=True
        )
        input_layout.addWidget(self.prefix_field)

        # Date bucketing: copy into dest/<YYYY-MM-DD>/ using EXIF capture time,
        # shifted by the timezone offset BEFORE the date is taken so near-midnight
        # shots land in the intended day.
        date_row = QHBoxLayout()
        self.bucket_checkbox = QCheckBox("Sort into date subfolders (YYYY-MM-DD)")
        # Remembered between runs; defaults ON (date-per-day is the normal layout).
        self.bucket_checkbox.setChecked(get_cache_gui_section("sdcard_ingest", "by_date") != "0")
        if days_mode or unfiled_mode:
            # A Day — and an unfiled day — IS a YYYY-MM-DD folder, so date-bucketing
            # is what makes the folder, not an option.
            self.bucket_checkbox.setChecked(True)
            self.bucket_checkbox.setEnabled(False)
        date_row.addWidget(self.bucket_checkbox)
        date_row.addSpacing(20)
        self.tz_offset_field = LabeledTextFieldQt(
            "Timezone offset (hours)",
            get_cache_gui_section("sdcard_ingest", "tz_offset") or "0", inline=True)
        self.tz_offset_field.field.setMaximumWidth(60)
        _tz_tip = ("Added to the camera's clock only when choosing each photo's date "
                   "folder. Set it when the camera time is on a different timezone than "
                   "where you shot (e.g. left on home time, or not switched to summer "
                   "time). It changes which day a photo is filed under — not the time "
                   "stored in the photo.")
        for _w in (self.tz_offset_field, self.tz_offset_field.label, self.tz_offset_field.field):
            _w.setToolTip(_tz_tip)
        date_row.addWidget(self.tz_offset_field)
        date_row.addStretch()
        input_layout.addLayout(date_row)

        # XMP sidecars: the durable, RAW-immutable carrier for culling metadata
        # the grid reads/edits. Default ON, remembered between runs.
        self.sidecar_checkbox = QCheckBox("Write XMP sidecars for RAWs (date, GPS, EXIF — for culling)")
        self.sidecar_checkbox.setChecked(get_cache_gui_section("sdcard_ingest", "write_sidecars") != "0")
        input_layout.addWidget(self.sidecar_checkbox)

        # Reverse-geocode the copied pictures' GPS → write place names into the
        # sidecars (online; dedup-by-distance + throttle). Default off.
        self.location_checkbox = QCheckBox("Add location names (reverse-geocode GPS — needs internet)")
        self.location_checkbox.setChecked(get_cache_gui_section("sdcard_ingest", "add_location") == "1")
        input_layout.addWidget(self.location_checkbox)

        # Live progress log inside this (green) block, so a long copy is visible at
        # a glance — "running while you fetch a cup of tea". Hidden when idle; the
        # one-line result still lands in the status line at the very bottom.
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("font-style: italic;")
        self.progress_label.setVisible(False)
        input_layout.addWidget(self.progress_label)
        layout.addWidget(self.input_frame)

        # ── Day picker: "Scan days" lists each capture-day on the card with its
        # file count so you can untick days you don't want before copying. Its own
        # framed block; hidden until you scan (if you never scan, Read copies every
        # day as before). Greyed after a completed run to show it's done. ───────
        self.days_container = QFrame()
        self.days_container.setFrameShape(QFrame.Shape.StyledPanel)
        days_layout = QVBoxLayout(self.days_container)
        days_layout.setContentsMargins(12, 10, 12, 10)
        days_layout.setSpacing(6)
        days_title = QLabel("Days on the card — tick the ones to copy")
        _tf = days_title.font(); _tf.setBold(True); days_title.setFont(_tf)
        days_layout.addWidget(days_title)
        self.all_days_checkbox = QCheckBox("All days")
        self.all_days_checkbox.setTristate(True)
        self.all_days_checkbox.setChecked(True)
        self.all_days_checkbox.clicked.connect(self._on_all_days_clicked)
        days_layout.addWidget(self.all_days_checkbox)
        self.days_table = QTableWidget(0, 2)
        self.days_table.setHorizontalHeaderLabels(["Day", "Files"])
        self.days_table.verticalHeader().setVisible(False)
        self.days_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.days_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.days_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.days_table.horizontalHeader().setStretchLastSection(True)
        self.days_table.itemChanged.connect(self._on_day_item_changed)
        days_layout.addWidget(self.days_table)
        self.days_container.setVisible(False)
        layout.addWidget(self.days_container)

        # Extra vertical space (when the dialog is enlarged) collects here, so the
        # blocks stay compact at the top and the buttons stay at the bottom rather
        # than the widgets spreading apart.
        layout.addStretch(1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # "What to do next" note after a read. Colours come from the theme: the
        # hard-coded pale-blue card was invisible on the dark theme — a white box
        # with near-white text, which read as an empty panel.
        from gui import theme
        self.pl_hint = QLabel("")
        self.pl_hint.setWordWrap(True)
        self.pl_hint.setVisible(False)
        self.pl_hint.setStyleSheet(
            f"background:{theme.ACCENT_SOFT}; border:1px solid {theme.ACCENT_SOFT_BORDER};"
            f"color:{theme.ACCENT_INK_SOFT}; border-radius:4px;"
            "padding:6px; font-size:11px;")
        layout.addWidget(self.pl_hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.scan_btn = QPushButton("Scan days")
        self.read_btn = QPushButton("Read")
        self.view_btn = QPushButton("View pictures")
        self.view_btn.setEnabled(False)  # enabled once a read produced files
        # A freshly ingested day has no .dop files, and SpeciesViewer will not
        # invent them (PhotoLab only recognises recipes it authored itself). So
        # the moment after a read is exactly when to say: let PhotoLab write them.
        self.pl_btn = QPushButton("Open day in PhotoLab")
        self.pl_btn.setEnabled(False)
        self.pl_btn.setToolTip(
            "Open this day in DxO PhotoLab so it can create the .dop recipes "
            "(select all with Ctrl-A, then write sidecars)")
        self.close_btn = QPushButton("Close")
        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.read_btn)
        btn_row.addWidget(self.view_btn)
        btn_row.addWidget(self.pl_btn)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        self._last_targets = []     # picture paths from the most recent read
        self._last_dest = ""        # destination root (used as preview cache dir)
        self._thread = None
        self._worker = None
        self._scan_thread = None
        self._scan_worker = None
        self._scanned_source = ""   # source the day table was built from
        self._preview_dlg = None    # the open culling grid (single instance)

        self.scan_btn.clicked.connect(self.on_scan)
        self.read_btn.clicked.connect(self.on_read)
        self.view_btn.clicked.connect(self.on_view)
        self.pl_btn.clicked.connect(self.on_open_in_photolab)
        self.close_btn.clicked.connect(self.reject)
        self._close_connected = True   # True = Close, False = Cancel

    def on_open_in_photolab(self):
        """Open the day just read in DxO PhotoLab, so IT authors the .dop files.

        This is the supported direction and the reason the button exists here:
        SpeciesViewer edits a PhotoLab recipe but never creates one, because
        PhotoLab matches recipes by a UUID from its own catalog and treats an
        unknown one as a virtual copy. Until PhotoLab has written sidecars for a
        day, colour and crop live in the .xmp alone.
        """
        from pathlib import Path
        from gui.large_image_view import dxo_executable
        from utils.open_path import launch_app_with_file

        # Open a PICTURE, not a folder, and take it from what was actually
        # copied. Two things went wrong with the folder:
        #   * _last_dest is the DAYS LOCATION ("…/2026 Zomer"), the parent of the
        #     dated day folders — not the day itself;
        #   * PhotoLab is single-instance, so handing an already-running PL a
        #     folder path just raised its existing window, still showing the SD
        #     card it had open. Passing a file is the route the large view already
        #     uses successfully, and it makes PL select that picture's folder.
        targets = [t for t in (self._last_targets or []) if Path(t).is_file()]
        if not targets:
            QMessageBox.information(self, "Open in PhotoLab",
                                    "Read a day first — there is nothing to open yet.")
            return
        raws = [t for t in targets if Path(t).suffix.lower() in _PL_RAW_EXTS]
        first = sorted(raws or targets)[0]
        folder = str(Path(first).parent)
        exe = dxo_executable()
        if not Path(exe).exists():
            QMessageBox.warning(
                self, "DxO PhotoLab not found",
                f"Could not find DxO PhotoLab at:\n\n{exe}\n\n"
                "Set the correct path in Settings ▸ Interfaces to Lightroom "
                "and PhotoLab ▸ PhotoLab Location.")
            return
        if not launch_app_with_file(exe, first):
            QMessageBox.warning(self, "DxO PhotoLab",
                                f"Could not open PhotoLab for:\n{first}")
            return
        self.logger.info(
            f"Opened day in DxO PhotoLab: {folder} (via {Path(first).name})")
        self.status_label.setText(
            f"Opened {Path(folder).name} in PhotoLab — select all (Ctrl-A) and write "
            "sidecars to create the .dop recipes. If PhotoLab was already running it "
            "may still show its previous folder; browse to this day.")

    def _on_cancel(self):
        self._cancelled = True   # on_finished still fires with the partial summary
        self.status_label.setText("Cancelling…")
        self._stop_worker()
        self.read_btn.setEnabled(True)
        self._restore_close_btn()

    def _restore_close_btn(self):
        if not self._close_connected:
            self.close_btn.clicked.disconnect(self._on_cancel)
            self.close_btn.clicked.connect(self.reject)
            self.close_btn.setText("Close")
            self._close_connected = True

    def _stop_worker(self):
        # Don't let the dialog (and its QThread) be destroyed while the copy
        # worker is still running — that crashes Qt ("QThread: Destroyed while
        # thread is still running"). Cancel and wait for a clean stop first.
        if self._thread is not None and self._thread.isRunning():
            if self._worker is not None:
                self._worker.cancel()
            self._thread.quit()
            self._thread.wait(5000)
        # The day-scan thread is short-lived but must also finish cleanly before
        # the dialog (and its QThread) is destroyed.
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(5000)

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    def reject(self):
        # The Close button → reject() does not fire closeEvent, and with exec()
        # the dialog is dropped right after, so wait here too.
        self._stop_worker()
        super().reject()

    def _tz_offset(self):
        try:
            return float(self.tz_offset_field.get_text() or "0")
        except ValueError:
            return 0.0

    def _set_progress(self, text):
        """Live progress in the green block (shown only while something runs)."""
        self.progress_label.setText(text)
        self.progress_label.setVisible(bool(text))

    def _log_phase(self, text):
        """Announce a long phase in the LOG as well as on the dialog.

        Copying a card runs for minutes with nothing written to the log between
        "Found N pictures" and "Created N sidecars", which reads as a hang. The
        dialog's own progress line is not enough — the log is where the user
        looks to see whether anything is happening."""
        self._set_progress(text)
        try:
            self.logger.info(text)
        except Exception:
            pass

    def on_scan(self):
        """Group the card by capture-day and fill the day table — no copying."""
        source = self.source_field.get_path()
        if not source or not os.path.isdir(source):
            self.source_field.show_hint("Please select a valid source folder.")
            return
        self.scan_btn.setEnabled(False)
        self.read_btn.setEnabled(False)
        self._log_phase("Scanning the card for capture days — this reads every "
                        "picture's date, so it can take a while on a full card…")
        self._scanned_source = os.path.normpath(source)
        self._scan_thread = QThread()
        self._scan_worker = _SDCardScanWorker(source, self._tz_offset())
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.finished.connect(self.on_scan_finished)
        self._scan_thread.start()

    def on_scan_finished(self, days, total, error):
        self._scan_thread.quit()
        self._scan_thread.wait()
        self.scan_btn.setEnabled(True)
        self.read_btn.setEnabled(True)
        self._set_progress("")
        if error:
            self.logger.warning(f"❌ Day scan failed: {error}")
            self.status_label.setText(f"❌ {error}")
            return
        self._populate_days(days)
        # The unfiled inbox is per YEAR, and until the card is scanned we only know
        # today's year — a card shot last December would otherwise land in the wrong
        # one. Now that the dates are known, re-point at the year they are in.
        if self.unfiled_mode and days:
            from engine.unfiled import unfiled_root, currentpictures_root, year_of_day
            year = year_of_day(days[0][0])
            base = currentpictures_root(self.dest_field.get_path())
            if year and base:
                self.dest_field.field.setText(unfiled_root(year, base=base))
        self.status_label.setText(
            f"Found {total} picture(s) across {len(days)} day(s). "
            f"Tick the days to copy, then Read.")

    def _populate_days(self, days):
        """Fill the day table with one checkable row per (date, count), all ticked."""
        self.days_table.blockSignals(True)
        self.days_table.setRowCount(0)
        for date_str, count in days:
            r = self.days_table.rowCount()
            self.days_table.insertRow(r)
            day_item = QTableWidgetItem(date_str)
            day_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            day_item.setCheckState(Qt.CheckState.Checked)
            self.days_table.setItem(r, 0, day_item)
            cnt_item = QTableWidgetItem(str(count))
            cnt_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            cnt_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.days_table.setItem(r, 1, cnt_item)
        self.days_table.blockSignals(False)
        self.days_container.setVisible(bool(days))
        self.days_container.setEnabled(True)   # a fresh scan re-enables a greyed block
        self._refresh_all_days_checkbox()
        self._size_days_table()
        # Grow the dialog so the (now visible) day table fits without the user
        # having to drag it taller; keep the current width.
        self.layout().activate()
        # Never ask for less than the layout can actually take. sizeHint() alone
        # produced "QWindowsWindow::setGeometry: Unable to set geometry … minimum
        # size 543x614 … mintrack(837, 977)" — Qt clamped the request and warned,
        # because the row of buttons and the PhotoLab hint make the real minimum
        # taller than the hint suggested.
        want_h = max(self.sizeHint().height(), self.minimumSizeHint().height())
        want_w = max(self.width(), self.minimumSizeHint().width())
        self.resize(want_w, want_h)

    def _size_days_table(self):
        """Make the day table exactly tall enough for its rows, up to a cap (then
        it scrolls), so the dialog sizes itself to fit the days found. Heights are
        measured (resizeRowsToContents), not estimated — styled rows can be much
        taller than the default section size, which previously clipped the table."""
        rows = self.days_table.rowCount()
        if rows == 0:
            return
        self.days_table.resizeRowsToContents()
        header = self.days_table.horizontalHeader()
        header_h = max(header.height(), header.sizeHint().height())
        frame = 2 * self.days_table.frameWidth()
        visible = min(rows, 8)                        # show up to 8 days, then scroll
        body = sum(self.days_table.rowHeight(i) for i in range(visible))
        self.days_table.setFixedHeight(header_h + body + frame + 2)

    def _on_all_days_clicked(self):
        """Master checkbox: tick all days unless they're all ticked (then clear)."""
        n = self.days_table.rowCount()
        all_on = n > 0 and all(
            self.days_table.item(r, 0).checkState() == Qt.CheckState.Checked
            for r in range(n))
        target = Qt.CheckState.Unchecked if all_on else Qt.CheckState.Checked
        self.days_table.blockSignals(True)
        for r in range(n):
            self.days_table.item(r, 0).setCheckState(target)
        self.days_table.blockSignals(False)
        self._refresh_all_days_checkbox()

    def _on_day_item_changed(self, _item):
        self._refresh_all_days_checkbox()

    def _refresh_all_days_checkbox(self):
        """Reflect the rows in the master checkbox (checked / partial / unchecked)."""
        n = self.days_table.rowCount()
        checked = sum(1 for r in range(n)
                      if self.days_table.item(r, 0).checkState() == Qt.CheckState.Checked)
        if checked == 0:
            state = Qt.CheckState.Unchecked
        elif checked == n:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self.all_days_checkbox.blockSignals(True)
        self.all_days_checkbox.setCheckState(state)
        self.all_days_checkbox.blockSignals(False)

    def _selected_days(self):
        """Set of ticked 'YYYY-MM-DD' strings, or None when no scan was done
        (→ copy every day, the original behaviour)."""
        n = self.days_table.rowCount()
        if n == 0:
            return None
        return {self.days_table.item(r, 0).text() for r in range(n)
                if self.days_table.item(r, 0).checkState() == Qt.CheckState.Checked}

    def _selected_file_count(self):
        """Sum the file counts of the ticked day rows."""
        total = 0
        for r in range(self.days_table.rowCount()):
            if self.days_table.item(r, 0).checkState() == Qt.CheckState.Checked:
                try:
                    total += int(self.days_table.item(r, 1).text())
                except ValueError:
                    pass
        return total

    def on_read(self):
        source = self.source_field.get_path()
        dest = self.dest_field.get_path()
        if not source or not os.path.isdir(source):
            self.source_field.show_hint("Please select a valid source folder.")
            return
        if self.unfiled_mode and dest and not os.path.isdir(dest):
            # The inbox for a year does not exist until the first card of that year
            # is read. Making it here is the whole "asks no questions" promise —
            # the path is derived, so there is nothing for the user to confirm.
            try:
                os.makedirs(dest, exist_ok=True)
                self.logger.info(f"Created the unfiled inbox: {dest}")
            except OSError as e:
                self.dest_field.show_hint(f"Could not create {dest}: {e}")
                return
        if not dest or not os.path.isdir(dest):
            self.dest_field.show_hint("Please select a valid project pictures folder.")
            return

        # The project dir tracks the active trip, so it can change unexpectedly
        # when the trip switched — warn before reading a card into a different
        # folder than last time (they can easily drift out of sync otherwise).
        # Not in the unfiled lane: there the destination is DERIVED from the date,
        # so "it differs from last time" is normal (a new year, another card) and
        # the prompt would be noise on exactly the flow that must ask nothing.
        last_dest = "" if self.unfiled_mode else get_cache_gui_section("sdcard_ingest", "last_dest")
        if last_dest and os.path.normpath(last_dest) != os.path.normpath(dest):
            if QMessageBox.question(
                    self, "Project directory changed",
                    f"Last time you read a card into:\n{last_dest}\n\n"
                    f"Now you're about to read into:\n{dest}\n\n"
                    f"These differ — read into the new folder?"
                    ) != QMessageBox.StandardButton.Yes:
                return
        update_cache_gui_section("sdcard_ingest", "last_dest", dest)

        prefix = self.prefix_field.get_text()
        bucket_by_date = self.bucket_checkbox.isChecked()
        write_sidecars = self.sidecar_checkbox.isChecked()
        try:
            tz_offset = float(self.tz_offset_field.get_text() or "0")
        except ValueError:
            self.tz_offset_field.field.setText("0")
            tz_offset = 0.0

        # Remember the inputs for next time (usually the same card / trip / layout).
        update_cache_gui_section("sdcard_ingest", "source", source)
        update_cache_gui_section("sdcard_ingest", "prefix", prefix)
        update_cache_gui_section("sdcard_ingest", "by_date", "1" if bucket_by_date else "0")
        update_cache_gui_section("sdcard_ingest", "tz_offset", str(tz_offset))
        update_cache_gui_section("sdcard_ingest", "write_sidecars", "1" if write_sidecars else "0")
        update_cache_gui_section("sdcard_ingest", "add_location",
                                 "1" if self.location_checkbox.isChecked() else "0")

        # Day selection (None = no scan done → copy every day). Guard against a
        # stale scan from a different source folder, and against ticking nothing.
        include_dates = self._selected_days()
        if include_dates is not None and self._scanned_source \
                and self._scanned_source != os.path.normpath(source):
            QMessageBox.information(
                self, "Re-scan needed",
                "The day list was scanned for a different source folder.\n"
                "Please press “Scan days” again before reading.")
            return
        if include_dates is not None and not include_dates:
            QMessageBox.information(self, "No days selected",
                                    "Tick at least one day to copy, or press “Scan days”.")
            return

        # Confirm what will happen — destination + filename prefix + scope.
        if include_dates is None:
            scope = "all pictures on the card"
        else:
            scope = f"{self._selected_file_count()} picture(s) from {len(include_dates)} day(s)"
        confirm = (f"Copy {scope} to:\n\n    {dest}\n\n"
                   f'Filenames prefixed with "{prefix}".' if prefix
                   else f"Copy {scope} to:\n\n    {dest}\n\nFilenames kept as-is (no prefix).")
        if bucket_by_date:
            confirm += "\nSorted into YYYY-MM-DD subfolders."
        if QMessageBox.question(self, "Read SD-card", confirm + "\n\nProceed?") \
                != QMessageBox.StandardButton.Yes:
            return

        self._last_dest = dest
        if self.days_mode:
            from utils.config import set_setting
            set_setting("days", "location", dest)   # remember the Days location
        self._include_dates = include_dates
        self._cancelled = False
        self.read_btn.setEnabled(False)
        self.view_btn.setEnabled(False)
        self.status_label.setText("")
        self._log_phase("Copying pictures from the card — each copy is verified, "
                        "so this is the slow part…")
        if self._close_connected:
            self.close_btn.clicked.disconnect(self.reject)
            self.close_btn.clicked.connect(self._on_cancel)
            self.close_btn.setText("Cancel")
            self._close_connected = False
        days_note = "all days" if include_dates is None else f"{len(include_dates)} selected day(s)"
        self.logger.info(
            f"Reading SD-card from {source} → {dest} (prefix: {prefix or '(none)'}, "
            f"by-date: {bucket_by_date}, tz-offset: {tz_offset}h, sidecars: {write_sidecars}, "
            f"days: {days_note})"
        )

        self._thread = QThread()
        self._worker = _SDCardCopyWorker(source, dest, prefix, bucket_by_date, tz_offset,
                                         write_sidecars, include_dates=include_dates)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.on_progress)
        self._worker.finished.connect(self.on_finished)
        self._thread.start()

    def on_progress(self, done, total, name):
        self._set_progress(f"Copying {done}/{total}: {name}")

    def on_finished(self, summary, error):
        self._thread.quit()
        self._thread.wait()
        self.read_btn.setEnabled(True)
        self._restore_close_btn()
        if error:
            self.logger.warning(f"❌ Card read failed: {error}")
            self.status_label.setText(f"❌ {error}")
            return
        renamed = summary.get("renamed", 0)
        renamed_txt = f" ({renamed} renamed)" if renamed else ""
        deselected = summary.get("deselected", 0)
        deselected_txt = f", {deselected} day-deselected" if deselected else ""
        copied = summary.get("copied", 0)
        # Shared tail — what was kept is reported the same whether the run finished
        # or was aborted (the files already copied are verified and kept).
        tail = (f"skipped {summary.get('skipped', 0)}{deselected_txt}, "
                f"failed {summary.get('failed', 0)} "
                f"of {summary.get('total', 0)} pictures; {summary.get('sidecars', 0)} sidecar(s).")
        cancelled = getattr(self, "_cancelled", False)
        # The copy is only the first phase: when "Add location names" is ticked, a
        # reverse-geocode run follows and keeps writing for another minute. Saying
        # "Done" here made the dialog claim success while the log was still
        # printing "master: set place names on N picture(s)" — so the copy result
        # is reported WITHOUT the Done marker, and _add_location_names writes the
        # real "Done" once everything has finished.
        geocode_follows = (not cancelled and self.location_checkbox.isChecked()
                           and bool(summary.get("targets")))
        if cancelled:
            # "Done" + a green check would read as success — flag the abort, but
            # still show that the partial copy was kept.
            msg = f"Aborted after {copied}{renamed_txt}, {tail}"
            self.logger.warning(f"⏹ Card read aborted. {msg}")
            self.status_label.setText(f"⚠️ {msg}")
        else:
            msg = f"Copied {copied}{renamed_txt}, {tail}"
            self.logger.info(f"✅ Card read complete. {msg}")
            self.status_label.setText(
                f"⏳ {msg} Now looking up location names…" if geocode_follows
                else f"✅ Done — {msg}")
        self._read_summary_msg = msg
        self._set_progress("")
        self._last_targets = summary.get("targets", [])
        self.view_btn.setEnabled(bool(self._last_targets))
        self.pl_btn.setEnabled(bool(self._last_targets) and bool(self._last_dest))
        if self._last_targets and not cancelled:
            self.pl_hint.setText(
                "Next: open the day in <b>PhotoLab</b>, press <b>Ctrl-A</b> and "
                "<b>write sidecars</b>. That creates the .dop recipes — "
                "SpeciesViewer edits those but never creates them, so colours and "
                "crops you set before that live in the .xmp only. "
                "Best done with PhotoLab closed while you cull.")
            self.pl_hint.setVisible(True)

        # On an abort, don't auto-run the network geocode or grey the day list —
        # leave it ready for a re-read to finish the rest.
        if not cancelled:
            if geocode_follows:
                QApplication.processEvents()   # paint the "looking up…" line first
                self._add_location_names()
            self._mark_run_done()
            if self.days_mode:
                self._register_ingested_days()

    def _register_ingested_days(self):
        """After a Days-lane card copy, register each YYYY-MM-DD folder the copy
        created (derived from the copied file paths) so it appears in the Days
        list; select the newest as the active day."""
        from utils import days_registry
        folders = []
        for t in self._last_targets:
            parent = Path(t).parent
            if days_registry.is_day_folder_name(parent.name):
                key = str(parent)
                if key not in folders:
                    folders.append(key)
        for folder in folders:
            days_registry.add_day(folder)
        panel = getattr(self.passed_parent, "days_panel", None)
        if folders:
            self.passed_parent.active_day_folder = sorted(folders)[-1]
        if panel is not None:
            panel.rebuild()
        if folders:
            self.status_label.setText(
                self.status_label.text() + f"  ·  {len(folders)} day(s) added to the Days list.")

    def _mark_run_done(self):
        """Grey the day selection after a completed run, as a visible 'done' cue.
        Re-scanning (or reopening the dialog) brings it back, possibly with a
        different selection."""
        if self.days_container.isVisible():
            self.days_container.setEnabled(False)

    def _add_location_names(self):
        """Reverse-geocode the just-copied RAWs and write place names into their
        XMP sidecars (real write, fill-empties-only)."""
        from gui.geolocate import run_geolocate_batch
        raw_exts = {".cr3", ".cr2", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2"}
        targets = []
        for t in self._last_targets:
            p = Path(t)
            if p.suffix.lower() not in raw_exts:
                continue
            xmp = p.with_suffix(".xmp")
            if xmp.exists():
                targets.append((p.stem, str(xmp), [str(xmp)]))  # GPS + write both in the sidecar
        # Skip what already HAS a place name. copy_card reports every destination
        # file, skipped ones included, so a re-read of the same card handed the
        # whole set here — and while the write is fill-empties-only (nothing was
        # overwritten), it still paid a Nominatim round for pictures that were
        # named on the first read. One batched exiftool read decides it.
        already = 0
        if targets:
            from gui.geolocate import read_location_fields_batch
            have = read_location_fields_batch([t[1] for t in targets])
            fresh = []
            for label, gsrc, files in targets:
                cur = have.get(os.path.normcase(os.path.normpath(gsrc)), {})
                if cur.get("Location") or cur.get("City"):
                    already += 1
                    continue
                fresh.append((label, gsrc, files))
            targets = fresh
        base = getattr(self, "_read_summary_msg", "")
        if already:
            self.logger.info(f"Location names: {already} picture(s) already had one — "
                             f"looking up {len(targets)}.")
        if not targets:
            # Nothing left to geocode (no RAW sidecars, or every one already had a
            # place name) — the run IS finished, so the status must not stay on the
            # "looking up…" line set by on_finished.
            tail = (f" All {already} picture(s) already had place names."
                    if already else "")
            self.status_label.setText(
                (f"✅ Done — {base}{tail}" if base else f"✅ Done.{tail}").strip())
            return
        self._log_phase("Looking up location names (one online lookup per new "
                        "place, the rest reuse nearby fixes)…")
        run_geolocate_batch(self, targets, dry_run=False, overwrite=False)
        self._set_progress("")
        self.status_label.setText(
            f"✅ Done — {base} Location names added." if base
            else "✅ Done — location names added.")

    def on_view(self):
        """Open the picture grid over the just-copied files. Uses an explicit
        file list (not folder discovery) so it works regardless of date
        bucketing; RAW cards render via the embedded preview (cached_jpg_for)."""
        # Single instance: a second click while the grid is already up should just
        # raise it, not stack a second (and, with the modal parent, unresponsive)
        # grid on top — which is what made earlier views impossible to close.
        if self._preview_dlg is not None and self._preview_dlg.isVisible():
            self._preview_dlg.raise_()
            self._preview_dlg.activateWindow()
            return
        from gui.image_viewer import ImagePreviewDialog
        from gui.sidecar_provider import SidecarCollection
        from gui.preview_provider import preview_cache_dir_for
        # Only formats the grid can render (still images + RAW); skip video.
        viewable_exts = {".jpg", ".jpeg", ".heic", ".heif", ".png",
                         ".nef", ".orf", ".cr2", ".cr3", ".arw", ".rw2", ".raf", ".dng"}
        paths = [p for p in self._last_targets if Path(p).suffix.lower() in viewable_exts]
        if not paths:
            self.status_label.setText("No viewable pictures to preview.")
            return
        # Keep the preview cache OUT of the project tree — inside it, the cache
        # folder shows up as a 'day' in the trip overview and would be scanned by
        # Read Directory. Park it in the per-user app dir instead.
        cache_root = str(preview_cache_dir_for(Path(self._last_dest)))
        # Sidecar-backed culling viewer: reads/edits the XMP sidecars, no pipeline.
        collection = SidecarCollection(paths, cache_root=cache_root)
        # mode="all": a freshly read card is RAW (+ the odd camera JPG), and the
        # default "jpg" mode starts the grid with the JPG-only overlay ON — which
        # on an all-RAW card showed "232 pictures" and an empty grid.
        dlg = ImagePreviewDialog(self.passed_parent, collection=collection, mode="all",
                                 cache_root=cache_root, source_label="SD-card read — culling")
        # This dialog is launched from the Read SD-cards dialog, which runs
        # application-modal (.exec()). A modal window blocks input to every other
        # window EXCEPT one that is itself modal and higher on the modal stack —
        # so without this the grid appears but can't be scrolled or closed. Making
        # it modal puts it on top and lets it receive input.
        dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.finished.connect(lambda *_: setattr(self, "_preview_dlg", None))
        self._preview_dlg = dlg
        dlg.show()


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


class ReadSummarizeDialog(QDialog):
    """Default trip ingest: run Read Directory (sv2) + Write Summary (sv3) as
    one step. Now that the EXIF cache makes re-reading cheap, fresh data is the
    sensible default — a species/spelling/translation fix written to the
    XMP/JPG only reaches the reports once both steps re-run. Uses
    ChainedBatchRunner so the two statemachine keys (ReadDirExif first, then
    WritePictures) are set in the right order. The demoted "Read Directory" and
    "Write Summary" items remain for a CSV-only refresh / debugging."""

    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Read & Summarize")
        self.resize(600, 320)

        self.logger = logging.getLogger("speciesviewer")
        self.menu_spec = menu_spec
        self.passed_parent = parent

        full_path = Path(_config["project"]["picturedirectory"])
        self.base_path = full_path.parent
        self.subdir_name = full_path.name

        layout = QVBoxLayout(self)

        info_html = HTMLLabel(
            "<p><b>Read &amp; Summarize a folder of pictures</b><br>"
            "This runs <b>Read Directory</b> and <b>Write Summary</b> back to "
            "back. Read Directory extracts Exif metadata (cached, so re-reading "
            "is fast); Write Summary enriches it with species information and "
            "writes the per-picture data the blog, website and validation panels "
            "use.<br>"
            "Use this as the normal step after correcting species names, spelling "
            "or translation source texts in Lightroom / DxO PhotoLab — both "
            "stages must re-run for the fix to reach the reports.<br>"
            "(The separate <b>Read Directory</b> / <b>Write Summary</b> items "
            "remain available for a quick read-only refresh or debugging.)</p>"
        )
        layout.addWidget(info_html)

        self.base_dir_field = DirectoryFieldQt("Project root folder", str(self.base_path))
        layout.addWidget(self.base_dir_field)

        self.subdir_dropdown = QComboBox()
        self.subdir_dropdown.setEditable(False)
        layout.addWidget(QLabel("Select subdirectory"))
        layout.addWidget(self.subdir_dropdown)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.read_btn = QPushButton("Read && Summarize")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.read_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.base_dir_field.field.textChanged.connect(self.update_subdirs)
        self.read_btn.clicked.connect(self.on_run)
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

    def on_run(self):
        import configparser
        from gui.batch import ChainedBatchRunner
        from engine.statemachine import StateMachine
        from utils.batch_config import generate_svconfig_ini
        import batch.sv2ReadDirExif
        import batch.sv3WritePictures

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
        self.logger.info(f"📁 Read & Summarize: {full_selected_path}")

        generate_svconfig_ini()
        module_config = configparser.ConfigParser()
        module_config.read("svconfig.ini")

        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        review_day = trip_root / "metadata" / "review" / selected_subdir
        sm = StateMachine(str(trip_root), str(review_day), readState=True)

        steps = [
            (batch.sv2ReadDirExif.main, "ReadDirExif",
             "Reading picture directory...", "✅ Directory scan complete."),
            (batch.sv3WritePictures.main, "WritePictures",
             "Reading meta data for all pictures ...", "✅ Summary written."),
        ]

        def on_complete():
            sm2 = StateMachine(str(trip_root), str(review_day), readState=True)
            self.passed_parent.refresh_trip_row(sm2, selected_subdir)

        # Parent the runner/spinner to the main window (not this dialog) so we
        # can close the dialog immediately without affecting the spinner.
        runner = ChainedBatchRunner(self.passed_parent, self.passed_parent, steps,
                                    module_config, sm, selected_subdir, on_complete)
        runner.start()
        self.close()


class PublishDayDialog(QDialog):
    """Publish a day: run Freeze (sv6) + Write Website Commit (sv7) as one step.
    Freeze builds the frozen snapshot and copies pictures into the repository;
    Write Commit promotes it to active, uploads the website and copies the tree
    into the master. Uses ChainedBatchRunner so the two statemachine keys
    (Freeze first, then WriteCommit) are set in order — mirrors Read & Summarize.
    The individual Freeze / Write Website Commit items remain under
    Trip ▸ Advanced (legacy)."""

    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Publish Day")
        self.resize(600, 320)

        self.logger = logging.getLogger("speciesviewer")
        self.menu_spec = menu_spec
        self.passed_parent = parent

        full_path = Path(_config["project"]["picturedirectory"])
        self.base_path = full_path.parent
        self.subdir_name = full_path.name

        layout = QVBoxLayout(self)

        info_html = HTMLLabel(
            "<p><b>Publish a day</b><br>"
            "This runs <b>Freeze</b> and <b>Write Website Commit</b> back to back. "
            "Freeze locks down the collected data and copies the pictures into the "
            "repository; Write Website Commit promotes the frozen data to active, "
            "uploads the website, and copies the tree into the master.<br>"
            "Run this once a day's blog and website look right — it requires an "
            "internet connection.<br>"
            "(The separate <b>Freeze</b> / <b>Write Website Commit</b> items remain "
            "under Trip ▸ Advanced for the freeze-only edge case or debugging.)</p>"
        )
        layout.addWidget(info_html)

        self.base_dir_field = DirectoryFieldQt("Project root folder", str(self.base_path))
        layout.addWidget(self.base_dir_field)

        self.subdir_dropdown = QComboBox()
        self.subdir_dropdown.setEditable(False)
        layout.addWidget(QLabel("Select subdirectory"))
        layout.addWidget(self.subdir_dropdown)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.run_btn = QPushButton("Publish Day")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.run_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.base_dir_field.field.textChanged.connect(self.update_subdirs)
        self.run_btn.clicked.connect(self.on_run)
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

    def on_run(self):
        import configparser
        from gui.batch import ChainedBatchRunner
        from engine.statemachine import StateMachine
        from utils.batch_config import generate_svconfig_ini
        import batch.sv6Freeze
        import batch.sv7WriteCommit

        # Don't publish while an open culling window has unsaved edits — Freeze
        # would snapshot stale review data. (Covers the menu path; the day-board
        # path is guarded in handle_trip_table_double_click.)
        guard = getattr(self.passed_parent, "confirm_no_pending_edits", None)
        if guard and not guard("Publish Day"):
            return

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
        self.logger.info(f"📤 Publish Day: {full_selected_path}")

        generate_svconfig_ini()
        module_config = configparser.ConfigParser()
        module_config.read("svconfig.ini")

        trip_root = Path(_config.get("trip", "tripbiotadatabase"))
        review_day = trip_root / "metadata" / "review" / selected_subdir
        sm = StateMachine(str(trip_root), str(review_day), readState=True)

        steps = [
            (batch.sv6Freeze.main, "Freeze",
             "Freezing all collected data ...", "✅ Data frozen."),
            (batch.sv7WriteCommit.main, "WriteCommit",
             "Publishing to the website ...", "✅ Website published."),
        ]

        def on_complete():
            sm2 = StateMachine(str(trip_root), str(review_day), readState=True)
            self.passed_parent.refresh_trip_row(sm2, selected_subdir)

        runner = ChainedBatchRunner(self.passed_parent, self.passed_parent, steps,
                                    module_config, sm, selected_subdir, on_complete)
        runner.start()
        self.close()


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
        # A name edit changes how every picture of that species renders, across
        # all days — offer to re-summarize the trip so the reports catch up.
        if getattr(dlg, "_saved_changes", False):
            self.passed_parent.rerun_summary_all_days("Species names were changed.")

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
        # ScientificNames whose editable fields were changed this session — the
        # caller mirrors exactly these into a trip's copy of the list.
        self.changed_names = set()
        # dirty is reset on every save, so it can't tell the caller "something
        # was edited and saved this session". _saved_changes is sticky: set once
        # any save persists an edit, so the trip call sites can offer to re-run
        # Write Summary on close.
        self._saved_changes = False
        self.cell_colors = {}   # (row, visual_col) -> QColor
        self.PAGE_SIZE = 50

        layout = QVBoxLayout(self)

        # Navigation bar
        nav_layout = QHBoxLayout()
        self.prev_btn = QPushButton("<")
        self.prev_btn.setFixedWidth(30)
        self.prev_btn.setStyleSheet("QPushButton { padding: 4px 0px; }")
        self.prev_btn.clicked.connect(self.on_prev_page)
        nav_layout.addWidget(self.prev_btn)
        self.range_label = QLabel("")
        self.range_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav_layout.addWidget(self.range_label)
        self.next_btn = QPushButton(">")
        self.next_btn.setFixedWidth(30)
        self.next_btn.setStyleSheet("QPushButton { padding: 4px 0px; }")
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
        from engine.nabortalib import NBASpecies
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
            copy_btn.setStyleSheet("QPushButton { padding: 0; }")
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
        # Remember WHICH names were touched, so an edit made on the shared list
        # can be mirrored into an active trip's copy without diffing two
        # 19,000-row files.
        name = (csv_row.get("ScientificName") or "").strip()
        if name:
            self.changed_names.add(name)
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
        self._saved_changes = True
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
            from utils.runtime_files import LAST_BLOG_URL, runtime_path
            with open(runtime_path(LAST_BLOG_URL), "r") as f:
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

            # Force the source language (this button is always NL->EN). Auto-
            # detection misfires on short, proper-noun-heavy titles like
            # "Dag 8: De Nestos Delta en Nestos kloof" and leaves them untranslated.
            translated_title = self.passed_parent.translator.cachedTranslate(nl_title, language='nl')
            translated_message = self.passed_parent.translator.cachedTranslate(nl_message, language='nl')

            self.title_en.setText(translated_title)
            self.message_en.setPlainText(translated_message)

            self.logger.info("✅ Translated Dutch to English using Google Translate.")
        except Exception as e:
            self.logger.error(f"❌ Translation failed: {e}")

    def translate_english_to_dutch(self):
        try:
            en_title = self.title_en.text().strip()
            en_message = self.message_en.toPlainText().strip()

            # Force the source language (this button is always EN->NL); see the
            # NL->EN handler for why auto-detection is unreliable on short titles.
            translated_title = self.passed_parent.translator.cachedTranslate(en_title, language='en')
            translated_message = self.passed_parent.translator.cachedTranslate(en_message, language='en')

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

        self.root_caption = QLabel("Trip root folder")
        layout.addWidget(self.root_caption)
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
            title="Reset Trip",
            info_text="No customization is lost, but after reset you can customize more. "
                      "This unlocks the trip: it clears the day directories and resets the "
                      "active data to empty templates so the whole pipeline can be re-run "
                      "(your pictures and the master are untouched).",
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
                import engine.projectlib
                import engine.speciesclassification
                import engine.specieslib
                import engine.bloglib
                worker_logger = logging.getLogger("speciesviewer")

                cfg = _cp.ConfigParser()
                cfg.read("svconfig.ini")

                worker_logger.info("Manage Blogs: initializing project for index rebuild...")
                project = engine.projectlib.Project(
                    cfg.get("repository", "BiotaDatabase"),
                    cfg.get("project", "PictureDirectory"),
                    cfg.get("repository", "BiotaWebRoot"))
                project.initMasterAndTrip(cfg, inittrip=True)
                # Index template renders need species/classification objects
                # registered (token resolution touches them even when no
                # species data is loaded).
                sc = engine.speciesclassification.SpeciesClassification(project)
                project.registerSpeciesClassification(sc)
                sp = engine.specieslib.Species(project)
                project.registerSpecies(sp)

                blog = engine.bloglib.Blog(project)
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
            title="Make the Blogs Clickable",
            info_text=("Adds species links & Previous/Next navigation to the "
                       "published day blogs (HTML only — no pictures moved). "
                       "Run once all days are committed."),
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

class _RescanWorker(QObject):
    """Runs exifcache.rescan (the long master EXIF scan) on a worker thread so
    the dialog stays responsive. rescan logs its own per-chunk progress."""
    finished = pyqtSignal(str, str)   # (error or "", brag summary or "")

    def __init__(self, root, cache_path):
        super().__init__()
        self._root = root
        self._cache_path = cache_path

    def run(self):
        try:
            from engine import exifcache
            stats = exifcache.rescan(self._root, self._cache_path)
            self.finished.emit("", stats.get("summary", ""))
        except Exception as e:
            logging.getLogger("speciesviewer").error(
                f"Master EXIF rescan failed: {e}", exc_info=True)
            self.finished.emit(str(e), "")


class RescanMasterCacheDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Rescan master EXIF cache",
            info_text=("Read EXIF for every picture under the master "
                       "(<master>/biota) into the master EXIF cache "
                       "(exif_cache_master.json). The first run reads every file "
                       "in parallel, using as many workers as this machine's "
                       "cores allow — a full master (~160k files) takes a few "
                       "minutes; afterwards only new or changed files are "
                       "re-read. This cache is separate from the day/trip cache, "
                       "so it never slows Read Directory."),
            subdirectory=False
        )
        # The base dialog defaults the path to the TRIP picture root, which is
        # wrong for a master-wide scan (no biota tree there → nothing cached).
        # Point it at <master>/biota and make it editable so the user can
        # confirm/adjust the scan target; this field drives the scan (on_write).
        from engine import exifcache
        master = exifcache.master_repository(_config)
        self._default_root = os.path.join(master, "biota") if master else ""
        self.root_caption.setText("Master biota folder to scan")
        self.root_label.setText(self._default_root)
        self.root_label.setReadOnly(False)
        self._rescan_thread = None
        self._rescan_worker = None

    def on_write(self):
        root = self.root_label.text().strip()
        if not root or not os.path.isdir(root):
            self.status_label.setText(f"❌ Folder not found: {root or '(empty)'}")
            return
        if self._rescan_thread is not None:
            return   # already running
        from engine import exifcache
        cache_path = exifcache.master_cache_path(_config)
        max_workers = exifcache.auto_worker_count(10 ** 6)
        self.write_btn.setEnabled(False)
        self.status_label.setText(
            f"Scanning master EXIF into the cache with up to {max_workers} "
            f"parallel workers… (progress in the log)")
        self._rescan_thread = QThread(self)
        self._rescan_worker = _RescanWorker(root, cache_path)
        self._rescan_worker.moveToThread(self._rescan_thread)
        self._rescan_thread.started.connect(self._rescan_worker.run)
        self._rescan_worker.finished.connect(self._on_rescan_done)
        self._rescan_worker.finished.connect(self._rescan_thread.quit)
        self._rescan_worker.finished.connect(self._rescan_worker.deleteLater)
        self._rescan_thread.finished.connect(self._rescan_thread.deleteLater)
        self._rescan_thread.start()

    def _on_rescan_done(self, err, summary):
        self._rescan_thread = None
        self._rescan_worker = None
        self.write_btn.setEnabled(True)
        if err:
            self.status_label.setText(f"❌ {err}")
        else:
            self.status_label.setText(
                f"✅ {summary}" if summary else "✅ Master EXIF cache updated.")


class MasterRebuildWebsiteDialog(QDialog):
    """Force a full re-render + upload of every master website page.

    HTML-only and publish-only: it re-renders every species/taxon page (so a
    shared-template change reaches all pages at once) and uploads, but never
    freezes or commits any master data. Runs threaded via BatchModuleRunner so
    the (multi-minute) render+upload doesn't block the GUI."""

    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.logger        = parent.logger
        self.passed_parent = parent
        self.menu_spec     = menu_spec
        self.setWindowTitle("Rebuild Entire Master Website")
        self.resize(600, 300)

        layout = QVBoxLayout(self)
        layout.addWidget(HTMLLabel(
            "<p><b>Rebuild the entire master website</b></p>"
            "<p>Re-renders <b>every</b> species and taxon page — not only those "
            "changed since the last build — and uploads them. Use it after a "
            "shared-template change that should appear on all pages (e.g. the "
            "species search box).</p>"
            "<p>This re-renders HTML and uploads only. It does <b>not</b> change, "
            "freeze, or commit any master data. Unchanged files are skipped on "
            "upload, but a full master can still take several minutes.</p>"
        ))

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.run_btn = QPushButton("Rebuild && Upload")
        close_btn = QPushButton("Close")
        btn_layout.addWidget(self.run_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.run_btn.clicked.connect(self.on_run)
        close_btn.clicked.connect(self.close)

    def on_run(self):
        self.run_btn.setEnabled(False)
        # on_success passes no args, on_failure passes the error string — accept both.
        def _reenable(*_args):
            self.run_btn.setEnabled(True)
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=_reenable, on_failure=_reenable)


class MasterPipelineDialog(QDialog):
    """Drive the master-side database merge — four progressive steps
    (count → ingest → build+freeze → commit) inside one dialog. Each step's
    button enables only after the previous turns green; failures keep
    the current step enabled for retry. The last step confirms re-commit if
    the picture directory is already in the master commit marker.

    Steps are declared as (label, MenuSpec, kind). The kind — not the index —
    says which artifact on disk proves the step ran, so inserting a step never
    silently re-points the probes at the wrong files."""

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
        # list of (label, MenuSpec, kind); kind drives artifact probing
        self.step_specs = [s if len(s) == 3 else (s[0], s[1], "")
                           for s in step_specs]
        self.step_kinds = [s[2] for s in self.step_specs]

        layout = QVBoxLayout(self)

        # The trip root = parent of the configured day directory. Master
        # ingest reads every day-subdir under it (sv2 walks recursively).
        configured_pic = _config.get('project', 'picturedirectory', fallback='')
        trip_root = str(Path(configured_pic).parent) if configured_pic else '?'
        info = HTMLLabel(
            "<p><b>Add the active trip's pictures to the master database.</b></p>"
            "<p>Run the four steps in order. Each step enables the next on "
            "success; failures stay open for retry. Step&nbsp;1 counts the "
            "trip's species and stores the result in the master — it runs "
            "first because it must see the master as it was <i>before</i> this "
            "trip, and it is the only record of the trip's new species that "
            "survives retiring the trip. Step&nbsp;2 reads photo "
            "metadata and identifies species. Step&nbsp;3 builds the master "
            "species pages (so existing pages pick up the new pictures) and "
            "freezes the result. Step&nbsp;4 commits the frozen artifact to "
            "master's active CSVs and copies pictures into master's biota tree.</p>"
            f"<p>Trip root (reads all day subdirectories): "
            f"<code>{trip_root}</code></p>"
        )
        layout.addWidget(info)

        self.rows = []
        for idx, (label, _spec, _kind) in enumerate(self.step_specs):
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

        # When True the dialog is auto-advancing through every step (the
        # "Run all" button). A step failure — or the user declining the
        # re-commit confirm — stops the chain.
        self._chaining = False

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.run_all_btn = QPushButton("Run all")
        self.run_all_btn.setToolTip(
            "Run every step in order, stopping automatically if one fails.")
        self.run_all_btn.clicked.connect(self._run_all)
        btn_layout.addWidget(self.run_all_btn)
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

    def _verdict_archive_path(self):
        """<master>/admin/trip_reports/<trip_id> species overview.txt — the
        archived verdict step 1 writes."""
        master = _config.get("master", "masterbiotadatabase", fallback="")
        trip_db = _config.get("trip", "tripbiotadatabase", fallback="")
        if not (master and trip_db):
            return None
        from utils.trip_identity import resolve
        trip_id = resolve(trip_db, kind="trip").trip_id
        if not trip_id:
            return None
        import engine.trip_verdict
        return os.path.join(engine.trip_verdict.archive_dir(master),
                            f"{trip_id} species overview.txt")

    def _artifact_mtime(self, idx):
        """Return the datetime that proves step idx ran for this trip,
        or None if there's no evidence on disk. Keyed on the step's kind so
        the probes follow the steps rather than their positions."""
        kind = self.step_kinds[idx] if idx < len(self.step_kinds) else ""
        if kind == "commit":
            # The commit step's "artifact" is the marker entry we write after sv7.
            when = self._committed_at()
            if not when:
                return None
            try:
                return datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        if kind == "verdict":
            path = self._verdict_archive_path()
        else:
            review = self._trip_review_dir()
            if not review:
                return None
            path = (os.path.join(review, "data_files.csv") if kind == "ingest"
                    else os.path.join(review, "frozen", "frozen_data_files.csv"))
        if not path or not os.path.isfile(path):
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
    def _run_all(self):
        """Run every step in order, auto-advancing on success and stopping on
        the first failure (or a declined re-commit confirm)."""
        self._chaining = True
        self.run_all_btn.setEnabled(False)
        self._run_step(0)

    def _stop_chain(self):
        self._chaining = False
        self.run_all_btn.setEnabled(True)

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
                if self._chaining:
                    self._stop_chain()
                    self._refresh_buttons()
                return

        self._set_pip(idx, 'running')
        self.run_all_btn.setEnabled(False)
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
            # Auto-advance to the next step when running the whole chain.
            if self._chaining and not is_last_step:
                self._run_step(idx + 1)
            else:
                self._stop_chain()

        def on_failure(_err):
            # Failure didn't touch downstream artifacts — leave their
            # state alone. Just flip this row to red and let the user
            # retry. Re-probe in case the script wrote partial output
            # (so the timestamp reflects truth either way).
            mtime = self._artifact_mtime(idx)
            self._set_pip(idx, 'done' if mtime else 'failed')
            self._set_timestamp(idx, mtime)
            self._stop_chain()  # a serious error stops the whole chain
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


class SmallMasterDialog(QDialog):
    """Create a smallmaster.zip from the master biota tree.

    The ZIP contains JPG+XMP species images (biota/) and the master index
    CSVs (metadata/active/).  RAW files and website output are excluded.
    Transfer the ZIP to the laptop and use Install Small Master to unpack it."""

    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Create Small Master Database")
        self.resize(650, 320)
        self.menu_spec = menu_spec
        self.logger = logging.getLogger("speciesviewer")
        self.passed_parent = parent

        master_root = _config.get("master", "masterbiotadatabase", fallback="")

        layout = QVBoxLayout(self)

        info = QLabel(
            "<b>Create SmallMaster ZIP</b><br>"
            "Writes <tt>smallmaster.zip</tt> into the selected directory.  "
            "The ZIP contains only JPG and XMP files from <tt>biota/</tt>, "
            "plus all CSVs from <tt>metadata/active/</tt> "
            "(data_files, data_pictures, species, speciescollection…).  "
            "RAW files and web output are excluded.  "
            "Use <i>Install Small Master</i> on the laptop to unpack it."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addWidget(QLabel("Master source (read-only):"))
        source_label = QLineEdit(master_root or "(not configured)")
        source_label.setReadOnly(True)
        layout.addWidget(source_label)

        self.target_field = DirectoryFieldQt("Output directory (ZIP saved here)", "")
        layout.addWidget(self.target_field)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.copy_btn = QPushButton("Create ZIP")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.copy_btn.clicked.connect(self.on_copy)
        self.close_btn.clicked.connect(self.close)

    def on_copy(self):
        target = self.target_field.get_path()
        if not target or not os.path.isdir(target):
            self.status_label.setText("Select a valid output directory first.")
            return
        if not _config.has_section("utilities"):
            _config.add_section("utilities")
        _config["utilities"]["smallmaster_target"] = target
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)


def _as_int(value, default=0):
    """A manifest value read back as a number — ini values are always strings,
    and an older export may not carry the key at all."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class ExportTripDialog(QDialog):
    """Zip the active trip's admin for transfer to another machine.

    Kilobytes, not gigabytes: trip.ini, the two trip images, the species
    collection, the lifer baseline, the trip's own translation cache and a
    manifest. The pictures move separately with an OS copy — this dialog names
    the folder to copy, so that step cannot be guessed wrong."""

    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Export Trip")
        self.resize(700, 400)
        self.menu_spec = menu_spec
        self.logger = logging.getLogger("speciesviewer")
        self.passed_parent = parent

        trip_root = _config.get("trip", "tripbiotadatabase", fallback="")

        layout = QVBoxLayout(self)

        info = QLabel(
            "<b>Export this trip</b><br>"
            "Writes a small ZIP holding the trip's settings (<tt>trip.ini</tt>), its "
            "trip picture and banner, its species list, its lifer status, its "
            "translations and its publication state.<br><br>"
            "The <b>pictures are not included</b> — copy the day folders yourself "
            "with Explorer or Finder, and use <i>Read &amp; Summarize</i> on the "
            "other machine to rebuild the day data from them."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addWidget(QLabel("Trip to export:"))
        source_label = QLineEdit(trip_root or "(no trip selected)")
        source_label.setReadOnly(True)
        layout.addWidget(source_label)

        # The one manual step, spelled out (D8) — computed up front so it is on
        # screen before the export runs, not only in the result message.
        # What to copy and whether it is published are the two facts that decide
        # whether this export is any use — so they read as normal text, not as
        # greyed-out chrome (a hardcoded grey vanishes on the dark theme).
        from gui import theme as _theme
        self.copy_label = QLabel("")
        self.copy_label.setWordWrap(True)
        self.copy_label.setStyleSheet(f"color: {_theme.INK_SOFT};")
        if trip_root and os.path.isdir(trip_root):
            try:
                from engine.trip_transfer import build_manifest, copy_instruction
                _cfg, _info = build_manifest(
                    trip_root,
                    master_root=_config.get("master", "masterbiotadatabase", fallback=""))
                published = {"yes": "This trip IS published on the website.",
                             "no": "This trip is not published yet.",
                             "unknown": "Publication state could not be determined."}
                from html import escape as _e
                self.copy_label.setTextFormat(Qt.TextFormat.RichText)
                # Escape: the instruction carries a Windows path, and an
                # unescaped one can be eaten by Qt's HTML parser.
                self.copy_label.setText(
                    f"📁 {_e(copy_instruction(_info))}<br>"
                    f"🌐 {published[_info['published']]}")
            except Exception as e:
                self.copy_label.setText(f"(could not read the trip: {e})")
        layout.addWidget(self.copy_label)
        layout.addSpacing(8)

        # Remembered across sessions: the transfer folder is the same one every
        # time (a share, a stick), so re-picking it on every export is pure toil.
        self.target_field = DirectoryFieldQt(
            "Output directory (ZIP saved here)",
            _config.get("utilities", "trip_export_target", fallback=""))
        layout.addWidget(self.target_field)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.copy_btn = QPushButton("Create ZIP")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.copy_btn.clicked.connect(self.on_export)
        self.close_btn.clicked.connect(self.close)

    def on_export(self):
        target = self.target_field.get_path()
        if not target or not os.path.isdir(target):
            self.status_label.setText("Select a valid output directory first.")
            return
        if not _config.has_section("utilities"):
            _config.add_section("utilities")
        _config["utilities"]["trip_export_target"] = target
        persist_config()          # remembered for the next export
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)


class ImportTripDialog(QDialog):
    """Unpack a trip export ZIP into this machine's Trips root and select it.

    Two cases, and the dialog says which one it is before you press Import:
    a NEW trip (the folder does not exist — the repository is built here), or an
    UPDATE of a trip this machine already has (settings replaced, species list
    and translations MERGED, the old files kept in the trip's .trash).

    Never touches the pictures, and never publishes anything."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Import Trip")
        self.resize(760, 560)
        self.logger = parent.logger
        self.passed_parent = parent
        self._manifest = None
        self._blocked = False

        layout = QVBoxLayout(self)

        info = QLabel(
            "<b>Import a trip from a ZIP file</b><br>"
            "Restores the trip's settings, images, species list, lifer status and "
            "translations, and builds its folder structure here. "
            "If this machine already has the trip, the species list and "
            "translations are <b>merged</b> and the old files are kept in the "
            "trip's <tt>.trash</tt> folder.<br>"
            "The pictures are not in the ZIP — copy the day folders separately."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Label + field on ONE line each, so the two paths read as a compact
        # block instead of stacking label-over-field down the dialog.
        from PyQt6.QtWidgets import QFormLayout as _QFormLayout
        form = _QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        zip_row = QHBoxLayout()
        # Remembered across sessions — the ZIP comes from the same transfer
        # folder every time.
        self.zip_field = QLineEdit(_config.get("utilities", "trip_import_zip", fallback=""))
        self.zip_field.setPlaceholderText("Select a trip ZIP…")
        zip_row.addWidget(self.zip_field)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_zip)
        zip_row.addWidget(browse_btn)
        form.addRow("ZIP file:", zip_row)

        trips_root = get_trips_root()
        self.root_edit = QLineEdit(trips_root or "(not configured — set it in Settings ▸ Repositories)")
        self.root_edit.setReadOnly(bool(trips_root))
        form.addRow("Trips root:", self.root_edit)
        layout.addLayout(form)
        # Breathing room between the inputs and what the dialog tells you about
        # them — they are different kinds of thing and should not run together.
        layout.addSpacing(12)

        from gui import theme as _theme
        self.summary_label = QLabel("Select a ZIP file to see what it holds.")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.RichText)
        # This block IS the decision the user makes ("new trip or update?"), so
        # it must be as readable as the body text.
        self.summary_label.setStyleSheet(f"color: {_theme.INK_SOFT};")
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.summary_label)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.status_label)
        # Soak up the leftover height HERE, so it never opens up as a gap
        # between a label and its own field.
        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.import_btn = QPushButton("Import")
        self.import_btn.setEnabled(False)
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.import_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.import_btn.clicked.connect(self._on_import)
        self.close_btn.clicked.connect(self.close)
        self.zip_field.textChanged.connect(self._inspect)

    def _browse_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Trip ZIP", "", "ZIP files (*.zip);;All files (*)")
        if path:
            self.zip_field.setText(path)

    def _dest_path(self):
        trips_root = get_trips_root()
        if not trips_root or not self._manifest:
            return None
        folder = self._manifest.get("folder") or Path(self.zip_field.text()).stem
        return str(Path(trips_root) / folder)

    def _inspect(self):
        """Read the manifest and say exactly what Import will do — including
        whether this is a new trip or an update, and where the pictures belong."""
        from engine.trip_transfer import inspect_zip
        from utils.config import get_currentpictures_root
        from utils.trip_identity import resolve
        from gui import theme as _theme

        self._manifest = None
        self.import_btn.setEnabled(False)
        zip_path = self.zip_field.text().strip()
        if not zip_path or not os.path.isfile(zip_path):
            self.summary_label.setText("Select a ZIP file to see what it holds.")
            return
        try:
            man = inspect_zip(zip_path)
        except Exception as e:
            self.summary_label.setText(f"<span style='color:{_theme.ALERT};'>{e}</span>")
            return
        self._manifest = man

        # This label is RichText, so every value interpolated into it must be
        # escaped: a Windows path or a placeholder like <CurrentPictures root>
        # is silently EATEN by Qt's HTML parser, which is how the pictures line
        # lost its first half.
        from html import escape as _e

        dest = self._dest_path()
        lines = [
            f"<b>{_e(man.get('title_nl') or man.get('trip_name', '?'))}</b> "
            f"({_e(man.get('fromdate', '?'))} – {_e(man.get('todate', '?'))})",
            f"Exported from <b>{_e(man.get('machine', '?'))}</b> on {_e(man.get('created', '?'))}.",
        ]

        # An existing folder holding a DIFFERENT trip is the one case we refuse:
        # it would overwrite unrelated work.
        self._blocked = False
        if not dest:
            lines.append(
                f"<span style='color:{_theme.ALERT};'><b>Cannot import.</b> No Trips root is "
                "configured — set it in Settings ▸ Repositories first.</span>")
            self._blocked = True
        elif os.path.isdir(dest):
            existing = resolve(dest, kind="trip")
            if existing.trip_id and man.get("trip_id") and \
                    existing.trip_id != man.get("trip_id"):
                lines.append(
                    f"<span style='color:{_theme.ALERT};'><b>Cannot import.</b> "
                    f"{_e(dest)} already holds a different trip "
                    f"({_e(existing.trip_id)}), not {_e(man.get('trip_id', ''))}.</span>")
                self._blocked = True
            else:
                lines.append(
                    f"<b>Updating</b> the trip already in {_e(dest)}. Settings and images "
                    f"are replaced, the species list and translations are merged, and "
                    f"the files being replaced go to its <tt>.trash</tt> folder.")
        else:
            lines.append(f"<b>New trip</b> — will be created in {_e(dest)}.")

        carried = []
        if man.get("_has_collection"):
            carried.append("species list")
        if man.get("_has_baseline"):
            carried.append("lifer status")
        if man.get("_has_transcache"):
            carried.append("translations")
        if man.get("_images"):
            carried.append(f"{len(man['_images'])} image(s)")
        lines.append("Carries: " + (", ".join(carried) if carried else "settings only") + ".")

        published = man.get("published", "unknown")
        lines.append({
            "yes": "🌐 This trip <b>is published</b> — the published trip list will be "
                   "refreshed from the website.",
            "no": "🌐 Not published. Nothing is uploaded.",
            "unknown": "🌐 Publication state was unknown at export; nothing is uploaded.",
        }[published])

        source_days = _as_int(man.get("day_count"))
        if not source_days:
            # A trip prepared in advance has no days yet — say so plainly and
            # ask for nothing. Recommending a copy here sends the user looking
            # for folders that were never supposed to exist.
            lines.append("📁 No day folders on the source machine yet — "
                         "nothing to copy.")
        elif man.get("picture_root"):
            rel = man["picture_root"].replace("/", os.sep)
            cp_root = get_currentpictures_root()
            if cp_root:
                lines.append(
                    f"📁 Pictures belong in {_e(os.path.normpath(os.path.join(cp_root, rel)))} "
                    f"({source_days} day folders on the source machine).")
            else:
                # Without this setting the trip's stored, portable picture path
                # cannot be turned into a real folder on THIS machine — so say
                # that, instead of printing a placeholder that reads like a path.
                lines.append(
                    f"<span style='color:{_theme.WARN};'>📁 This trip's pictures live in "
                    f"<b>{_e(rel)}</b>, but the CurrentPictures root is not set on this "
                    f"machine, so they cannot be found. Set it in "
                    f"Settings ▸ Repositories.</span>")

        self.summary_label.setText("<br>".join(lines))
        self.import_btn.setEnabled(not self._blocked)

    def _on_import(self):
        import configparser as _cp
        import time as _time
        from engine.trip_transfer import (restore_identity, merge_species_collection,
                                          restore_baseline, merge_transcache,
                                          pull_trip_admin)
        from utils.batch_config import generate_svconfig_ini
        from utils.trip_identity import resolve_trip_picture_root
        from utils.config import get_currentpictures_root

        zip_path = self.zip_field.text().strip()
        dest = self._dest_path()
        if not zip_path or not os.path.isfile(zip_path) or not dest or self._blocked:
            self.status_label.setText("Select a valid ZIP file first.")
            return

        if not _config.has_section("utilities"):
            _config.add_section("utilities")
        _config.set("utilities", "trip_import_zip", zip_path)
        persist_config()          # remembered for the next import

        is_update = os.path.isdir(dest) and os.path.isfile(os.path.join(dest, "trip.ini"))
        if is_update:
            answer = QMessageBox.question(
                self, "Update this trip?",
                f"{dest}\n\nalready holds this trip.\n\n"
                "Its settings and images will be replaced, and its species list and "
                "translations merged with the ones in the ZIP. The replaced files are "
                "kept in the trip's .trash folder — nothing is deleted.\n\nProceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self.status_label.setText("Import cancelled.")
                return

        t0 = _time.time()
        notes = []
        try:
            # 1. trip.ini + images FIRST — sv1MakeReposity reads trip.ini, exactly
            #    as Create Trip writes it before running the batch.
            restored, backups = restore_identity(zip_path, dest)
            self.status_label.setText("Restored the trip settings; building the folders…")
            QApplication.processEvents()

            # 2. Build the repository — but in RESTORE mode, so it neither re-seeds
            #    the species list/baseline from this machine's master nor rewrites
            #    the master's shared published-trip list.
            if not is_update:
                if not _config.has_section("utilities"):
                    _config.add_section("utilities")
                _config.set("utilities", "trip_restore_mode", "yes")
                try:
                    generate_svconfig_ini()
                    module_config = _cp.ConfigParser()
                    module_config.read("svconfig.ini")
                    module_config.set("repository", "BiotaDatabase", dest)
                    # No web root → sv1's whole SFTP block is skipped. Importing a
                    # trip must never touch the website.
                    module_config.set("repository", "BiotaWebRoot", "")
                    import batch.sv1MakeReposity as sv1
                    sv1.main(None, module_config)
                finally:
                    _config.set("utilities", "trip_restore_mode", "no")
                    persist_config()

            # 3. The payload: species list (ZIP wins), lifer baseline (never
            #    overwritten), translations (verified entries protected).
            sc = merge_species_collection(zip_path, dest, backup=is_update)
            if sc:
                if is_update and sc["overridden"]:
                    notes.append(f"Species list merged: {sc['total']} entries "
                                 f"({sc['kept_local']} kept from this machine, "
                                 f"{sc['overridden']} replaced by the ZIP).")
                else:
                    notes.append(f"Species list restored: {sc['total']} entries.")
            base = restore_baseline(zip_path, dest)
            notes.append({"restored": "Lifer status restored.",
                          "kept": "Kept the lifer status already here.",
                          "absent": "No lifer status in the ZIP — Count Species will "
                                    "use the trip's start date."}[base])
            tc = merge_transcache(zip_path, dest)
            if tc:
                kept = (f", {tc['kept_verified']} checked translations kept"
                        if tc["kept_verified"] else "")
                notes.append(f"Translations merged: {tc['total']} entries "
                             f"({tc['added']} new{kept}).")

            # 4. Publication — pull only, never push.
            if (self._manifest or {}).get("published") == "yes":
                rows, msg = pull_trip_admin(
                    _config.get("websettings", "base_url", fallback=""),
                    (self._manifest.get("master_web_root")
                     or _config.get("master", "masterbiotawebroot", fallback="")),
                    _config.get("master", "masterbiotadatabase", fallback=""))
                notes.append(msg)

            # 5. Make it the active trip, exactly as Select does in Create Trip.
            trip_data = read_trip_ini(dest) or {}
            web_slug = trip_data.get("web_slug") or trip_data.get("trip_name", "")
            web_root = web_slug if web_slug.startswith("/trips/") else f"/trips/{web_slug}"
            picture_root = resolve_trip_picture_root(dest, kind="trip",
                                                     active_picturedirectory="")
            first_day = _first_day_under(picture_root) if picture_root else ""
            _config.set("trip", "tripsrootdirectory", str(Path(dest).parent))
            set_trip_settings(dest, web_root, first_day or picture_root or "")
            update_cache_gui_section("trip_project_map", web_root,
                                     first_day or picture_root or "")
        except Exception as e:
            self.logger.warning(f"Import Trip failed: {e}")
            self.status_label.setText(f"❌ Import failed: {e}")
            return

        # 6. The pictures. Not having them yet is the normal order of events, so
        #    say what to do rather than reporting an error. NOTE `notes` is PLAIN
        #    text throughout — it goes to the log as well as to a RichText label,
        #    and pre-escaping it here put a literal "&amp;" in the log.
        rel = (trip_data.get("picture_root") or "").replace("/", os.sep)
        cp_root = get_currentpictures_root()
        source_days = _as_int((self._manifest or {}).get("day_count"))
        if not source_days:
            # Nothing was shot on the source machine, so there is nothing to
            # fetch and nothing to summarize. Silence beats a false to-do.
            notes.append("This trip has no day folders yet — nothing to copy. "
                         "Read the pictures in when you have them.")
        elif not cp_root and rel:
            # The real reason the pictures can't be located: this machine has no
            # CurrentPictures root, so the trip's portable relative path cannot
            # be resolved. Naming the setting beats printing a placeholder.
            notes.append(f"⚠ This trip's pictures live in {rel}, but the CurrentPictures "
                         f"root is not set on this machine, so they cannot be found. "
                         f"Set it in Settings ▸ Repositories, then re-select the trip.")
        elif not picture_root or not os.path.isdir(picture_root):
            notes.append(f"⚠ The {source_days} day folders are not here yet — copy them to "
                         f"{os.path.normpath(os.path.join(cp_root, rel))}, "
                         f"then run Read & Summarize.")
        elif not first_day:
            notes.append(f"⚠ {picture_root} holds no day folders yet — copy them there, "
                         f"then run Read & Summarize.")
        else:
            notes.append(f"Pictures found in {picture_root}. "
                         f"Next step: Read & Summarize, to rebuild the day data from them.")

        populate_trip_overview(self.passed_parent)
        self.passed_parent.trip_panel.rebuild()
        self.passed_parent.rebuild_menu()

        elapsed = _time.time() - t0
        verb = "Updated" if is_update else "Imported"
        self.logger.info(f"{verb} trip into {dest} in {elapsed:.1f}s. " + " ".join(notes))
        from html import escape as _e
        self.status_label.setTextFormat(Qt.TextFormat.RichText)
        self.status_label.setText(
            f"✅ {verb} in {elapsed:.1f}s.<br>" + "<br>".join(_e(n) for n in notes))


class InstallSmallMasterDialog(QDialog):
    """Extract a smallmaster.zip to the configured master database directory.

    Contents of the ZIP:
      biota/          JPG and XMP species images — what the viewer displays
      metadata/active/
        data_files.csv       EXIF data for every master file (camera, GPS, lens…)
        data_pictures.csv    Species rating data (rating, colour, which files belong
                             to which picture)
        species.csv          Species list with picture counts
        speciescollection*.csv  Trip-specific species checklists
    All four are required; without metadata/active/ the master DB cannot be built.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Install Small Master Database")
        self.resize(680, 400)

        layout = QVBoxLayout(self)

        info = QLabel(
            "<b>Install SmallMaster from a ZIP file</b><br>"
            "Extracts <tt>biota/</tt> (JPG + XMP species images) and "
            "<tt>metadata/active/</tt> (data_files, data_pictures, species, "
            "speciescollection…) to the configured master directory.  "
            "Existing files are overwritten; nothing else is removed.  "
            "The master database is rebuilt automatically on next launch."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # ZIP file picker
        zip_row = QHBoxLayout()
        zip_row.addWidget(QLabel("ZIP file:"))
        self.zip_field = QLineEdit()
        self.zip_field.setPlaceholderText("Select smallmaster.zip…")
        zip_row.addWidget(self.zip_field)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_zip)
        zip_row.addWidget(browse_btn)
        layout.addLayout(zip_row)

        # Extract target
        master_root = _config.get("master", "masterbiotadatabase", fallback="")
        layout.addWidget(QLabel("Extract to (master database directory):"))
        self.target_edit = QLineEdit(master_root or "(not configured — set MasterBiotaDatabase in settings)")
        self.target_edit.setReadOnly(bool(master_root))
        layout.addWidget(self.target_edit)

        # ZIP contents summary (populated when a file is chosen)
        self.contents_label = QLabel("Select a ZIP file to see its contents.")
        self.contents_label.setWordWrap(True)
        self.contents_label.setStyleSheet("color: #555; font-style: italic;")
        layout.addWidget(self.contents_label)

        self.delete_check = QCheckBox("Delete ZIP file after successful install")
        layout.addWidget(self.delete_check)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.install_btn = QPushButton("Install")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.install_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.install_btn.clicked.connect(self._on_install)
        self.close_btn.clicked.connect(self.close)
        self.zip_field.textChanged.connect(self._update_contents)

    def _browse_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SmallMaster ZIP", "", "ZIP files (*.zip);;All files (*)")
        if path:
            self.zip_field.setText(path)

    def _update_contents(self):
        import zipfile as _zf
        zip_path = self.zip_field.text().strip()
        if not zip_path or not os.path.isfile(zip_path):
            self.contents_label.setText("Select a ZIP file to see its contents.")
            return
        try:
            with _zf.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
            biota = sum(1 for n in names if n.startswith("biota/") and not n.endswith("/"))
            meta  = sorted(
                os.path.basename(n)
                for n in names
                if n.startswith("metadata/active/") and not n.endswith("/")
            )
            meta_str = ", ".join(meta[:6])
            if len(meta) > 6:
                meta_str += f" + {len(meta) - 6} more"
            size_mb = os.path.getsize(zip_path) / 1024 / 1024
            self.contents_label.setText(
                f"{biota:,} biota files (JPG/XMP)  |  "
                f"Metadata ({len(meta)}): {meta_str}  |  "
                f"ZIP: {size_mb:.0f} MB"
            )
        except Exception as e:
            self.contents_label.setText(f"Cannot read ZIP: {e}")

    def _on_install(self):
        import tempfile as _tmp
        import zipfile as _zf
        import time as _time
        zip_path = self.zip_field.text().strip()
        target   = self.target_edit.text().strip()
        if not zip_path or not os.path.isfile(zip_path):
            self.status_label.setText("Select a valid ZIP file first.")
            return
        if not target or target.startswith("(not configured"):
            self.status_label.setText("No target directory configured.")
            return
        os.makedirs(target, exist_ok=True)

        # Snapshot existing speciescollection.csv before the ZIP overwrites it.
        # Trip processing on this machine may have added species via svr5 that
        # the ZIP (built on another machine) doesn't know about yet.
        sc_path = os.path.join(target, "metadata", "active", "speciescollection.csv")
        existing_sc_content = None
        if os.path.isfile(sc_path):
            try:
                with open(sc_path, "r", encoding="utf-8") as _f:
                    existing_sc_content = _f.read()
            except Exception:
                pass

        t0 = _time.time()
        try:
            with _zf.ZipFile(zip_path, "r") as zf:
                members = zf.namelist()
                total = len(members)
                last_ui = t0
                for i, member in enumerate(members):
                    zf.extract(member, target)
                    now = _time.time()
                    if now - last_ui >= 0.5:
                        pct = 100 * i // total
                        self.status_label.setText(
                            f"Extracting… {i:,} / {total:,} files ({pct}%)"
                        )
                        QApplication.processEvents()
                        last_ui = now
        except Exception as e:
            self.status_label.setText(f"Extract failed: {e}")
            return
        elapsed = _time.time() - t0

        # Merge back any species that were in the existing collection but are
        # not in the ZIP (e.g. new insects added on this machine via svr5).
        merged_note = ""
        if existing_sc_content and os.path.isfile(sc_path):
            try:
                from engine.speciesclassification import SpeciesClassification
                sc_zip = SpeciesClassification(None)
                sc_zip.clearDict()
                sc_zip.readSpeciesCollection(sc_path, donotaddLanguageIndices=True)
                zip_keys = set(sc_zip.sc_dict.keys())

                with _tmp.NamedTemporaryFile(
                    mode="w", suffix=".csv", delete=False, encoding="utf-8"
                ) as tf:
                    tf.write(existing_sc_content)
                    tmp_path = tf.name
                try:
                    sc_existing = SpeciesClassification(None)
                    sc_existing.clearDict()
                    sc_existing.readSpeciesCollection(tmp_path, donotaddLanguageIndices=True)
                finally:
                    os.unlink(tmp_path)

                new_entries = [s for s in sc_existing.sc_dict if s not in zip_keys]
                if new_entries:
                    sc_zip.mergeSpeciesCollections(sc_existing.scCopy())
                    sc_zip.writeSpeciesCollection(sc_path)
                    merged_note = f"  Merged {len(new_entries)} local species entries into speciescollection."
            except Exception as e:
                merged_note = f"  (speciescollection merge skipped: {e})"

        # Rebuild the master DB immediately so it is ready on first use.
        db_note = ""
        try:
            from db.master_db import MasterDB
            self.status_label.setText(
                f"Extracted {total:,} files in {elapsed:.0f}s.{merged_note}  Building master database…"
            )
            QApplication.processEvents()

            def _db_progress(msg):
                self.status_label.setText(
                    f"Extracted {total:,} files in {elapsed:.0f}s.{merged_note}  {msg}"
                )
                QApplication.processEvents()

            MasterDB(target).rebuild(progress_cb=_db_progress)
            db_note = "  Master database built."
        except Exception as e:
            db_note = f"  (DB build skipped: {e})"

        # SmallMaster machines never hold the full RAW archive, so master writes
        # must be permanently blocked.  Write both keys and save now so the lock
        # survives even if the user never opens Repository Settings.
        _config.set("master", "readonly", "yes")
        _config.set("master", "readonly_locked", "yes")
        persist_config()

        msg = f"✅ Installed in {elapsed:.0f}s → {target}{merged_note}{db_note}"
        if self.delete_check.isChecked():
            try:
                os.remove(zip_path)
                msg += "  (ZIP deleted)"
            except Exception as e:
                msg += f"  (could not delete ZIP: {e})"
        self.status_label.setText(msg)
