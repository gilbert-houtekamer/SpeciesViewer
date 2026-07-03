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
import batch.svv5AddToMaster
import batch.svv6RedoBlog
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


def _common_blog_min_score(virttrip_root):
    """If every day's blog ini shares the same minimum_score, return it; else
    None. Lets the Write-Blogs dialog default to the value the days actually use
    instead of a hard-coded 25."""
    import configparser as _cp
    import glob as _glob
    blog_dir = os.path.join(virttrip_root, "metadata", "blog")
    if not os.path.isdir(blog_dir):
        return None
    scores = set()
    for fn in _glob.glob(os.path.join(blog_dir, "blog_*.ini")):
        cfg = _cp.RawConfigParser()
        try:
            cfg.read(fn, encoding="utf-8")
            scores.add(cfg.getint("blog_config", "minimum_score", fallback=25))
        except Exception:
            pass
    return scores.pop() if len(scores) == 1 else None


def _trip_picture_root(trip_db):
    """The CurrentPictures root of a trip (parent of its dated day folders),
    derived from any WindowsFileName in the trip's per-day data_files.csv."""
    import csv as _csv
    review = os.path.join(trip_db, "metadata", "review")
    if not os.path.isdir(review):
        return None
    for d in sorted(os.listdir(review)):
        fp = os.path.join(review, d, "data_files.csv")
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, encoding="utf-8", errors="replace", newline="") as fh:
                for row in _csv.DictReader(fh, delimiter=";"):
                    wd = (row.get("WindowsFileName") or "").strip()
                    if wd:
                        return str(Path(wd).parent.parent)
        except Exception:
            pass
    return None


