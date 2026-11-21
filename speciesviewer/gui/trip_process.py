from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTabWidget, QWidget, QComboBox, QLineEdit, QFormLayout, QSizePolicy, QFileDialog, QTextEdit,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QStyledItemDelegate, QMessageBox, QApplication,
    QListWidget, QListWidgetItem)
from PyQt6.QtGui import QFont, QFontMetrics, QGuiApplication, QColor
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal
import logging
import os, shutil
from datetime import datetime
from pathlib import Path
import webbrowser
from gui.batch import BatchModuleRunner  # Assuming batch handler is here

from utils.config import set_trip_settings, _config, get_trips_root, persist_config
from utils.runtime_files import svconfig_path
from utils.trip_config import read_trip_ini
from widgets.small_widgets import HTMLLabel
from widgets.trip_widgets import (LabeledTextFieldQt, DateRangeFieldQt, DirectoryFieldQt, RecentDirectoryFieldQt, ImageFileFieldQt, image_field_row,
                                  DEFAULT_TRIP_PICTURE, DEFAULT_TRIP_BANNER)
from utils.gui_config import update_cache_gui_section, get_cache_gui_section
from gui.trip_overview import populate_trip_overview
from gui.link_insert import add_link_editing
from utils.day_folders import DAY_RE, strip_to_project_root, first_day_under

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

    # This writes the WHOLE file, so every [trip_config] key this dialog does not
    # own (tz_offset, picture_prefix, name_profile, retired_to_album, ...) must be
    # carried over — otherwise "Update Trip Settings" silently erases them, and a
    # lost picture_prefix orphans every sidecar on the next card read.
    _owned = {"master_repository_web", "master_repository_disk", "trip_banner",
              "trip_picture", "created", "trip_name", "trip_id", "web_slug",
              "picture_root", "fromdate", "todate", "oneblog"}
    carried = ""
    created = datetime.now().strftime('%Y-%m-%d %H:%M')
    if os.path.isfile(ini_path):
        from configparser import ConfigParser
        from utils.trip_config import coerce_orphan_continuations
        with open(ini_path, "rb") as f:
            raw = f.read()
        try:
            old_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            old_text = raw.decode("cp1252")
        old = ConfigParser(interpolation=None)
        old.read_string(coerce_orphan_continuations(old_text))
        if old.has_section("trip_config"):
            carried = "".join(f"{k} = {v}\n" for k, v in old.items("trip_config")
                              if k not in _owned)
            # an update is not a re-creation
            created = old.get("trip_config", "created", fallback="") or created

    ini_text = f"""\
[trip_config]
master_repository_web = {master_web}
master_repository_disk = {master_disk}
; the banner size is 2000x270 jpg, and the picture is 600x450 jpg
trip_banner = {banner_filename}
trip_picture = {picture_filename}
created = {created}
trip_name = {trip_name}
trip_id = {trip_id}
web_slug = {web_slug}
picture_root = {picture_root}
fromdate = {from_date}
todate = {to_date}
oneblog = day
{carried}
[trip_text]
title_nl = {_ini_escape(title_nl)}
message_nl = {_ini_escape(message_nl)}
title_en = {_ini_escape(title_en)}
message_en = {_ini_escape(message_en)}
"""

    try:
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(ini_text)
        logger.info("✅ Trip settings saved.")
        logger.debug(f"trip.ini written to {ini_path}")
        return
    except Exception as err:
        logger.error(f"❌ Could not write trip.ini ({ini_path}): {err}")
        return 

