import webbrowser
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QSpinBox, QTabWidget, QWidget, QLineEdit)
import logging
import os
import shutil
from datetime import datetime, timedelta
from datetime import datetime
from pathlib import Path

from gui.batch import BatchModuleRunner
from utils.config import set_setting, _config, get_virttrips_root, get_trips_root
from utils.virttrip_config import read_virttrip_ini
from gui.virttrip_overview import populate_virttrip_overview
import batch.svv2ExtractDays
import batch.svv3WriteBlog
import batch.svv4BuildWebsite
from gui.menu import MenuSpec
from widgets.small_widgets import HTMLLabel
from widgets.trip_widgets import (LabeledTextFieldQt, DateRangeFieldQt,
                                   RecentDirectoryFieldQt, ImageFileFieldQt)

import batch.svv1MakeVirtualRepository


def generate_virttrip_ini(
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
    ini_path = os.path.join(trip_root, "virttrip.ini")

    master_web  = _config.get("master", "masterbiotawebroot",  fallback="/nature/")
    master_disk = _config.get("master", "masterbiotadatabase", fallback="")
    trip_name   = Path(trip_name_path).name

    def _ini_escape(text):
        if "\n" not in text:
            return text
        head, *rest = text.split("\n")
        return head + "\n" + "\n".join(("    " + l) if l.strip() else l for l in rest)

    ini_text = f"""\
[trip_config]
trip_type = virtual
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
        logger.info(f"✅ virttrip.ini written to {ini_path}")
    except Exception as err:
        logger.info(f"❌ Failed to write virttrip.ini: {err}")


class VirtualTripCreationDialog(QDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.menu_spec = menu_spec
        self.logger = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Create Virtual Trip")
        self.resize(800, 600)

        trip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data = read_virttrip_ini(trip_root) if trip_root and os.path.isdir(trip_root) else {}

        main_layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ── Tab 1: Select Trip Folder ──────────────────────────────────────────
        tab1 = QWidget()
        layout1 = QVBoxLayout(tab1)
        layout1.addWidget(HTMLLabel(
            "<p><b>What is a virtual trip?</b><br>"
            "A <b>virtual trip</b> is a curated collection of species and pictures drawn from an "
            "existing date range in the master database. Unlike a regular trip there is no picture "
            "directory to scan — pictures are selected by date range from the master.</p>"
            "<p><b>Creating a new virtual trip</b><br>"
            "Enter a folder name (e.g. <i>birds2024</i>) and click <b>Create Folder</b>. "
            "Fill in the dates and description, then click <b>Create Virtual Trip</b>.</p>"
        ))

        self.root_field = RecentDirectoryFieldQt("Virtual Trip Root folder", trip_root)
        self.root_field.textChanged(self.on_trip_root_changed)
        layout1.addWidget(self.root_field)

        new_folder_widget = QWidget()
        new_folder_row = QHBoxLayout(new_folder_widget)
        new_folder_row.setContentsMargins(0, 0, 0, 0)
        new_folder_row.addWidget(QLabel("New trip folder"))
        self.new_folder_name = QLineEdit()
        self.new_folder_name.setPlaceholderText("e.g. birds2024")
        self.new_folder_name.setMaximumWidth(300)
        self.new_folder_name.textChanged.connect(self._update_folder_preview)
        new_folder_row.addWidget(self.new_folder_name)
        create_folder_btn = QPushButton("Create Folder")
        create_folder_btn.setFixedWidth(110)
        create_folder_btn.clicked.connect(self.create_new_trip_folder)
        new_folder_row.addWidget(create_folder_btn)
        new_folder_row.addStretch()
        layout1.addWidget(new_folder_widget)

        self.trip_web = LabeledTextFieldQt(
            "Trip Web root",
            _config.get("virttrip", "virttripbiotawebroot", fallback=""),
            multiline=False, inline=True
        )
        self.trip_web.field.setMaximumWidth(300)
        trip_web_row = QWidget()
        trip_web_layout = QHBoxLayout(trip_web_row)
        trip_web_layout.setContentsMargins(0, 0, 0, 0)
        trip_web_layout.addWidget(self.trip_web)
        trip_web_layout.addStretch()
        layout1.addWidget(trip_web_row)

        tabs.addTab(tab1, "Select Trip Folder")

        # ── Tab 2: Images & Dates ──────────────────────────────────────────────
        tab2 = QWidget()
        layout2 = QVBoxLayout(tab2)
        layout2.addWidget(HTMLLabel(
            "<p><b>Trip image</b><br>"
            "This image appears in trip listings and should visually represent the trip.<br>"
            "Required dimensions: <b>600×450 pixels</b>.</p>"
        ))
        self.trip_img_field = ImageFileFieldQt("Trip Image", (600, 450), self.root_field)
        self.trip_img_field.field.setText(trip_data.get("trip_picture", ""))
        layout2.addWidget(self.trip_img_field)

        layout2.addWidget(HTMLLabel(
            "<p><b>Web banner</b><br>"
            "This banner is displayed at the top of all blog and species pages for this trip.<br>"
            "Required dimensions: <b>2000×270 pixels</b>.</p>"
        ))
        self.topbar_img_field = ImageFileFieldQt("Top Web Bar", (2000, 270), self.root_field)
        self.topbar_img_field.field.setText(trip_data.get("trip_banner", ""))
        layout2.addWidget(self.topbar_img_field)

        layout2.addWidget(HTMLLabel(
            "<p><b>Date range</b><br>"
            "Defines the date range from which pictures will be drawn from the master database.</p>"
        ))
        self.date_field = DateRangeFieldQt()
        self.date_field.set_dates(trip_data.get("fromdate", ""), trip_data.get("todate", ""))
        layout2.addWidget(self.date_field)

        tabs.addTab(tab2, "Images && Dates")

        # ── Tab 3: Trip Text ───────────────────────────────────────────────────
        tab3 = QWidget()
        layout3 = QVBoxLayout(tab3)
        sub_tabs = QTabWidget()

        dutch_tab = QWidget()
        dutch_layout = QVBoxLayout(dutch_tab)
        self.title_nl_field = LabeledTextFieldQt("Trip title (Dutch)", trip_data.get("title_nl", ""))
        self.message_nl_field = LabeledTextFieldQt(
            "Trip description (Dutch)", trip_data.get("message_nl", ""), multiline=True)
        translate_nl_btn = QPushButton("Translate to English")
        translate_nl_btn.clicked.connect(self.translate_dutch_to_english)
        dutch_layout.addWidget(translate_nl_btn)
        dutch_layout.addWidget(HTMLLabel(
            "<p><b>Trip title and description (Dutch)</b><br>"
            "Use the <i>Translate</i> button to generate an English version automatically.</p>"
        ))
        dutch_layout.addWidget(self.title_nl_field)
        dutch_layout.addWidget(self.message_nl_field)
        sub_tabs.addTab(dutch_tab, "Dutch Text")

        english_tab = QWidget()
        english_layout = QVBoxLayout(english_tab)
        self.title_en_field = LabeledTextFieldQt("Trip title (English)", trip_data.get("title_en", ""))
        self.message_en_field = LabeledTextFieldQt(
            "Trip description (English)", trip_data.get("message_en", ""), multiline=True)
        translate_en_btn = QPushButton("Translate to Dutch")
        translate_en_btn.clicked.connect(self.translate_english_to_dutch)
        english_layout.addWidget(translate_en_btn)
        english_layout.addWidget(HTMLLabel(
            "<p><b>Trip title and description (English)</b><br>"
            "Use the <i>Translate</i> button to generate a Dutch version automatically.</p>"
        ))
        english_layout.addWidget(self.title_en_field)
        english_layout.addWidget(self.message_en_field)
        sub_tabs.addTab(english_tab, "English Text")

        layout3.addWidget(sub_tabs)
        tabs.addTab(tab3, "Trip Text")

        main_layout.addWidget(tabs)

        self.status_label = QLabel("")
        main_layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        write_ini_btn = QPushButton("Write virttrip.ini")
        create_btn    = QPushButton("Create Virtual Trip")
        select_btn    = QPushButton("Select")
        btn_layout.addWidget(write_ini_btn)
        btn_layout.addWidget(create_btn)
        btn_layout.addWidget(select_btn)
        main_layout.addLayout(btn_layout)

        write_ini_btn.clicked.connect(self.on_write_ini_clicked)
        create_btn.clicked.connect(self.on_create_clicked)
        select_btn.clicked.connect(self.on_select)

    def closeEvent(self, event):
        self.passed_parent.translator.writeTranslationCache()
        super().closeEvent(event)

    def _update_folder_preview(self, folder_name):
        root = _config.get("virttrip", "virttriprootdirectory", fallback="")
        if not folder_name:
            self.status_label.setText("")
        elif not root:
            self.status_label.setText("⚠ Virtual Trips root not configured — set it in Settings → Config first.")
        else:
            self.status_label.setText(f"Will create: {Path(root) / folder_name}")

    def create_new_trip_folder(self):
        folder_name = self.new_folder_name.text().strip()
        if not folder_name:
            self.status_label.setText("❌ Enter a folder name first.")
            return

        # Use only the explicit root key — the get_virttrips_root() fallback
        # derives from virttripbiotadatabase's parent, which silently lands in
        # the regular trips directory when no root is configured yet.
        trips_parent_str = _config.get("virttrip", "virttriprootdirectory", fallback="")
        if not trips_parent_str:
            self.status_label.setText(
                "❌ Virtual Trips root directory is not configured. "
                "Set it in Settings → Config before creating a trip folder."
            )
            return
        trips_parent = Path(trips_parent_str)
        if not trips_parent.is_dir():
            self.status_label.setText(f"❌ Cannot find parent directory: {trips_parent}")
            return

        new_trip_path = trips_parent / folder_name
        try:
            new_trip_path.mkdir(exist_ok=True)
        except Exception as e:
            self.status_label.setText(f"❌ Could not create folder: {e}")
            return

        dist_dir = Path(__file__).resolve().parent.parent / "dist" / "includes web"
        for src_name in ("tripbanner.jpg", "tripimage.jpg"):
            src = dist_dir / src_name
            dest = new_trip_path / src_name
            if src.exists() and not dest.exists():
                shutil.copy(src, dest)

        self.root_field.set_text(str(new_trip_path))
        self.topbar_img_field.field.setText("tripbanner.jpg")
        self.trip_img_field.field.setText("tripimage.jpg")
        self.trip_web.set_text(f"/virttrips/{folder_name}")
        self.status_label.setText(f"✅ Created: {new_trip_path}")
        self.logger.info(f"Created new virtual trip folder: {new_trip_path}")

    def on_trip_root_changed(self, new_path):
        trip_data = read_virttrip_ini(new_path)
        if not trip_data:
            return
        trip_name = trip_data["trip_name"]
        if "trips" not in trip_name:
            trip_name = "/virttrips/" + trip_name
        self.trip_web.set_text(trip_name)
        self.trip_img_field.field.setText(trip_data["trip_picture"])
        self.topbar_img_field.field.setText(trip_data["trip_banner"])
        self.date_field.set_dates(trip_data["fromdate"], trip_data["todate"])
        self.title_nl_field.field.setText(trip_data["title_nl"])
        self.message_nl_field.field.setText(trip_data["message_nl"])
        self.title_en_field.field.setText(trip_data["title_en"])
        self.message_en_field.field.setText(trip_data["message_en"])

    def on_write_ini_clicked(self):
        self._write_virttrip_ini()
        self.passed_parent.status_bar.showMessage("virttrip.ini written.")

    def on_create_clicked(self):
        self._write_virttrip_ini()
        set_setting("virttrip", "virttriprootdirectory", str(Path(self.root_field.get_text()).parent))
        set_setting("virttrip", "virttripbiotadatabase", self.root_field.get_text())
        set_setting("virttrip", "virttripbiotawebroot",  self.trip_web.field.text())
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)

    def on_select(self):
        set_setting("virttrip", "virttriprootdirectory", str(Path(self.root_field.get_text()).parent))
        set_setting("virttrip", "virttripbiotadatabase", self.root_field.get_text())
        set_setting("virttrip", "virttripbiotawebroot",  self.trip_web.field.text())
        populate_virttrip_overview(self.passed_parent)
        self.accept()

    def translate_dutch_to_english(self):
        self.title_en_field.set_text(
            self.passed_parent.translator.getOneLanguage(self.title_nl_field.get_text(), languagein='en'))
        self.message_en_field.set_text(
            self.passed_parent.translator.getOneLanguage(self.message_nl_field.get_text(), languagein='en'))

    def translate_english_to_dutch(self):
        self.title_nl_field.set_text(
            self.passed_parent.translator.getOneLanguage(self.title_en_field.get_text(), languagein='nl'))
        self.message_nl_field.set_text(
            self.passed_parent.translator.getOneLanguage(self.message_en_field.get_text(), languagein='nl'))

    def _write_virttrip_ini(self):
        trip_root = self.root_field.get_text()
        if not trip_root or not os.path.isdir(trip_root):
            self.root_field.show_hint("Please select a valid trip folder.")
            return
        self.root_field.clear_hint()
        from_date, to_date = self.date_field.get_dates()
        generate_virttrip_ini(
            trip_root,
            self.trip_web.get_text(),
            self.topbar_img_field.get_filename(),
            self.trip_img_field.get_filename(),
            from_date,
            to_date,
            self.title_nl_field.get_text(),
            self.message_nl_field.get_text(),
            self.title_en_field.get_text(),
            self.message_en_field.get_text(),
            self.logger,
        )


class ExtractDaysDialog(QDialog):
    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.menu_spec = menu_spec
        self.logger = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Extract Days from Master")
        self.resize(600, 280)

        layout = QVBoxLayout(self)

        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data = read_virttrip_ini(virttrip_root) if virttrip_root else {}

        fromdate = trip_data.get("fromdate", "?") if trip_data else "?"
        todate   = trip_data.get("todate",   "?") if trip_data else "?"

        # Count days in range for the info label
        day_count = ""
        try:
            n = (datetime.strptime(todate, "%Y-%m-%d") -
                 datetime.strptime(fromdate, "%Y-%m-%d")).days + 1
            day_count = f" ({n} day{'s' if n != 1 else ''})"
        except ValueError:
            pass

        layout.addWidget(HTMLLabel(
            "<p><b>Extract daily picture data from the master</b><br>"
            "Reads the master's <i>data_files.csv</i> and <i>data_pictures.csv</i> and writes "
            "one sub-directory per day that has pictures:</p>"
            "<pre>  &lt;virttrip&gt;/metadata/review/YYYY-MM-DD/data_files.csv\n"
            "  &lt;virttrip&gt;/metadata/review/YYYY-MM-DD/data_pictures.csv</pre>"
            "<p>Days with no pictures in the master are skipped.</p>"
        ))

        layout.addWidget(QLabel(f"Virtual trip:  {virttrip_root or '(not set)'}"))
        layout.addWidget(QLabel(f"Date range:    {fromdate}  →  {todate}{day_count}"))

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        run_btn   = QPushButton("Extract Days")
        close_btn = QPushButton("Close")
        btn_layout.addWidget(run_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        run_btn.clicked.connect(self.on_run)
        close_btn.clicked.connect(self.close)

    def on_run(self):
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec,
                   on_success=lambda: populate_virttrip_overview(self.passed_parent))


def _virttrip_spec(module_fn, before_msg, after_msg):
    """Return a MenuSpec that runs a virtual-trip batch module."""
    return MenuSpec(
        callback=None,
        batch_parms={
            "statusname":    "",
            "module_called": module_fn,
            "before_msg":    before_msg,
            "after_msg":     after_msg,
            "virttrip_mode": True,
        },
    )


class VirttripWriteBlogDialog(QDialog):
    """Run svv3WriteBlog: auto-create blog ini files + generate + upload all days."""

    def __init__(self, parent):
        super().__init__(parent)
        self.logger       = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Write Virtual Trip Blogs")
        self.resize(580, 300)

        layout = QVBoxLayout(self)

        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data  = read_virttrip_ini(virttrip_root) if virttrip_root else {}
        trip_name  = (trip_data or {}).get("trip_name", Path(virttrip_root).name if virttrip_root else "?")
        day_count  = 0
        if virttrip_root:
            review_root = Path(virttrip_root) / "metadata" / "review"
            if review_root.is_dir():
                day_count = sum(
                    1 for d in review_root.iterdir()
                    if d.is_dir() and (d / "data_files.csv").exists()
                )

        layout.addWidget(HTMLLabel(
            "<p><b>Write Virtual Trip Blogs</b></p>"
            "<p>For every day that has extracted data:</p>"
            "<ul>"
            "<li>Auto-creates <code>metadata/blog/blog_YYYY-MM-DD.ini</code> if absent "
            "(title and text generated from species + location data).</li>"
            "<li>Reads the ini (auto-generated or hand-edited) and uploads "
            "NL + EN blog HTML pages.</li>"
            "</ul>"
            "<p>Re-running is safe — existing ini files are preserved; "
            "HTML is always regenerated and re-uploaded.</p>"
        ))

        layout.addWidget(QLabel(f"Virtual trip:   {virttrip_root or '(not set)'}"))
        layout.addWidget(QLabel(f"Trip name:      {trip_name}"))
        layout.addWidget(QLabel(f"Days with data: {day_count}"))

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # Minimum rating threshold — overrides the per-day ini value for this run
        score_row = QHBoxLayout()
        score_row.addWidget(QLabel("Minimum rating (0–100):"))
        self.min_score_spin = QSpinBox()
        self.min_score_spin.setRange(0, 100)
        self.min_score_spin.setValue(int(_config.get("virttrip", "minimum_score", fallback="25")))
        self.min_score_spin.setToolTip(
            "Pictures with a computed rating below this value are excluded from the blog.\n"
            "Lower this if blog pages appear empty."
        )
        score_row.addWidget(self.min_score_spin)
        score_row.addStretch()
        layout.addLayout(score_row)

        self.open_after_checkbox = QCheckBox("Open trip blog in browser after writing")
        self.open_after_checkbox.setChecked(True)
        layout.addWidget(self.open_after_checkbox)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        run_btn   = QPushButton("Write All Blogs")
        close_btn = QPushButton("Close")
        btn_layout.addWidget(run_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        run_btn.clicked.connect(self.on_run)
        close_btn.clicked.connect(self.close)

    def get_blog_url(self) -> str:
        base_url = _config.get("websettings", "base_url", fallback="https://houtekamer.com/")
        try:
            with open("last_blog_url.txt", "r") as f:
                relative_url = f.read().strip()
            return base_url.rstrip("/") + relative_url
        except FileNotFoundError:
            return base_url

    def on_run(self):
        set_setting("virttrip", "minimum_score", str(self.min_score_spin.value()))
        # Build menu_spec dynamically so the current minimum_score is captured
        menu_spec = _virttrip_spec(
            batch.svv3WriteBlog.main,
            "Writing virtual trip blogs...",
            "✅ All blogs written and uploaded.",
        )
        menu_spec.batch_parms["extra_config"] = [
            ("blogconfig", "minimum_score", self.min_score_spin.value()),
        ]

        def on_success():
            populate_virttrip_overview(self.passed_parent)
            if self.open_after_checkbox.isChecked():
                webbrowser.open(self.get_blog_url())

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(menu_spec, on_success=on_success)


class VirttripBuildWebsiteDialog(QDialog):
    """Run svv4BuildWebsite: generate species HTML pages for the virtual trip."""

    def __init__(self, parent):
        super().__init__(parent)
        self.logger        = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Build Virtual Trip Website")
        self.resize(560, 260)

        layout = QVBoxLayout(self)

        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data = read_virttrip_ini(virttrip_root) if virttrip_root else {}
        trip_name = (trip_data or {}).get("trip_name", Path(virttrip_root).name if virttrip_root else "?")

        layout.addWidget(HTMLLabel(
            "<p><b>Build Virtual Trip Website</b></p>"
            "<p>Generates species HTML pages (NL + EN) for the virtual trip "
            "using pictures extracted from the master. No SFTP — local build only, "
            "identical to the regular trip's Build Website step.</p>"
            "<p>Requires 'Extract Days from Master' to have been run first.</p>"
        ))

        layout.addWidget(QLabel(f"Virtual trip:  {virttrip_root or '(not set)'}"))
        layout.addWidget(QLabel(f"Trip name:     {trip_name}"))

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        run_btn   = QPushButton("Build Website")
        close_btn = QPushButton("Close")
        btn_layout.addWidget(run_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.menu_spec = _virttrip_spec(
            batch.svv4BuildWebsite.main,
            "Building virtual trip species pages...",
            "✅ Virtual trip website built.",
        )
        run_btn.clicked.connect(self.on_run)
        close_btn.clicked.connect(self.close)

    def on_run(self):
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)