def retire_trip_to_virttrip(parent):
    """One action: create the successor virtual trip from the ACTIVE trip (same
    name, date range and trip text), build its tree (svv1), extract its days
    from the master (svv2), and migrate the day blogs — so the virtual-trip blog
    comes out identical to the original trip blog. (svr7 delete stays separate.)"""
    import re as _re
    import argparse as _argparse
    import configparser as _cp
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtCore import Qt
    from utils.batch_config import generate_svconfig_ini
    import engine.blogmigrate as blogmigrate

    logger = parent.logger
    trip_db = _config.get("trip", "tripbiotadatabase", fallback="").replace('"', "").replace("'", "")
    if not trip_db or not os.path.isdir(trip_db):
        QMessageBox.warning(parent, "Retire Trip", "Select an active trip first.")
        return
    trip_ini = os.path.join(trip_db, "trip.ini")
    if not os.path.isfile(trip_ini):
        QMessageBox.warning(parent, "Retire Trip", f"No trip.ini found in {trip_db}.")
        return

    tc = _cp.ConfigParser()
    try:
        tc.read(trip_ini, encoding="utf-8")
    except Exception as e:
        QMessageBox.warning(parent, "Retire Trip", f"Could not read trip.ini: {e}")
        return

    def g(sec, key, fb=""):           # config value with inline-comment stripped
        try:
            return tc.get(sec, key, fallback=fb).split(";")[0].strip()
        except Exception:
            return fb

    def raw(sec, key, fb=""):         # raw text (trip_text may contain ';')
        try:
            return tc.get(sec, key, fallback=fb)
        except Exception:
            return fb

    leaf = Path(trip_db).name
    trip_name = (g("trip_config", "trip_name") or leaf).strip("/").rsplit("/", 1)[-1] or leaf
    fromdate = g("trip_config", "fromdate")
    todate = g("trip_config", "todate")
    if not (fromdate and todate):
        review = os.path.join(trip_db, "metadata", "review")
        days = sorted(d for d in (os.listdir(review) if os.path.isdir(review) else [])
                      if _re.match(r"^\d{4}-\d{2}-\d{2}$", d))
        if days:
            fromdate, todate = fromdate or days[0], todate or days[-1]
    if not (fromdate and todate):
        QMessageBox.warning(parent, "Retire Trip", "Could not determine the trip's date range.")
        return

    web_root = (_config.get("trip", "tripbiotawebroot", fallback="").replace('"', "").replace("'", "")
                or f"/trips/{trip_name}")
    banner = g("trip_config", "trip_banner") or "keerkringvogel.jpg"
    picture = g("trip_config", "trip_picture")
    title_nl, message_nl = raw("trip_text", "title_nl", trip_name), raw("trip_text", "message_nl")
    title_en, message_en = raw("trip_text", "title_en", trip_name), raw("trip_text", "message_en")

    virttrips_root = get_virttrips_root() or str(Path(get_trips_root() or trip_db).parent / "virttrips")
    virttrip_root = os.path.join(virttrips_root, leaf)

    exists = os.path.isfile(os.path.join(virttrip_root, "virttrip.ini"))
    prompt = (f"A virtual trip already exists at {virttrip_root}. Rebuild it?"
              if exists else
              f"Create successor virtual trip from '{trip_name}'?\n\n"
              f"Virtual trip:  {virttrip_root}\nWeb:  {web_root}\nRange:  {fromdate} → {todate}\n\n"
              f"Creates the virttrip, extracts its days from the master, and migrates the blogs.")
    if QMessageBox.question(parent, "Retire Trip to Virtual Trip", prompt,
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
        return

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    copied = no_blog = 0
    try:
        set_setting("virttrip", "virttriprootdirectory", str(Path(virttrip_root).parent))
        set_setting("virttrip", "virttripbiotadatabase", virttrip_root)
        set_setting("virttrip", "virttripbiotawebroot",
                    web_root if web_root.endswith("/") else web_root + "/")
        os.makedirs(virttrip_root, exist_ok=True)
        generate_virttrip_ini(virttrip_root, web_root, banner, picture, fromdate, todate,
                              title_nl, message_nl, title_en, message_en, logger)
        generate_svconfig_ini(virttrip_mode=True)
        mcfg = _cp.ConfigParser()
        mcfg.read("svconfig.ini")
        args = _argparse.Namespace(config="svconfig.ini", dir="")
        batch.svv1MakeVirtualRepository.main(args, mcfg)
        batch.svv2ExtractDays.main(args, mcfg)
        trip_pic_root = _trip_picture_root(trip_db)
        if trip_pic_root:
            copied, _kept, no_blog = blogmigrate.migrate_trip_blogs(trip_pic_root, virttrip_root)
        else:
            logger.warning("retire: could not locate the trip's picture folder; blogs not migrated")
        generate_svconfig_ini()       # restore active-trip context
    except Exception as e:
        QApplication.restoreOverrideCursor()
        logger.warning(f"Retire trip failed: {e}")
        QMessageBox.critical(parent, "Retire Trip", f"Failed: {e}")
        return
    finally:
        QApplication.restoreOverrideCursor()

    try:
        populate_virttrip_overview(parent)
        parent.virttrip_panel.rebuild()
    except Exception:
        pass
    QMessageBox.information(
        parent, "Retire Trip to Virtual Trip",
        f"Successor virtual trip '{trip_name}' created and days extracted.\n"
        f"Migrated {copied} day blog(s); {no_blog} day(s) had none (nothing fabricated).\n\n"
        f"Next: Virtual Trips → Write Virtual Trip Blogs, then compare to the trip blog.")


def _clear_virttrip_blogs(virttrip_root, web_root, pwd, logger):
    """Remove stale blog artifacts before a fresh write: the local biotablog
    HTML dir, blog_admin.csv, the FTP upload cache (so everything re-uploads),
    and the published biotablog dir on the web. Old per-min-rating runs each
    produced differently-named pages, so without this they accumulate (doubled
    days, leftover garbage on the site)."""
    import shutil
    biotablog = os.path.join(virttrip_root, "web", "biotablog")
    try:
        if os.path.isdir(biotablog):
            shutil.rmtree(biotablog, ignore_errors=True)
        os.makedirs(biotablog, exist_ok=True)
    except Exception as e:
        logger.warning(f"clear blogs: local biotablog: {e}")
    for rel in ("admin/blog_admin.csv", "admin/FTPcache.json"):
        try:
            os.remove(os.path.join(virttrip_root, *rel.split("/")))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"clear blogs: {rel}: {e}")
    if pwd and web_root:
        try:
            import engine.sftplib as sftplib
            s = sftplib.Sftp(hostname="houtfp.ssh.transip.me",
                             username="houtekamercom", password=pwd)
            s.connect()
            remote = s.remotePrefix() + web_root.rstrip("/") + "/biotablog"
            s.rmtree(remote)
            s.disconnect()
            logger.info(f"clear blogs: removed remote {remote}")
        except Exception as e:
            logger.warning(f"clear blogs: remote biotablog: {e}")


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
        # Web namespace matches regular trips — to a visitor a virtual trip is
        # just a trip (and it typically continues a retired trip at the same
        # /trips/<name>/ URL, where its blog + pictures already live).
        self.trip_web.set_text(f"/trips/{folder_name}")
        self.status_label.setText(f"✅ Created: {new_trip_path}")
        self.logger.info(f"Created new virtual trip folder: {new_trip_path}")

    def on_trip_root_changed(self, new_path):
        trip_data = read_virttrip_ini(new_path)
        if not trip_data:
            return
        trip_name = trip_data["trip_name"]
        if "trips" not in trip_name:
            trip_name = "/trips/" + trip_name
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
        runner.run(self.menu_spec, on_success=self._refresh_panels)

    def _refresh_panels(self):
        populate_virttrip_overview(self.passed_parent)
        self.passed_parent.virttrip_panel.rebuild()

    def on_select(self):
        set_setting("virttrip", "virttriprootdirectory", str(Path(self.root_field.get_text()).parent))
        set_setting("virttrip", "virttripbiotadatabase", self.root_field.get_text())
        set_setting("virttrip", "virttripbiotawebroot",  self.trip_web.field.text())
        populate_virttrip_overview(self.passed_parent)
        self.passed_parent.virttrip_panel.rebuild()
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

        common_score = _common_blog_min_score(virttrip_root) if virttrip_root else None

        layout.addWidget(HTMLLabel(
            "<p><b>Write Virtual Trip Blogs</b></p>"
            "<p>For every day that has extracted data:</p>"
            "<ul>"
            "<li>Reads each day's <code>metadata/blog/blog_YYYY-MM-DD.ini</code> "
            "(migrated from the trip or hand-edited) and uploads NL + EN blog HTML pages.</li>"
            "<li>A day with no blog ini is skipped — nothing is fabricated.</li>"
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
        self.min_score_spin.setMinimumWidth(90)
        self.min_score_spin.setValue(
            common_score if common_score is not None
            else int(_config.get("virttrip", "minimum_score", fallback="25")))
        self.min_score_spin.setToolTip(
            "Pictures with a computed rating below this value are excluded from the blog.\n"
            "Lower this if blog pages appear empty."
        )
        score_row.addWidget(self.min_score_spin)
        if common_score is not None:
            note = QLabel(f"(all days currently use {common_score})")
            note.setStyleSheet("color:#666;")
            score_row.addWidget(note)
        score_row.addStretch()
        layout.addLayout(score_row)

        self.clear_first_checkbox = QCheckBox(
            "Clear old blog pages first (wipes biotablog + blog_admin.csv, "
            "local and on the web — removes stale/duplicate entries)")
        self.clear_first_checkbox.setChecked(False)
        layout.addWidget(self.clear_first_checkbox)

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
        if self.clear_first_checkbox.isChecked():
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtCore import Qt
            vt = _config.get("virttrip", "virttripbiotadatabase", fallback="").replace('"', "").replace("'", "")
            wr = _config.get("virttrip", "virttripbiotawebroot", fallback="")
            pwd = _config.get("websettings", "sftppwd", fallback="")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                _clear_virttrip_blogs(vt, wr, pwd, self.logger)
            finally:
                QApplication.restoreOverrideCursor()
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


class VirttripRedoBlogDialog(QDialog):
    """Run svv6RedoBlog: recreate blog HTML (species links + complete Previous/Next
    navigation) for every published day — no JPGs are moved or re-uploaded."""

    def __init__(self, parent):
        super().__init__(parent)
        self.logger        = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Make the blogs clickable")
        self.resize(580, 300)

        layout = QVBoxLayout(self)

        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data  = read_virttrip_ini(virttrip_root) if virttrip_root else {}
        trip_name  = (trip_data or {}).get("trip_name", Path(virttrip_root).name if virttrip_root else "?")
        # Count days that will be recreated: extracted day dirs with a blog ini
        # (the same set svv6 targets — not blog_admin.csv, which may be absent).
        blog_count = 0
        if virttrip_root:
            review_root = Path(virttrip_root) / "metadata" / "review"
            blog_dir    = Path(virttrip_root) / "metadata" / "blog"
            if review_root.is_dir():
                blog_count = sum(
                    1 for d in review_root.iterdir()
                    if d.is_dir() and (d / "data_files.csv").exists()
                    and (blog_dir / f"blog_{d.name}.ini").exists()
                )

        layout.addWidget(HTMLLabel(
            "<p><b>Make the blogs clickable</b> — add species links &amp; navigation</p>"
            "<p>Re-renders the blog pages that <i>Write Virtual Trip Blogs</i> already "
            "published, rewriting only the HTML:</p>"
            "<ul>"
            "<li>Adds the full <b>Previous / Next</b> navigation between days "
            "(the initial write can only link backwards).</li>"
            "<li>Refreshes the species-reference hyperlinks on every card.</li>"
            "</ul>"
            "<p><b>No pictures are moved or re-uploaded</b> — the day thumbnails already "
            "on the web are left untouched. Run this after all days have been written.</p>"
        ))

        layout.addWidget(QLabel(f"Virtual trip:  {virttrip_root or '(not set)'}"))
        layout.addWidget(QLabel(f"Trip name:     {trip_name}"))
        layout.addWidget(QLabel(f"Day blogs:     {blog_count}"))

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.open_after_checkbox = QCheckBox("Open trip blog in browser after recreating")
        self.open_after_checkbox.setChecked(True)
        layout.addWidget(self.open_after_checkbox)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        run_btn   = QPushButton("Make clickable")
        close_btn = QPushButton("Close")
        btn_layout.addWidget(run_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.menu_spec = _virttrip_spec(
            batch.svv6RedoBlog.main,
            "Adding species links & navigation to the blogs (HTML only)...",
            "✅ Blogs are now clickable (species links + Previous/Next navigation).",
        )
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
        def on_success():
            populate_virttrip_overview(self.passed_parent)
            if self.open_after_checkbox.isChecked():
                webbrowser.open(self.get_blog_url())

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=on_success)


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
        runner.run(self.menu_spec,
                   on_success=lambda: populate_virttrip_overview(self.passed_parent))


class VirttripAddToMasterDialog(QDialog):
    """Run svv5AddToMaster: register the virtual trip in the master trip index."""

    def __init__(self, parent):
        super().__init__(parent)
        self.logger        = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Add Virtual Trip to Master")
        self.resize(580, 280)

        layout = QVBoxLayout(self)

        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data = read_virttrip_ini(virttrip_root) if virttrip_root else {}
        trip_name = (trip_data or {}).get("trip_name", Path(virttrip_root).name if virttrip_root else "?")
        master_db = _config.get("master", "masterbiotadatabase", fallback="")

        layout.addWidget(HTMLLabel(
            "<p><b>Add Virtual Trip to Master</b></p>"
            "<p>Registers this virtual trip in the master trip index so it appears "
            "on the website alongside regular trips:</p>"
            "<ul>"
            "<li>Adds (or refreshes) the trip row in the master <code>trip_admin.csv</code>.</li>"
            "<li>Regenerates the master <code>tripindex-nl.html</code> and "
            "<code>tripindex-en.html</code> pages.</li>"
            "<li>Uploads the updated index pages and trip admin CSV via SFTP.</li>"
            "</ul>"
            "<p>Run this once after the trip is complete and the blog + website are ready. "
            "Re-running is safe — the existing entry is replaced in place.</p>"
        ))

        layout.addWidget(QLabel(f"Virtual trip:  {virttrip_root or '(not set)'}"))
        layout.addWidget(QLabel(f"Trip name:     {trip_name}"))
        layout.addWidget(QLabel(f"Master:        {master_db or '(not set)'}"))

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.local_checkbox = QCheckBox(
            "Read from local trip_admin.csv instead of web (use after manual cleanup)")
        self.local_checkbox.setChecked(False)
        self.local_checkbox.setToolTip(
            "Normally the live web copy is used as the authoritative source.\n"
            "Enable this only when you have manually edited the local trip_admin.csv\n"
            "to remove unwanted entries and want to re-upload that cleaned version.")
        layout.addWidget(self.local_checkbox)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        run_btn   = QPushButton("Add to Master")
        close_btn = QPushButton("Close")
        btn_layout.addWidget(run_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        run_btn.clicked.connect(self.on_run)
        close_btn.clicked.connect(self.close)

    def on_run(self):
        menu_spec = _virttrip_spec(
            batch.svv5AddToMaster.main,
            "Registering virtual trip in master trip index...",
            "✅ Virtual trip added to master trip index and uploaded.",
        )
        if self.local_checkbox.isChecked():
            menu_spec.batch_parms["extra_config"] = [
                ("virttrip_addtomaster", "read_from_local", "true"),
            ]
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(menu_spec)