class TripCreationDialog(QDialog):
    def __init__(self, parent, menu_spec, mode="update"):
        super().__init__(parent)
        self.menu_spec = menu_spec
        self.logger = parent.logger
        self.passed_parent = parent
        # ONE screen, but never two jobs at once. "create" starts EMPTY and can only
        # write into a folder it makes itself; "update" loads the trip you picked and
        # can only write back into that one. The old single mode did both from fields
        # pre-filled with whichever trip happened to be active — which is how a live
        # trip's identity got overwritten. See docs/plans/trip_dialog_and_ingest_lanes.md.
        self.mode = mode if mode in ("create", "update") else "update"
        self.setWindowTitle("Create Trip" if self.mode == "create"
                            else "Select or Update Trip")
        self.resize(800, 600)

        # In create mode nothing is inherited: the active trip is not a template for
        # a new one, and a half-copied identity is worse than an empty form.
        trip_root = ("" if self.mode == "create"
                     else _config.get("trip", "tripbiotadatabase", fallback=""))
        trip_data = read_trip_ini(trip_root) or {}
  
        main_layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # Tab 1: Trip Folder
        tab1 = QWidget()
        layout1 = QVBoxLayout(tab1)
        if self.mode == "create":
            info_label = HTMLLabel(
                "<p><b>A trip</b> is the pictures of a holiday, an outing or any event, "
                "one folder per day.</p>"
                "<p>Type a name and the dates. SpeciesViewer suggests where everything "
                "goes; change a suggestion if you like. <b>Create Trip</b> makes the "
                "folders. The trip picture, the banner and the texts can follow later.</p>")
        else:
            info_label = HTMLLabel(
                "<p><b>A trip</b> is the pictures of a holiday, an outing or any event, "
                "one folder per day.</p>"
                "<p><b>This tab:</b> the trip's folders. Its web address is fixed "
                "once the trip is created.</p>")
        layout1.addWidget(info_label)
        self._layout1 = layout1
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
        # (The "New trip folder / Create Folder" row is gone: in create mode the
        # name panel suggests the folder and Create Trip makes it —
        # docs/plans/trip_create_naming_plan.md.)

        # The web root is the trip's identity — trip_name/web_slug are derived from
        # it — so in create mode it starts empty rather than carrying the last trip's.
        self.trip_web = LabeledTextFieldQt(
            "Trip Web root",
            "" if self.mode == "create" else _config.get("trip", "tripbiotawebroot", fallback=""),
            multiline=False, inline=True)
        self.trip_web.field.setMaximumWidth(300)
        trip_web_row = QWidget()
        trip_web_layout = QHBoxLayout(trip_web_row)
        trip_web_layout.setContentsMargins(0, 0, 0, 0)
        trip_web_layout.addWidget(self.trip_web)
        if self.mode != "create":
            # The web root IS the trip's public address: trip_name and web_slug
            # are cut from it, and the master trip index keys its row on that
            # slug. Retype it on an existing trip and the site keeps the old
            # folder and the old index row while the trip starts publishing to
            # a new one — a dead link on the public trip list that nothing in
            # the app can clean up afterwards, because "unadvertise" looks for
            # the NEW slug. It happened once (brazil2026db → brazil2026), and a
            # form field you fill in top to bottom is no place for it. Set at
            # creation; renaming later is a deliberate trip to Settings.
            from gui import theme
            self.trip_web.field.setReadOnly(True)
            self.trip_web.field.setStyleSheet(
                f"color: {theme.INK_FAINT}; background: transparent; "
                f"border: 1px solid {theme.LINE};")
            # Grey and unresponsive already says "you cannot type here"; a label
            # spelling out WHY answers a question nobody asked while filling in
            # a form. The reason waits in the tooltip for whoever goes looking.
            self.trip_web.field.setToolTip(
                "The website link is defined when the website is created.")
        trip_web_layout.addStretch()
        layout1.addWidget(trip_web_row)
        self.project_path = DirectoryFieldQt(
            "Trip pictures folder (contains the day subfolders)",
            "" if self.mode == "create" else _config.get("project", "picturedirectory", fallback=""))
        layout1.addWidget(self.project_path)
        if self.mode == "create":
            # The name panel (added once the date field exists, below) asks for
            # these; the fields stay as the values the save reads.
            for w in (self.root_field, trip_web_row, self.project_path):
                w.setVisible(False)
        tabs.addTab(tab1, "Name && Folders" if self.mode == "create" else "Trip Folders")

        # Tab 2: Images & Dates
        tab2 = QWidget()
        layout2 = QVBoxLayout(tab2)
        info_label21 = HTMLLabel(
            "<p><b>Trip image</b><br>"
            "This image appears in trip listings and should visually represent the trip.<br>"
            "You can update it later if a better photo becomes available.<br>"
            "Required dimensions: <b>600×450 pixels</b>.</p>"
        )

        self.trip_img_field = ImageFileFieldQt("Trip Image", (600, 450), self.root_field,
                                               default_file=DEFAULT_TRIP_PICTURE)
        self.trip_img_field.field.setText(trip_data.get("trip_picture", ""))
        layout2.addWidget(image_field_row(info_label21, self.trip_img_field))
        layout2.addWidget(self.trip_img_field)

        info_label22 = HTMLLabel(
            "<p><b>Web banner</b><br>"
            "This banner is displayed at the top of all blog and species pages for this trip.<br>"
            "It sets the visual tone for the trip’s web presence.<br>"
            "Required dimensions: <b>2000×270 pixels</b>.</p>"
        )

        self.topbar_img_field = ImageFileFieldQt("Top Web Bar", (2000, 270), self.root_field,
                                                 default_file=DEFAULT_TRIP_BANNER)
        self.topbar_img_field.field.setText(trip_data.get("trip_banner", ""))
        layout2.addWidget(image_field_row(info_label22, self.topbar_img_field))
        layout2.addWidget(self.topbar_img_field)

        info_label23 = HTMLLabel(
            "<p><b>Date range</b><br>"
            "Defines the expected days of the trip and helps generate accurate lists for the website.<br>"
            "You can adjust this range later to include additional days or refine the trip timeline.</p>"
        )

        if self.mode != "create":           # create: the dates sit beside the name
            layout2.addWidget(info_label23)
        # A new trip is planned ahead: start two weeks out, run for two weeks —
        # and moving the start drags the end along so the length is kept. An
        # existing trip's saved range is restored below without re-triggering that.
        self.date_field = DateRangeFieldQt(lead_days=14, span_days=14, follow_end=True)
        self.date_field.set_dates(trip_data.get("fromdate", ""), trip_data.get("todate", ""))
        if self.mode == "create":
            from gui.trip_name_panel import TripNamePanel
            self.name_panel = TripNamePanel(self.date_field)
            self._layout1.insertWidget(1, self.name_panel)
            self._layout1.addStretch()
        else:
            layout2.addWidget(self.date_field)
        from gui.range_check_view import check_range_row
        # Beside the dates it checks: on the first tab when creating.
        range_row = check_range_row(
            self, self.date_field, "trip",
            own_name=lambda: Path(self.trip_web.get_text().strip()).name)
        if self.mode == "create":
            self._layout1.insertLayout(2, range_row)
        else:
            layout2.addLayout(range_row)

        tabs.addTab(tab2, "Images" if self.mode == "create" else "Images && Dates")

        # Tab 3: Dutch, English, Create
        tab3 = QWidget()
        layout3 = QVBoxLayout(tab3)
        sub_tabs = QTabWidget()

        # Sub-tab: Dutch
        dutch_tab = QWidget()
        dutch_layout = QVBoxLayout(dutch_tab)
        self.title_nl_field = LabeledTextFieldQt("Trip title (Dutch)", trip_data.get("title_nl", ""))
        self.message_nl_field = LabeledTextFieldQt("Trip description (Dutch)", trip_data.get("message_nl", ""), multiline=True)
        add_link_editing(self.message_nl_field.field)   # Ctrl+K / right-click → <a href>
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
        add_link_editing(self.message_en_field.field)   # Ctrl+K / right-click → <a href>
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
        self.tabs = tabs

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
        self.create_button = create_button
        select_button = QPushButton("Select")

        # One job per screen: Create mode offers only Create, Update mode only the
        # two that act on the trip you loaded. A button that does the other job is
        # the whole reason this dialog could damage a live trip.
        btn_layout.addWidget(create_button)
        btn_layout.addWidget(write_tripini_button)
        btn_layout.addWidget(select_button)
        if self.mode == "create":
            write_tripini_button.setVisible(False)
            select_button.setVisible(False)
        else:
            create_button.setVisible(False)

        main_layout.addLayout(btn_layout)

        # Hook up buttons
        write_tripini_button.clicked.connect(self.on_write_tripini_clicked)
        create_button.clicked.connect(self.on_create_clicked)
        select_button.clicked.connect(self.on_select)

        # Every field now exists → load them FROM the folder the dialog is showing,
        # so what you read belongs to one trip. See the note at root_field. In
        # create mode there is nothing to load: the form stays empty until you name
        # the folder.
        if self.mode == "update":
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

    def _make_trip_folders(self) -> bool:
        """Create mode: check the name panel, make the trip's folders, and fill
        the (hidden) fields the save below reads. False = nothing made; the
        status line says why."""
        problems = self.name_panel.problems()
        if problems:
            self.tabs.setCurrentIndex(0)
            self.status_label.setText("❌ Not created: " + "; ".join(problems) + ".")
            return False
        data = self.name_panel.data_folder()
        pictures = self.name_panel.pictures_folder()
        try:
            data.mkdir(parents=True, exist_ok=True)
            pictures.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.status_label.setText(f"❌ Could not create the folders: {e}")
            return False
        self.logger.info(f"New trip folders: {data} and {pictures}")
        self.root_field.set_text(str(data))      # first: the picture fields check against it
        # The trip gets its own copy of the default picture and banner: the
        # blog and the trip index use them, and they are the files to replace
        # with real ones later (Gilbert, 2026-10-12).
        for default, field in ((DEFAULT_TRIP_PICTURE, self.trip_img_field),
                               (DEFAULT_TRIP_BANNER, self.topbar_img_field)):
            name = Path(default).name
            if not field.get_filename().strip():
                try:
                    if not (data / name).exists():
                        shutil.copy(default, data / name)
                    field.field.setText(name)
                except OSError as e:
                    self.logger.warning(f"Default picture not copied ({name}): {e}")
        self.trip_web.set_text("/trips/" + self.name_panel.slug())
        self.project_path.field.setText(str(pictures))
        # Titles default to the name; the Trip Text tab can say more later.
        title = self.name_panel.trip_name()
        title = title[:1].upper() + title[1:]
        for field in (self.title_nl_field, self.title_en_field):
            if not field.get_text().strip():
                field.set_text(title)
        return True

    def on_trip_root_changed(self, new_path):
        trip_data = read_trip_ini(new_path)
        if trip_data:
            # logger, not print: this now runs when the dialog OPENS, and a bare
            # print of an emoji dies with UnicodeEncodeError on a cp1252 console —
            # which would take the whole dialog down before it appears.
            self.logger.debug(f"trip.ini loaded: {new_path}")
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
            # The trip's pictures folder. trip.ini's picture_root is the truth; the
            # per-machine cache is only a fallback and goes STALE when the folder is
            # renamed ("2026 Brazilie" → "2026 Brazil"). A dead path here made every
            # "Update Trip Settings" fail validation on a tab you weren't looking at.
            from utils.trip_identity import resolve_trip_picture_root
            pictdir = resolve_trip_picture_root(new_path, "trip", active_picturedirectory="")
            if not os.path.isdir(pictdir):
                pictdir = get_cache_gui_section("trip_project_map", trip_name)
            if pictdir and os.path.isdir(pictdir):
                # [project] picturedirectory must stay a DAY (WP8)
                _config["project"]["picturedirectory"] = (
                    pictdir if DAY_RE.match(Path(pictdir).name)
                    else first_day_under(pictdir) or pictdir)
                self.project_path.field.setText(pictdir)
        else:
            self.logger.debug(f"No trip.ini at {new_path} — all tabs must be filled in.")

    def on_write_tripini_clicked(self):
        if not self.create_trip_ini():
            return
        self.logger.debug("Trip settings updated (trip.ini written; repository not rebuilt).")
        self.passed_parent.status_bar.showMessage("Trip settings updated.")
        self.status_label.setText("✅ Trip settings saved.")
        self._update_trip_on_site()

    def _update_trip_on_site(self):
        """Bring the website's trip list (title, text, picture, banner) in line
        with the trip.ini just saved — only for a trip that is ALREADY listed:
        saving settings must never publish a trip still in draft. The same
        step as Manage Trip Publication ▸ List on the site (svr4UpdateTrips)."""
        trip_dir = self.root_field.get_text()
        active = _config.get("trip", "tripbiotadatabase", fallback="").strip()
        if not active or Path(trip_dir).resolve() != Path(active).resolve():
            # svr4 works on the ACTIVE trip; another one would be published instead.
            self.logger.info("Website not updated: this is not the selected trip. "
                             "Select it, then Update Trip Settings again.")
            return
        from utils.trip_config import read_trip_ini
        from engine.publish_index import advertised_names
        trip_name = (read_trip_ini(trip_dir) or {}).get("trip_name", "")
        names = advertised_names(_config.get("websettings", "base_url", fallback=""),
                                 _config.get("master", "masterbiotawebroot", fallback="/nature/"))
        if names is None:
            self.logger.warning("Website not updated: the site could not be reached.")
            return
        if trip_name not in names:
            self.logger.info("This trip is not on the website's trip list, so only "
                             "trip.ini was saved. Trip ▸ Manage Trip Publication "
                             "puts it on the site.")
            return
        import batch.svr4UpdateTrips
        from gui.menu import MenuSpec
        spec = MenuSpec(callback=None, batch_parms={
            "statusname":    "",
            "module_called": batch.svr4UpdateTrips.main,
            "before_msg":    "Updating the trip on the website…",
            "after_msg":     "✅ Trip settings saved and the website updated.",
            "virttrip_mode": False,
        })
        BatchModuleRunner(self, self.passed_parent).run(spec)

    def _refuse_save(self, reason: str) -> bool:
        """A refused save must be SEEN: the red hint sits on the first tab, and
        you are usually on another one — it used to fail with no trace at all."""
        self.tabs.setCurrentIndex(0)
        self.status_label.setText(f"❌ Not saved: {reason}.")
        self.logger.warning(f"Trip settings not saved: {reason}.")
        return False

    def _picturedirectory_for_config(self) -> str:
        """The value to store in [project] picturedirectory — always a DAY folder.
        The 'Trip pictures folder' field may now hold the trip root, so keep it
        only if it is already a day, otherwise map it to the first day under it
        (falling back to the field value if no day exists yet). WP8."""
        field = self.project_path.get_path()
        if field and DAY_RE.match(Path(field).name):
            return field
        return first_day_under(field) or field

    def on_create_clicked(self):
        # Create makes its own folder as part of saving. Without this the root field
        # still pointed at whatever it pointed at — the live-trip overwrite — because
        # "Create Folder" was a separate step nothing forced you to take.
        if self.mode == "create" and not self._make_trip_folders():
            return
        # Abort if trip root or project pictures folder is invalid — otherwise the
        # batch below would build a trip pointing at a non-existent picture dir.
        if not self.create_trip_ini():
            return
        # make sure new structure is created
        pd = self._picturedirectory_for_config()
        from utils.config import root_is_unset
        if root_is_unset("trip", "tripsrootdirectory"):
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
            if self.mode == "create":
                # Done: a second press would only say "already exists".
                self.create_button.setEnabled(False)
                self.create_button.setText("Created ✓")
                self.name_panel.setEnabled(False)
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=_on_created)

    def on_select(self):
        # A trip belongs to one master: never open another master's in place.
        from engine.trip_owner import foreign_owner, refusal_text
        trip_dir = self.root_field.get_text()
        owner = foreign_owner(trip_dir, "trip",
                              _config.get("master", "masterbiotadatabase", fallback=""))
        if owner:
            QMessageBox.warning(self, "Select Trip", refusal_text(trip_dir, "trip", owner))
            return
        # save settings
        pd = self._picturedirectory_for_config()
        from utils.config import root_is_unset
        if root_is_unset("trip", "tripsrootdirectory"):
            _config.set("trip", "tripsrootdirectory", str(Path(self.root_field.get_text()).parent))
        set_trip_settings(self.root_field.get_text(), self.trip_web.field.text(), pd)
        update_cache_gui_section("trip_project_map", self.trip_web.field.text(), pd)
        populate_trip_overview(self.passed_parent)
        self.accept()  # ✅ Actually closes the dialog

    def translate_dutch_to_english(self):
        nl_title = self.title_nl_field.get_text()
        nl_message = self.message_nl_field.get_text()

        # Force NL->EN; auto-detection misfires on short proper-noun titles.
        tr = self.passed_parent.translator
        tr.last_error = None
        translated_title = tr.cachedTranslate(nl_title, language='nl')
        translated_message = tr.cachedTranslate(nl_message, language='nl')

        self.title_en_field.set_text(translated_title)
        self.message_en_field.set_text(translated_message)

        self._report_translation(tr, "Dutch to English")

    def translate_english_to_dutch(self):
        en_title = self.title_en_field.get_text()
        en_message = self.message_en_field.get_text()

        # Force EN->NL; auto-detection misfires on short proper-noun titles.
        tr = self.passed_parent.translator
        tr.last_error = None
        translated_title = tr.cachedTranslate(en_title, language='en')
        translated_message = tr.cachedTranslate(en_message, language='en')

        self.title_nl_field.set_text(translated_title)
        self.message_nl_field.set_text(translated_message)

        self._report_translation(tr, "English to Dutch")

    def _report_translation(self, tr, direction: str):
        """✅ only when the live call actually succeeded — cachedTranslate
        swallows a network failure and hands back the original text, so an
        unconditional success line was reporting a no-op as done."""
        if tr.last_error:
            self.logger.warning(
                f"⚠ Translation incomplete ({direction}) — Google Translate "
                f"returned nothing; kept the original text. ({tr.last_error})")
        else:
            self.logger.info(f"✅ Translated {direction}.")

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
            return self._refuse_save("the trip folder does not exist")
        else:
            self.root_field.clear_hint()

        # The web root is the trip's address on its website — on this computer or
        # on a server, SpeciesViewer always publishes. Empty, the trip records no
        # name or web address and its pages land at the top of the site (found
        # 2026-10-11). The folder pick pre-fills /trips/<folder>; keep it filled.
        if not Path(trip_name.strip()).name:
            folder = os.path.basename(os.path.normpath(trip_root))
            return self._refuse_save(
                f"the trip needs a web root — for example /trips/{folder}")

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
                    f"To make a NEW trip instead: close this and use "
                    f"Trips ▸ Create Trip.\n\n"
                    f"Replace the settings of “{shown}”?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                    ) != QMessageBox.StandardButton.Yes:
                self.root_field.show_hint(
                    "Pick an empty folder, or make a new trip with Trips ▸ Create Trip.")
                return False
            self.logger.warning(
                f"Trip settings of '{existing_name}' in {folder} replaced by "
                f"'{new_name}' (confirmed by the user).")

        project_dir = self.project_path.get_path()
        if not project_dir or not os.path.isdir(project_dir):
            self.project_path.show_hint("Please select a valid trip pictures folder.")
            return self._refuse_save(
                f"the trip pictures folder does not exist ({project_dir or 'empty'})")
        else:
            self.project_path.clear_hint()

        # WP8: record the trip's picture ROOT in trip.ini. The field may point at
        # a day folder (historic) or the root; strip a trailing day/cache segment
        # so we always store the root that holds the YYYY-MM-DD subdirs.
        picture_root_abs = strip_to_project_root(project_dir)

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
        )
        return True

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
        read_ini(module_config, svconfig_path())

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
        read_ini(module_config, svconfig_path())

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

    # The highlight colours are fixed pastels, so the text on them must be dark
    # in BOTH themes — in dark mode the palette's light-grey text vanished on
    # the light yellow of a newly merged row (found 2026-10-14).
    _INK_ON_PASTEL = QColor(40, 36, 30)

    def paint(self, painter, option, index):
        color = self.cell_colors.get((index.row(), index.column()))
        if color:
            from PyQt6.QtGui import QPalette
            from PyQt6.QtWidgets import QStyleOptionViewItem
            painter.fillRect(option.rect, color)
            option = QStyleOptionViewItem(option)
            option.palette.setColor(QPalette.ColorRole.Text, self._INK_ON_PASTEL)
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
            # on_save writes back exactly these columns (Sequence, SubspeciesOf, …)
            self._fieldnames = list(reader.fieldnames or [])

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
        """Write the list back with every column it was read with, via a temp
        file swapped in only when complete. A hard-coded 6-column header used to
        crash on Sequence/SubspeciesOf AFTER open("w") had emptied the file —
        that wiped the master list (2026-11-12). Any failure now leaves the
        file as it was."""
        import csv
        if not self.all_rows:
            QMessageBox.warning(self, "Save species list",
                                "The list is empty — nothing was saved.")
            return
        headers = list(getattr(self, "_fieldnames", []) or [])
        for row in self.all_rows:                 # never drop a column a row has
            headers += [k for k in row if k is not None and k not in headers]
        tmp = self.csv_path + ".tmp"
        try:
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers, quoting=csv.QUOTE_MINIMAL,
                                        delimiter=";")
                writer.writeheader()
                writer.writerows(self.all_rows)
            from utils.atomic_replace import replace_with_retry
            replace_with_retry(tmp, self.csv_path)
        except Exception as e:
            logging.getLogger("speciesviewer").error(
                f"Saving the species list failed, file left unchanged: {e}", exc_info=True)
            QMessageBox.warning(self, "Save species list",
                                f"Could not save — the species list was left unchanged.\n\n{e}")
            return
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
from utils.ini_io import read_ini

class CreateBlogDialog(QDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Configure Blog Post")
        self.resize(760, 620)       # room for the cover picker's thumbnails

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
            read_ini(self.parser, self.ini_path)
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

        # No separate Save: closing without it lost the text, often (his
        # report, 2026-10-21). Close saves; the window's X / Esc asks first.
        create_btn = QPushButton("Create Blog")
        close_btn = QPushButton("Close")
        close_btn.setToolTip("Save the text and settings, then close")

        btn_layout.addWidget(create_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        create_btn.clicked.connect(self.on_create_blog)
        close_btn.clicked.connect(self._save_and_close)
        self._saved = self._values()       # what blog.ini holds now

    def get_blog_url(self):
        from utils import site_config
        base_url = site_config.site_base() + "/"
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
            "column_width": "300",
            "max_per_species": "1",
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
            tr = self.passed_parent.translator
            tr.last_error = None
            translated_title = tr.cachedTranslate(nl_title, language='nl')
            translated_message = tr.cachedTranslate(nl_message, language='nl')

            self.title_en.setText(translated_title)
            self.message_en.setPlainText(translated_message)

            self._report_translation(tr, "Dutch to English")
        except Exception as e:
            self.logger.error(f"❌ Translation failed: {e}")

    def translate_english_to_dutch(self):
        try:
            en_title = self.title_en.text().strip()
            en_message = self.message_en.toPlainText().strip()

            # Force the source language (this button is always EN->NL); see the
            # NL->EN handler for why auto-detection is unreliable on short titles.
            tr = self.passed_parent.translator
            tr.last_error = None
            translated_title = tr.cachedTranslate(en_title, language='en')
            translated_message = tr.cachedTranslate(en_message, language='en')

            self.title_nl.setText(translated_title)
            self.message_nl.setPlainText(translated_message)

            self._report_translation(tr, "English to Dutch")
        except Exception as e:
            self.logger.error(f"❌ Translation failed: {e}")

    def _report_translation(self, tr, direction: str):
        """✅ only when the live call actually succeeded — cachedTranslate
        swallows a network failure and returns the original text."""
        if tr.last_error:
            self.logger.warning(
                f"⚠ Translation incomplete ({direction}) — Google Translate "
                f"returned nothing; kept the original text. ({tr.last_error})")
        else:
            self.logger.info(f"✅ Translated {direction}.")


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
        # setPlainText, never QTextEdit(text): given text in the constructor Qt
        # GUESSES rich text, so a saved <a href> came back as a rendered link
        # and toPlainText() then saved it without the tag (link lost).
        self.message_nl = QTextEdit()
        self.message_nl.setPlainText(self.parser.get("blog_text", "message_nl", fallback=""))
        self.message_nl.setAcceptRichText(False)  # plain text — stored in blog.ini
        add_link_editing(self.message_nl)   # Ctrl+K / right-click → <a href>

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
        self.message_en = QTextEdit()
        self.message_en.setPlainText(self.parser.get("blog_text", "message_en", fallback=""))
        self.message_en.setAcceptRichText(False)  # plain text — stored in blog.ini
        add_link_editing(self.message_en)

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

        # More than one picture of a species on a day you photographed it a
        # lot (2026-10-21). Per sex / age / plumage, as the one picture was.
        self.max_per_species = QSpinBox()
        self.max_per_species.setRange(1, 20)
        self.max_per_species.setValue(
            self.parser.getint("blog_config", "max_per_species", fallback=1))
        self.max_per_species.setToolTip(
            "How many pictures of each species the blog shows — the best-rated "
            "ones, in the order they were taken.\nCounted separately for male / "
            "female, adult / juvenile and plumage, so 2 can show 2 males and "
            "2 females.")

        form.addRow("Minimum picture rating (1–200):", self.minimum_score)
        form.addRow("Minimum picture width (px):", self.column_width)
        form.addRow("Pictures per species:", self.max_per_species)

        # The picture that stands for this day on the blog index (blogjpg).
        from gui.blog_cover_picker import BlogCoverPicker
        self.cover_picker = BlogCoverPicker(
            _config.get("trip", "tripbiotadatabase", fallback="").strip(),
            Path(_config["project"]["picturedirectory"]).name,
            self.parser.get("blog_selection", "species", fallback="biota"),
            self.parser.get("blog_text", "blogjpg", fallback=""))
        form.addRow(QLabel("Picture on the blog index:"))
        form.addRow(self.cover_picker)

        self.tabs.addTab(tab, "Configuration")

    def _values(self):
        """Everything this dialog writes to blog.ini — compared on close."""
        return (self.title_nl.text(), self.message_nl.toPlainText(),
                self.title_en.text(), self.message_en.toPlainText(),
                self.minimum_score.value(), self.column_width.value(),
                self.max_per_species.value(), self.cover_picker.choice())

    def _save_and_close(self):
        if self._values() != self._saved:
            self.save_ini()
        self.accept()

    def reject(self):
        """The window's X and Esc. Unsaved text is the user's work: ask."""
        if self._values() == self._saved:
            return super().reject()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Configure Blog Post")
        box.setText("The blog text or settings were changed. Save them?")
        box.setStandardButtons(QMessageBox.StandardButton.Save
                               | QMessageBox.StandardButton.Discard
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            self.save_ini()
            super().accept()
        elif answer == QMessageBox.StandardButton.Discard:
            super().reject()
        # Cancel: stay open

    def save_ini(self):
        self.parser["blog_text"]["titel_nl"] = self.title_nl.text()
        self.parser["blog_text"]["message_nl"] = self.message_nl.toPlainText()
        self.parser["blog_text"]["titel_en"] = self.title_en.text()
        self.parser["blog_text"]["message_en"] = self.message_en.toPlainText()

        self.parser["blog_config"]["minimum_score"] = str(self.minimum_score.value())
        self.parser["blog_config"]["column_width"] = str(self.column_width.value())
        self.parser["blog_config"]["max_per_species"] = str(self.max_per_species.value())
        cover = self.cover_picker.choice()
        if cover:
            self.parser["blog_text"]["blogjpg"] = cover
        else:
            self.parser.remove_option("blog_text", "blogjpg")

        with open(self.ini_path, "w", encoding="utf-8") as f:
            self.parser.write(f)

        self._saved = self._values()
        self.logger.info(f"✅ Blog text saved ({self.ini_path}).")
        self.passed_parent.status_bar.showMessage("Blog text saved.")

    def on_create_blog(self):
        try:
            self.save_ini()  # Ensure latest edits are saved

            def open_blog_in_browser():
                # The build may have chosen other pictures: show those.
                self.cover_picker.reload()
                self._saved = self._values()
                if self.open_after_checkbox.isChecked():
                    webbrowser.open(self.get_blog_url())

            runner = BatchModuleRunner(self, self.passed_parent)
            runner.run(self.menu_spec, on_success=open_blog_in_browser)
        except Exception as e:
            self.logger.error(f"❌ Blog creation failed: {e}")
            self.status_label.setText("❌ Blog creation failed.")
            self.passed_parent.status_bar.showMessage("Blog creation failed.")        


class BaseTripModuleDialog(QDialog):
    def __init__(self, parent, menu_spec, title, info_text, subdirectory=True,
                 independent=False, picture=None):
        # independent=True makes this a window in its own right: minimize and
        # maximize buttons, its own taskbar entry, and no Qt parent — so it does
        # NOT float above the main window and can stay open next to the Trip
        # overview. The price is lifetime: with no Qt parent, the CALLER must
        # hold a reference or Python collects the dialog the moment show()
        # returns. passed_parent still points at the main window, which is what
        # BatchModuleRunner and the overview refreshes actually need.
        super().__init__(None if independent else parent)
        self.setWindowTitle(title)
        if independent:
            self.setWindowFlags(Qt.WindowType.Window
                                | Qt.WindowType.WindowMinimizeButtonHint
                                | Qt.WindowType.WindowMaximizeButtonHint
                                | Qt.WindowType.WindowCloseButtonHint)
            self.setSizeGripEnabled(True)
        self.resize(600, 250)

        self.logger = logging.getLogger("speciesviewer")
        self.passed_parent = parent    
        self.menu_spec = menu_spec      
        self.subdirectory = subdirectory             

        full_path = Path(_config["project"]["picturedirectory"])
        self.base_path = full_path.parent
        self.default_subdir = full_path.name
        self.trip_root = _config["trip"]["tripbiotadatabase"]

        # `picture` (assets/wait_<name>.jpg) sits on the left of the whole dialog,
        # as in the Setup Checklist. Then the fields live in a column beside it,
        # and a subclass inserts into self.body_layout — self.layout() is the row.
        pic = None
        if picture:
            from gui.wait_picture import picture_label
            pic = picture_label(picture, 220, self.devicePixelRatioF())
        if pic is not None:
            row = QHBoxLayout(self)
            row.setContentsMargins(20, 20, 20, 20)
            row.setSpacing(24)
            row.addWidget(pic, alignment=Qt.AlignmentFlag.AlignVCenter)
            layout = QVBoxLayout()
            # Its own line spacing: a nested layout INHERITS its parent's, so the
            # 24 px between bird and fields also opened up every line (2026-10-20).
            layout.setSpacing(6)
            row.addLayout(layout, 1)
            self.resize(900, 260)       # height grows to fit: picture + fields
        else:
            layout = QVBoxLayout(self)
        self.body_layout = layout

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
        # Kept so a subclass can put a result line left of the buttons.
        self.btn_layout = btn_layout

        self.write_btn = QPushButton("Run Step")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.write_btn)
        btn_layout.addWidget(self.close_btn)
        if pic is not None:
            # Spare height goes above the buttons, not between the lines: beside a
            # tall picture the column is often shorter than the row, and Qt would
            # otherwise spread the difference over every gap (2026-10-20).
            layout.addStretch(1)
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
            subdirectory=False,
            picture="toucan",
        )

    def on_write(self):
        _close_picture_windows()
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=lambda: populate_trip_overview(self.passed_parent))


def _close_picture_windows() -> int:
    """Close every open picture window before Reset Trip clears the day data
    under them. Left open, a day viewer kept writing a day CSV that no longer
    existed ("could not persist summary", 0.5.0 release test). Closing first also
    lands their pending colour/rating writes. Large views and Quick views go
    before the grids they were opened from. Returns how many were closed."""
    from PyQt6.QtWidgets import QApplication
    from gui.image_viewer import ImagePreviewDialog
    from gui.large_image_view import LargeImageView
    from gui.quick_viewer import QuickViewer
    from gui.rating_editor import RatingEditorDialog
    windows = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
    order = (QuickViewer, LargeImageView, ImagePreviewDialog, RatingEditorDialog)
    closed = 0
    for cls in order:
        for w in windows:
            if isinstance(w, cls) and w.isVisible():
                w._suppress_rerun = True      # no "re-run Read & Summarize?" now
                w.close()
                closed += 1
    if closed:
        logging.getLogger("speciesviewer").info(
            f"Closed {closed} picture window(s) before the reset.")
    return closed


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
    # Width in characters BEFORE a report exists — the window opens empty and is
    # refitted to the real thing by _fit_to_report() the moment one loads. Wide
    # enough for the legend block, so the empty window is not a slot.
    _REPORT_COLS = 110
    # Dialog margins + the viewer's frame + the vertical scrollbar (the report is
    # always long enough to have one). Measured against a real window rather than
    # guessed: 90 left a visible strip of dead space down the right.
    _REPORT_CHROME_PX = 70
    # Air past the longest line, so the text does not touch the scrollbar. Small
    # on purpose — the report is monospace, so the longest line IS the width;
    # anything more is empty window.
    _REPORT_SLACK = 1.01

    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Count Species",
            info_text="Count the different number of species picture in the trip.",
            subdirectory=False,
            independent=True,
        )

        # The report is monospace and never wraps, so the window it wants is a
        # CHARACTER COUNT, not a fraction of the screen. Sized to the screen it
        # was right on a laptop and far too wide on a 4K desktop — the same 90%
        # meaning two different things. _REPORT_COLS is what the report actually
        # needs, measured in the viewer's OWN font so it holds at any DPI or
        # display scaling, and capped below so it can never exceed the screen.
        font = QFont("Courier New")
        font.setStyleHint(QFont.StyleHint.Monospace)
        fm = QFontMetrics(font)
        # A real run of characters, not averageCharWidth(): that rounds down,
        # and 200 columns of rounding is a visibly short window.
        want_w = fm.horizontalAdvance("0" * self._REPORT_COLS) + self._REPORT_CHROME_PX
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            self.resize(min(want_w, int(avail.width() * 0.95)),
                        int(avail.height() * 0.9))
        else:
            self.resize(min(want_w, 1400), 750)
        # Report viewer setup
        self.report_viewer = QTextEdit()
        self.report_viewer.setReadOnly(True)
        self.report_viewer.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.report_viewer.setFont(font)
        self.report_viewer.setMinimumHeight(500)
        self.report_viewer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # The one check that decides whether the website will be complete: a
        # species with no JPG gets no picture on the species page, yet still
        # counts in its totals. Shown left of Run Step so it is seen without
        # reading the report.
        self.jpg_check_label = QLabel("")
        self.jpg_check_label.setWordWrap(True)
        self.jpg_check_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_layout.insertWidget(0, self.jpg_check_label, 1)

        # Add widgets to layout
        self.layout().addWidget(QLabel("Species Overview Report"))
        self.layout().addWidget(self.report_viewer)

        # Add buttons
        button_layout = QHBoxLayout()
        save_button = QPushButton("Save As")
        table_button = QPushButton("Save Species Table (Excel / CSV)")
        copy_button = QPushButton("Copy to Clipboard")
        save_button.clicked.connect(self.save_report)
        table_button.clicked.connect(self.save_table)
        copy_button.clicked.connect(self.copy_to_clipboard)
        button_layout.addWidget(save_button)
        button_layout.addWidget(table_button)
        button_layout.addWidget(copy_button)
        self.layout().addLayout(button_layout)

    def _fit_to_report(self, text: str):
        """Size the window to the report that just loaded: its longest line plus
        a few percent of air, measured in the viewer's own font.

        A constant cannot do this. The species table is 100 + 3 x (trip days)
        characters wide — 76 of names, 4 for "New ", 3 per day, 20 for the
        summary columns — so a two-week trip and a five-week trip want visibly
        different windows. One number left either dead space down the right or a
        horizontal scrollbar, depending on the trip.

        The cap is what keeps a laptop honest: on a narrow screen the window
        stops at 95% of the available width and the report scrolls, which is the
        right answer there and costs the big screen nothing.
        """
        lines = text.splitlines()
        if not lines:
            return
        cols = max(len(ln) for ln in lines)
        cols = int(cols * self._REPORT_SLACK) + 1
        fm = self.report_viewer.fontMetrics()
        want = fm.horizontalAdvance("0" * cols) + self._REPORT_CHROME_PX
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            want = min(want, int(screen.availableGeometry().width() * 0.95))
        # Height is left alone: the report is long whatever its width, and the
        # user may have set it deliberately.
        self.resize(max(want, 600), self.height())

    def _reports_dir(self) -> Path:
        """Where the step writes its report (the album version overrides it)."""
        return Path(_config.get("trip", "tripbiotadatabase")) / "metadata" / "reports"

    def _save_folder(self) -> list:
        """Where Save As / Save Species Table start: [] = the trip's picture
        folder (start_file's own default). The album version overrides it."""
        return []

    def on_write(self):
        if self.subdirectory:
            selected_subdir = self.subdir_dropdown.currentText()
            _config["project"]["picturedirectory"] = os.path.join(self.base_path, selected_subdir)

        def load_report():
            try:
                reports_dir = self._reports_dir()
                report_path = reports_dir / "Species overview.txt"
                if report_path.exists():
                    with open(report_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        self.report_viewer.setPlainText(content)
                        self._fit_to_report(content)
                    self._show_jpg_check(reports_dir / "Species overview.csv")
                else:
                    self.report_viewer.setPlainText("⚠️ Report file not found.")
            except Exception as e:
                self.report_viewer.setPlainText(f"❌ Error loading report: {e}")

        self.jpg_check_label.setText("")
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=load_report)

    def _show_jpg_check(self, table_path: Path):
        """Orange when a species has no JPG anywhere in the trip (it will be
        counted on the website but have no picture there), plain when every
        species has one. Read from the step's own species table, the same
        rows as the report."""
        import csv
        try:
            with open(table_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        except OSError:
            self.jpg_check_label.setText("")
            return
        missing = [r.get("English") or r.get("Scientific", "")
                   for r in rows if r.get("JPG") == "no"]
        if missing:
            n = len(missing)
            self.jpg_check_label.setStyleSheet("color: #B45309; font-weight: bold;")
            self.jpg_check_label.setText(
                f"⚠ {n} species {'has' if n == 1 else 'have'} no JPG — "
                f"no picture on the website, but still counted: "
                + ", ".join(missing))
        else:
            self.jpg_check_label.setStyleSheet("")
            self.jpg_check_label.setText(
                f"✅ All {len(rows)} species have a JPG.")

    def save_report(self):
        from utils.start_folder import start_file
        default_path = start_file(self._save_folder(), "Species overview.txt")
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Report As", default_path, "Text Files (*.txt);;All Files (*)"
        )
        if filename:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(self.report_viewer.toPlainText())

    def save_table(self):
        """Save the species table as a spreadsheet, one column per report
        column and one per day, so it can be filtered (e.g. no JPG yet, seen
        on these days). The step writes the table next to the text report;
        this only converts it to the format the user picks."""
        import csv
        source = self._reports_dir() / "Species overview.csv"
        if not source.exists():
            QMessageBox.information(
                self, "Save Species Table",
                "There is no species table yet. Press Run Step first.")
            return
        with open(source, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        from utils.start_folder import start_file
        default_path = start_file(self._save_folder(), "Species table.xlsx")
        filename, chosen = QFileDialog.getSaveFileName(
            self, "Save Species Table As", default_path,
            "Excel Workbook (*.xlsx);;CSV (*.csv)")
        if not filename:
            return
        try:
            if filename.lower().endswith(".csv") or chosen.startswith("CSV"):
                with open(filename, "w", encoding="utf-8-sig", newline="") as f:
                    csv.writer(f).writerows(rows)
            else:
                from openpyxl import Workbook
                from openpyxl.styles import Font
                wb = Workbook()
                ws = wb.active
                ws.title = "Species"
                for r in rows:
                    # Numbers as numbers, so Excel can sort and filter on them.
                    ws.append([int(v) if v.isdigit() else v for v in r])
                for cell in ws[1]:
                    cell.font = Font(bold=True)
                ws.auto_filter.ref = ws.dimensions
                ws.freeze_panes = "C2"
                widths = {"Nr": 5, "Group": 8, "English": 32, "Dutch": 32,
                          "Scientific": 30, "New": 6, "Stars": 6, "Days": 6, "Pics": 6,
                          "Best": 6, "JPG": 6}
                from openpyxl.styles import Alignment
                from openpyxl.utils import get_column_letter
                import re as _re
                for i, name in enumerate(rows[0], start=1):
                    is_day = bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}", name))
                    # Day headers stand upright in narrow columns (one mark each),
                    # so a whole trip fits on the screen next to the colour counts.
                    ws.column_dimensions[get_column_letter(i)].width = (
                        3.5 if is_day else widths.get(name, 9))
                    if is_day:
                        ws.cell(row=1, column=i).alignment = Alignment(
                            text_rotation=90, horizontal="center")
                ws.row_dimensions[1].height = 62
                wb.save(filename)
        except OSError as e:
            QMessageBox.warning(self, "Save Species Table",
                                f"Could not save the table:\n{e}")
            return
        # Open it in whatever the system uses for .xlsx/.csv (Excel, Numbers).
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(filename))

    def copy_to_clipboard(self):
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.report_viewer.toPlainText())

class AlbumCountSpeciesDialog(CountSpeciesDialog):
    """Count Species for the active album: the same window, report and
    spreadsheet export as a trip's, built from the master over the album's
    dates (batch.svv7CountSpecies). No New column; best stars in its place."""

    def __init__(self, parent, menu_spec):
        super().__init__(parent, menu_spec)
        self.setWindowTitle("Count Species — Album")
        # The base window names the active TRIP's folder; this is the album's.
        self.root_caption.setText("Album folder")
        self.info_text.setText("Count the species in this album: one row per species, "
                               "one column per day, read from the master.")
        self.root_label.setText(str(self._album_root()))

    @staticmethod
    def _album_root() -> Path:
        return Path(_config.get("virttrip", "virttripbiotadatabase", fallback="")
                    .replace('"', "").replace("'", "").strip())

    def _reports_dir(self) -> Path:
        return self._album_root() / "metadata" / "reports"

    def _save_folder(self) -> list:
        return [str(self._album_root())]


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
        # full width, text aligned inside — with an addWidget alignment a
        # word-wrapped label kept its own narrow width (2026-10-18)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(msg)
        from gui.wait_picture import spinner_movie
        movie = spinner_movie()
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
                read_ini(cfg, svconfig_path())

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
            "<p><b>Run</b> takes the four steps in order and shows each one's "
            "progress; it stops at a step that fails, and the next Run can "
            "continue there. Step&nbsp;1 counts the "
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
            self.rows.append(row)

        self.warning_label = QLabel("")
        # Information, not a warning: having committed is normal (2026-10-12).
        from gui import theme
        self.warning_label.setStyleSheet(f"color: {theme.INK_FAINT};")
        self._committed_here = False     # a commit made in THIS dialog
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

        # When True the dialog is auto-advancing through the steps (the Run
        # button; the per-step Run buttons were removed 2026-10-14, once the
        # chain had proven reliable). A step failure — or the user declining
        # the re-commit confirm — stops the chain.
        self._chaining = False

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.run_all_btn = QPushButton("Run")
        self.run_all_btn.setDefault(True)
        self.run_all_btn.setToolTip(
            "Run the steps in order, stopping automatically if one fails.")
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
        parent_layout.addLayout(row)
        return {'pip': pip, 'timestamp': timestamp, 'state': 'pending'}

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
        """Run is available whenever no step is running."""
        self.run_all_btn.setEnabled(
            not any(r['state'] == 'running' for r in self.rows))

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
        """Say that these pictures are already in the master — but not right
        after committing them here: the green "Master commit complete" says
        that, and a second line read as "you did something wrong"."""
        when = self._committed_at()
        if when and not self._committed_here:
            self.warning_label.setText(
                f"These pictures are in the master since {when}. Running step "
                f"{len(self.rows)} again asks first — it would add them a second time.")
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
        self._committed_here = True
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
        """Run the steps in order, auto-advancing on success and stopping on
        the first failure (or a declined re-commit confirm).

        Where to start: nothing done, or everything done -> step 1. Stopped
        halfway (some green, then one not) -> ask: continue there, or start
        again from step 1 (right after changing the trip in between)."""
        first_open = next((i for i, r in enumerate(self.rows)
                           if r['state'] != 'done'), None)
        start = 0
        # Merged before: first make sure there is something to add — also when
        # steps 1–3 are still green from an earlier run (the laptop test went
        # straight to "Continue at step 4?" and ran them again).
        if self._committed_at() and self._nothing_new():
            return
        if first_open not in (None, 0):
            box = QMessageBox(self)
            box.setWindowTitle("Add Trip Pictures to Master")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(f"Steps 1–{first_open} are done. "
                        f"Continue at step {first_open + 1}?")
            box.setInformativeText(
                "Start again from step 1 if you changed the trip since.")
            cont = box.addButton(f"Continue at step {first_open + 1}",
                                 QMessageBox.ButtonRole.AcceptRole)
            again = box.addButton("Start again from step 1",
                                  QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(cont)
            box.exec()
            if box.clickedButton() is cont:
                start = first_open
            elif box.clickedButton() is not again:
                return
        self._chaining = True
        self.run_all_btn.setEnabled(False)
        self._run_step(start)

    def _nothing_new(self) -> bool:
        """Merged before: check, before any step runs, that the trip still has
        files the master lacks. Says so and returns True when it has none."""
        trip_db = _config.get("trip", "tripbiotadatabase", fallback="")
        master = _config.get("master", "masterbiotadatabase", fallback="")
        if not (trip_db and master):
            return False
        try:
            from engine.merge_check import nothing_new
            from gui.wait_picture import run_waiting
            none_new, n = run_waiting(self, "Checking what the master already has…",
                                      lambda: nothing_new(trip_db, master))
        except Exception as e:
            self.logger.warning(f"Could not check what the master already has: {e}")
            return False
        if none_new:
            QMessageBox.information(
                self, "Nothing to add",
                f"All {n} files of this trip's pictures are already in the master "
                f"(merged {self._committed_at()}).\n\nReset Trip and publishing "
                f"again does not make them new. Nothing was run.")
        return none_new

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
            subdirectory=False,
            picture="coati",
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
                        from utils.atomic_replace import replace_with_retry
                        replace_with_retry(backup_csv, master_csv)
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
        ("avibase",       "Fixed AviBase layout  (English;scientific;Dutch)"),
        ("birdingbreaks", "Columns I marked myself  (any list)"),
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
            info_text=(
                "Reads the trip's bird list and writes an Excel checklist beside "
                "it in the trip data folder, with a column per trip day."
            ),
            subdirectory=False,
            # The secretary bird sits in the dialog itself (his request,
            # 2026-10-20): the wait box after Run Step is gone too fast to see.
            picture="secretary_bird",
        )
        # The bird list lives in the trip's DATA folder, not with the pictures.
        # The base dialog shows the picture folder, which sent the eye to the
        # wrong place while the step read somewhere else entirely.
        self.root_caption.setText("Trip data folder")
        self.root_label.setText(str(self.trip_root))

        # Which list to read. Browse… rather than a fixed avibase.csv: the list
        # arrives from AviBase or Birding Breaks under whatever name it came with.
        self.source_field = QLineEdit(self._initial_source_file())
        self.source_field.setPlaceholderText(
            "No list picked yet — Browse… to the CSV you downloaded")
        browse_source_btn = QPushButton("Browse…")
        browse_source_btn.clicked.connect(self._browse_source)
        paste_source_btn = QPushButton("Paste…")
        paste_source_btn.setToolTip(
            "Paste a list copied from the AviBase page; it is saved beside the trip")
        paste_source_btn.clicked.connect(self._paste_source)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source_field)
        source_row.addWidget(browse_source_btn)
        source_row.addWidget(paste_source_btn)

        # Which columns hold what - only a Birding Breaks list needs this; an
        # AviBase list has a fixed shape the old reader already knows.
        self.columns_btn = QPushButton("Columns…")
        self.columns_btn.clicked.connect(self._edit_columns)
        self.columns_summary = QLabel("")
        self.columns_summary.setWordWrap(True)

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
        self.reader_dropdown.currentIndexChanged.connect(
            lambda _idx: self._sync_columns_row())

        layout = self.body_layout
        anchor = layout.indexOf(self.info_text)
        layout.insertWidget(anchor, QLabel("Species list file"))
        layout.insertLayout(anchor + 1, source_row)
        layout.insertWidget(anchor + 2, QLabel("Source list format"))
        format_row = QHBoxLayout()
        format_row.addWidget(self.reader_dropdown)
        format_row.addWidget(self.columns_btn)
        layout.insertLayout(anchor + 3, format_row)
        layout.insertWidget(anchor + 4, self.columns_summary)
        layout.insertWidget(anchor + 5, self.phone_layout_checkbox)
        layout.insertWidget(anchor + 6, self.first_lang_label)
        layout.insertWidget(anchor + 7, self.first_lang_dropdown)
        self._sync_columns_row()

    @staticmethod
    def _same_folder(a, b):
        if not a or not b:
            return False
        return (os.path.normcase(os.path.abspath(a)) ==
                os.path.normcase(os.path.abspath(b)))

    def _default_source_file(self):
        return os.path.join(self.trip_root, "avibase.csv")

    def _initial_source_file(self):
        """The list to start from: the one picked last time FOR THIS TRIP, else
        avibase.csv in the trip data folder, else nothing.

        A path remembered for a different trip is ignored on purpose — reusing it
        would quietly build this trip's checklist from the previous trip's list.
        """
        saved = _config.get("avibase", "source_file", fallback="").strip()
        saved_trip = _config.get("avibase", "source_trip", fallback="").strip()
        if (saved and os.path.isfile(saved)
                and self._same_folder(saved_trip, self.trip_root)):
            return saved
        default = self._default_source_file()
        return default if os.path.isfile(default) else ""

    def _browse_source(self):
        current = self.source_field.text().strip()
        from utils.start_folder import start_folder
        start_dir = start_folder(current, str(self.trip_root or ""))
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the trip's bird list", start_dir,
            "Species lists (*.xlsx *.xlsm *.csv);;Spreadsheets (*.xlsx *.xlsm);;"
            "Text lists (*.csv *.txt);;All files (*)")
        if path:
            self.source_field.setText(os.path.normpath(path))
            self._sync_columns_row()      # another file, possibly another shape

    def _current_format(self):
        return self.READER_OPTIONS[self.reader_dropdown.currentIndex()][0]

    def _saved_columns(self):
        """The remembered column map. Unlike the file path this is NOT tied to a
        trip: Birding Breaks sends the same shape every time, so the map used
        last time is the best starting point for the next trip."""
        from engine.avibaselib import parse_roles, decode_delimiter, column_map_for
        source = self.source_field.text().strip() if hasattr(self, "source_field") else ""
        if source:
            saved = column_map_for(_config, source)
            if saved:
                return saved
        roles = parse_roles(_config.get("avibase", "columns", fallback=""))
        first_row = _config.getint("avibase", "data_start_row", fallback=1)
        delimiter = decode_delimiter(_config.get("avibase", "delimiter", fallback=""))
        return roles, first_row, delimiter

    def _sync_columns_row(self):
        """The column map belongs to the Birding Breaks reader only, so the
        button and its summary are hidden for an AviBase list rather than
        offered and then ignored."""
        from gui.avibase_columns import describe_roles
        is_bb = self._current_format() == "birdingbreaks"
        self.columns_btn.setVisible(is_bb)
        self.columns_summary.setVisible(is_bb)
        if is_bb:
            roles, first_row, _ = self._saved_columns()
            self.columns_summary.setText(describe_roles(roles, first_row))

    def _edit_columns(self):
        from gui.avibase_columns import ColumnMappingDialog, describe_roles
        from engine.avibaselib import format_roles, encode_delimiter
        source = self.source_field.text().strip()
        if not source or not os.path.isfile(source):
            self.status_label.setText(
                "❌ Pick the bird list first with Browse… — the columns are "
                "marked on the file itself."
            )
            return
        roles, first_row, delimiter = self._saved_columns()
        dialog = ColumnMappingDialog(self, source, roles, first_row, delimiter)
        if not dialog.exec():
            return
        if not _config.has_section("avibase"):
            _config.add_section("avibase")
        _config["avibase"]["columns"] = format_roles(dialog.roles)
        _config["avibase"]["data_start_row"] = str(dialog.first_data_row)
        _config["avibase"]["delimiter"] = encode_delimiter(dialog.delimiter)
        # ...and against this file by name, because a trip can hold several
        # lists and PhotoMechanic reads them all in one run.
        from engine.avibaselib import COLUMN_MAP_SECTION, format_column_map
        if not _config.has_section(COLUMN_MAP_SECTION):
            _config.add_section(COLUMN_MAP_SECTION)
        _config[COLUMN_MAP_SECTION][os.path.basename(source)] = format_column_map(
            dialog.roles, dialog.first_data_row, dialog.delimiter)
        persist_config()
        self.columns_summary.setText(
            describe_roles(dialog.roles, dialog.first_data_row))
        self.status_label.setText("")

    def _fixed_layout_complaint(self, source):
        from gui.avibase_columns import fixed_layout_complaint
        return fixed_layout_complaint(source)

    def _paste_source(self):
        """A list copied from the AviBase page, saved beside the trip and marked
        up in one go — the columns always need a look, so go straight there."""
        from gui.avibase_paste import PasteListDialog
        dialog = PasteListDialog(self, self.trip_root)
        if not dialog.exec() or not dialog.saved_path:
            return
        self.source_field.setText(os.path.normpath(dialog.saved_path))
        # A pasted list is read by the marked-columns reader, never by the fixed
        # layout — pick that here so Run Step cannot use the wrong one.
        for idx, (key, _) in enumerate(self.READER_OPTIONS):
            if key == "birdingbreaks":
                self.reader_dropdown.setCurrentIndex(idx)
                break
        if not _config.has_section("avibase"):
            _config.add_section("avibase")
        _config["avibase"]["columns"] = ""      # this file has its own shape
        self._sync_columns_row()
        self._edit_columns()

    def _sync_first_lang_enabled(self):
        enabled = self.phone_layout_checkbox.isChecked()
        self.first_lang_dropdown.setEnabled(enabled)
        self.first_lang_label.setEnabled(enabled)

    def on_write(self):
        # Say which file is missing before the step runs — the batch module can
        # only report a path, and a path is what confused this in the first place.
        source = self.source_field.text().strip()
        if not source or not os.path.isfile(source):
            self.status_label.setText(
                f"❌ Pick the bird list first with Browse… "
                f"(nothing found as {os.path.basename(self._default_source_file())} "
                f"in {self.trip_root})."
            )
            return

        if self._current_format() == "avibase":
            # The fixed layout reads semicolon-separated text and nothing else.
            # An .xlsx or a tab-separated paste would come out as a checklist of
            # section headings with no species in it, which is a puzzle to
            # receive rather than an error.
            complaint = self._fixed_layout_complaint(source)
            if complaint:
                self.status_label.setText(complaint)
                return

        if self._current_format() == "birdingbreaks" and not self._saved_columns()[0]:
            self.status_label.setText(
                "❌ Open Columns… first and mark which column holds the "
                "sequence number and the names — a Birding Breaks list has no "
                "fixed shape."
            )
            return

        if not _config.has_section("avibase"):
            _config.add_section("avibase")
        _config["avibase"]["source_file"] = source
        # Remembered together with the trip it belongs to — see _initial_source_file.
        _config["avibase"]["source_trip"] = str(self.trip_root)
        _config["avibase"]["source_format"] = self.READER_OPTIONS[self.reader_dropdown.currentIndex()][0]
        _config["avibase"]["phone_layout"] = "true" if self.phone_layout_checkbox.isChecked() else "false"
        _config["avibase"]["first_column_lang"] = self.LANG_OPTIONS[self.first_lang_dropdown.currentIndex()][0]
        persist_config()          # the pick survives a restart
        super().on_write()


class PhotoMechanicDialog(BaseTripModuleDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(
            parent,
            menu_spec,
            title="Create PhotoMechanic keyword files for next trip.",
            info_text=(
                "Writes two PhotoMechanic keyword files — one with Dutch names, "
                "one with English — into the PhotoMechanic keywords folder. They "
                "hold the names from every list ticked below, so one keyword set "
                "covers the whole trip."
            ),
            subdirectory=False
        )
        # Every list the trip holds, because a keyword set you have to swap
        # between the legs of one trip is a keyword set that gets in the way.
        # Each is read with the columns marked for it, so lists of different
        # shapes can go into one set.
        self.root_caption.setText("Trip data folder")
        self.root_label.setText(str(self.trip_root))

        self.lists_widget = QListWidget()
        self.lists_widget.setMaximumHeight(150)
        layout = self.layout()
        anchor = layout.indexOf(self.info_text)
        layout.insertWidget(anchor, QLabel("Species lists in this trip"))
        layout.insertWidget(anchor + 1, self.lists_widget)
        self._fill_lists()

    def _fill_lists(self):
        """Name every list with what it would contribute, so the choice is made
        on what is in the files rather than on their names."""
        from engine.avibaselib import trip_list_files, read_list_file
        chosen = [n.strip().lower()
                  for n in _config.get("avibase", "pm_lists", fallback="").split("|")
                  if n.strip()]
        self.lists_widget.clear()
        for path in trip_list_files(self.trip_root, os.path.basename(self.trip_root)):
            name = os.path.basename(path)
            try:
                species = read_list_file(path, _config)
                count = sum(1 for v in species.values() if v.get("number") != -1)
                text = f"{name}   —   {count} species"
                readable = count > 0
            except Exception as err:
                text = f"{name}   —   cannot be read ({err.__class__.__name__})"
                readable = False
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ticked = readable and (not chosen or name.lower() in chosen)
            item.setCheckState(Qt.CheckState.Checked if ticked
                               else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if not readable:
                item.setForeground(QColor("#8a8a8a"))
            self.lists_widget.addItem(item)

        if self.lists_widget.count() == 0:
            self.status_label.setText(
                "❌ No species list in the trip data folder. Add one with "
                "Utilities ▸ AviBase (Browse… or Paste…) and come back."
            )

    def _ticked_lists(self):
        return [self.lists_widget.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.lists_widget.count())
                if self.lists_widget.item(i).checkState() == Qt.CheckState.Checked]

    def on_write(self):
        ticked = self._ticked_lists()
        if not ticked:
            self.status_label.setText(
                "❌ Tick at least one species list — the keyword files are built "
                "from the names in them."
            )
            return
        if not _config.has_section("avibase"):
            _config.add_section("avibase")
        _config["avibase"]["pm_lists"] = "|".join(ticked)
        persist_config()
        super().on_write()


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
            "The ZIP contains the JPGs from <tt>biota/</tt> and their sidecars, "
            "plus all CSVs from <tt>metadata/active/</tt> "
            "(data_files, data_pictures, species, speciescollection…).  "
            "RAW files, web output and sidecars for pictures with no JPG are "
            "left out — the laptop cannot show those pictures, and reads their "
            "rating and camera data from the CSVs anyway.  "
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
            "Writes a small ZIP holding the trip's settings (<tt>trip.ini</tt>), its "
            "trip picture and banner, its species list, its lifer status, its "
            "translations and its publication state. The days' work and the "
            "pictures follow in steps 2 and 3."
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
                from engine.trip_transfer import build_manifest
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
                self.copy_label.setText(f"🌐 {_e(published[_info['published']])}")
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
        # The remembered ZIP was filled in before textChanged was connected, and
        # Browse to the same file changes no text — so read it once now.
        self._inspect()

    def _browse_zip(self):
        from utils.start_folder import start_folder
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Trip ZIP",
            start_folder(self.zip_field.text(), Path.home() / "Downloads"),
            "ZIP files (*.zip);;All files (*)")
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
                                          restore_baseline, merge_transcache, merge_aliases,
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
                    read_ini(module_config, svconfig_path())
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
            names_added = merge_aliases(zip_path, dest)
            if names_added:
                notes.append(f"Species names merged: {names_added} new.")
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
            first_day = first_day_under(picture_root) if picture_root else ""
            from utils.config import root_is_unset
            if root_is_unset("trip", "tripsrootdirectory"):
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
            "Extracts <tt>biota/</tt> (species JPGs and their sidecars) and "
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

        # How old the ZIP is, and how old what is already here is — the two
        # dates the decision to install actually turns on.
        self.age_label = QLabel("")
        self.age_label.setWordWrap(True)
        self.age_label.setTextFormat(Qt.TextFormat.RichText)
        self.age_label.setVisible(False)
        layout.addWidget(self.age_label)

        self.delete_check = QCheckBox("Delete ZIP file after successful install")
        layout.addWidget(self.delete_check)

        # The result line. The progress itself goes to a wait window (the cooper's
        # hawk) that is open only while the install runs — see _install_wait_box.
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
        from utils.start_folder import start_folder
        path, _ = QFileDialog.getOpenFileName(
            self, "Select SmallMaster ZIP",
            start_folder(self.zip_field.text(), Path.home() / "Downloads"),
            "ZIP files (*.zip);;All files (*)")
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
            self._show_snapshot_ages(zip_path)
        except Exception as e:
            self.contents_label.setText(f"Cannot read ZIP: {e}")
            self.age_label.setVisible(False)

    def _show_snapshot_ages(self, zip_path):
        """Say how old the ZIP is, and how old what is already installed is.

        The question this screen exists to answer is "is this newer than what I
        have?", and until now neither half of that was on the screen. Both dates
        together also make the honest case for NOT installing: a ZIP older than
        the copy on the laptop would take the master backwards.
        """
        from engine import smallmaster_stamp as _st
        from gui import theme as _th
        zip_info = _st.read_zip_stamp(zip_path)
        target = self.target_edit.text().strip()
        here = _st.read_stamp(target) or _st.estimate_from_files(target)
        parts = [f"<b>This ZIP:</b> {_st.describe(zip_info)}"]
        if here:
            parts.append(f"<b>Installed now:</b> {_st.describe(here)}")
        else:
            parts.append("<b>Installed now:</b> nothing here yet")
        backwards = (zip_info and here
                     and (_st.age_days(zip_info) or 0) > (_st.age_days(here) or 0) + 1)
        if backwards:
            parts.append("<b>⚠ this ZIP is OLDER than the copy already installed</b>")
        self.age_label.setText(" &nbsp;|&nbsp; ".join(parts))
        colour = _th.WARN if backwards else _th.INK_SOFT
        self.age_label.setStyleSheet(f"color: {colour};")
        self.age_label.setVisible(True)

    def _on_install(self):
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
        # The alias table too: a local name learned on this machine lives only
        # here until a trip carries it home.
        from engine.species_aliases import alias_path
        existing_alias_text = None
        if os.path.isfile(alias_path(target)):
            try:
                with open(alias_path(target), "r", encoding="utf-8", newline="") as _f:
                    existing_alias_text = _f.read()
            except Exception:
                pass

        t0 = _time.time()
        self.status_label.setText("")
        box, say = self._install_wait_box()
        try:
            msg = self._install_steps(zip_path, target, existing_sc_content,
                                      sc_path, t0, say, existing_alias_text)
        finally:
            box.close()
            box.deleteLater()
        self.status_label.setText(msg)

    def _install_wait_box(self):
        """The wait window shown while the ZIP is extracted and the database built:
        the cooper's hawk beside the progress line. Modal, so the dialog's own
        Install/Close cannot be pressed mid-install. Returns (box, say) — say(text)
        updates the line and lets the window repaint (the install runs on the GUI
        thread, in steps)."""
        from gui.wait_picture import wait_layout
        box = QDialog(self)
        box.setWindowTitle("Installing SmallMaster…")
        box.setModal(True)
        box.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        column = wait_layout(box, "coopers_hawk", 380, 120)
        line = QLabel("Opening the ZIP…")
        line.setWordWrap(True)
        line.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        column.addWidget(line)
        box.show()
        QApplication.processEvents()

        def say(text):
            line.setText(text)
            QApplication.processEvents()
        return box, say

    def _install_steps(self, zip_path, target, existing_sc_content, sc_path, t0, say,
                       existing_alias_text=None):
        """Extract, merge, rebuild the DB, lock read-only. Returns the result line."""
        import tempfile as _tmp
        import zipfile as _zf
        import time as _time
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
                        say(f"Extracting… {i:,} / {total:,} files ({pct}%)")
                        last_ui = now
        except Exception as e:
            return f"Extract failed: {e}"
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
        if existing_alias_text:
            try:
                from engine.species_aliases import alias_path, merge_alias_text
                n = merge_alias_text(existing_alias_text, alias_path(target))
                if n:
                    merged_note += f"  Kept {n} species name(s) learned on this computer."
            except Exception as e:
                merged_note += f"  (species names merge skipped: {e})"

        # Rebuild the master DB immediately so it is ready on first use.
        db_note = ""
        try:
            from db.master_db import MasterDB
            say(f"Extracted {total:,} files in {elapsed:.0f}s.{merged_note}\n"
                f"Building master database…")

            def _db_progress(msg):
                say(f"Extracted {total:,} files in {elapsed:.0f}s.{merged_note}\n{msg}")

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

        from engine import smallmaster_stamp as _st
        stamp = _st.read_stamp(target)
        stamp_note = f"  Snapshot {_st.describe(stamp)}." if stamp else                      "  (this ZIP carries no snapshot date — made before they existed)"
        msg = f"✅ Installed in {elapsed:.0f}s → {target}{merged_note}{db_note}{stamp_note}"
        # The status bar carries this date from now on; repaint it here rather
        # than making the user restart to see what they just installed.
        main = self.parent()
        if hasattr(main, "update_master_snapshot_label"):
            main.update_master_snapshot_label()
        if self.delete_check.isChecked():
            try:
                os.remove(zip_path)
                msg += "  (ZIP deleted)"
            except Exception as e:
                msg += f"  (could not delete ZIP: {e})"
        return msg
