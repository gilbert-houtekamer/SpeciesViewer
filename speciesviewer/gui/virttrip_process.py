import webbrowser
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QSpinBox, QTabWidget, QWidget, QLineEdit, QFileDialog, QApplication,
    QComboBox, QMessageBox)
from PyQt6.QtCore import QThread, QObject, pyqtSignal, Qt
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from datetime import datetime
from pathlib import Path

from gui.batch import BatchModuleRunner
from utils.config import set_setting, _config, get_virttrips_root, get_trips_root
from utils.runtime_files import svconfig_path
from utils.virttrip_config import read_virttrip_ini
from gui.virttrip_overview import populate_virttrip_overview
import batch.svv3WriteBlog
import batch.svv4BuildWebsite
import batch.svv5AddToMaster
import batch.svv6RedoBlog
from gui.menu import MenuSpec
from widgets.small_widgets import HTMLLabel
from widgets.trip_widgets import (LabeledTextFieldQt, DateRangeFieldQt,
                                   RecentDirectoryFieldQt, DirectoryFieldQt, ImageFileFieldQt,
                                   image_field_row)

import batch.svv1MakeVirtualRepository
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")


def require_full_master(parent) -> bool:
    """True when this machine holds the FULL master and may AUTHOR albums.

    An album is a live query over the WHOLE master by date range, so creating or
    redesigning one needs the complete master. A SmallMaster install
    (master.readonly_locked = yes) holds only a JPG-bearing subset — it can VIEW and
    PUBLISH albums, but designing one against a partial master would silently drop
    pictures. Returns False (with a plain-language note) on such a machine so the
    caller can abort before opening the authoring dialog."""
    locked = _config.get("master", "readonly_locked",
                         fallback="no").strip().lower() == "yes"
    if locked:
        QMessageBox.information(
            parent, "Design albums on the main machine",
            "This machine holds a partial copy of the master (a SmallMaster install), "
            "so it can view and publish albums but not create or redesign them.\n\n"
            "An album is a live query over the WHOLE master by date range, which isn't "
            "available here — designing one would silently leave pictures out. Create "
            "and edit albums on the machine that has the full master.")
        return False
    return True


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
    logger,
    source_trip_id: str = "",
):
    ini_path = os.path.join(trip_root, "virttrip.ini")

    master_web  = _config.get("master", "masterbiotawebroot",  fallback="/nature/")
    master_disk = _config.get("master", "masterbiotadatabase", fallback="")
    trip_name   = Path(trip_name_path).name
    # WP4: explicit identities. trip_id = the virttrip's own folder basename;
    # web_slug = the URL/registry key; source_trip_id = the explicit link back
    # to the origin trip (empty for a retroactive/hand-created virttrip).
    trip_id  = os.path.basename(os.path.normpath(trip_root))
    web_slug = trip_name

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
trip_id = {trip_id}
web_slug = {web_slug}
source_trip_id = {source_trip_id}
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
            read_ini(cfg, fn)
            scores.add(cfg.getint("blog_config", "minimum_score", fallback=25))
        except Exception:
            pass
    return scores.pop() if len(scores) == 1 else None


_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _has_day_subdirs(base):
    """True if `base` is a directory that directly contains YYYY-MM-DD folders
    (i.e. it is a trip's CurrentPictures root, where each day's blog.ini lives)."""
    try:
        return os.path.isdir(base) and any(
            _DAY_RE.match(d) and os.path.isdir(os.path.join(base, d))
            for d in os.listdir(base))
    except OSError:
        return False


def _trip_picture_root(trip_db):
    """The CurrentPictures root of a trip — the folder that holds its dated day
    folders, and thus each day's blog.ini.

    Preferred source is the active trip's [project] picturedirectory (a day
    folder), whose parent is the trip root. Since the master-relativization
    change, the WindowsFileName column stores a path RELATIVE to that root
    (e.g. /2026-06-26/foo.xmp), so the old parent.parent trick collapses to the
    filesystem root — kept only as a legacy fallback for absolute paths."""
    import csv as _csv
    from utils.trip_identity import resolve_trip_picture_root

    # 0) WP8: the trip's own explicit picture_root (self-contained), resolved
    #    against the per-machine CurrentPictures base. active_picturedirectory=""
    #    disables the day-parent fallback here so this only fires on an explicit
    #    picture_root; the config-driven path below stays the fallback.
    explicit_root = resolve_trip_picture_root(trip_db, kind="trip",
                                              active_picturedirectory="")
    if explicit_root and _has_day_subdirs(explicit_root):
        return explicit_root

    # 1) Config-driven: picturedirectory is the active trip's day folder; its
    #    parent is the trip root. Accept it directly if it already holds days.
    pic_dir = (_config.get("project", "picturedirectory", fallback="")
               .strip().strip('"').strip("'"))
    if pic_dir:
        cand = Path(pic_dir)
        for base in (cand, cand.parent):
            if _has_day_subdirs(str(base)):
                return str(base)

    # 2) Legacy: an ABSOLUTE WindowsFileName in the trip's data_files.csv.
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
                    if wd and os.path.isabs(wd):
                        root = str(Path(wd).parent.parent)
                        if _has_day_subdirs(root):
                            return root
        except Exception:
            pass
    return None


class _RetireVirttripWorker(QObject):
    """Runs the ordered retire→virttrip pipeline steps in a worker thread,
    stopping on the first exception. Each step is a (label, callable); the
    callable may return a short note string that is collected into the result.
    `progress` fires before each step; `finished` reports ("" on success, or the
    failing step's label) plus the collected notes."""

    progress = pyqtSignal(int, int, str)   # index, total, label
    finished = pyqtSignal(str, str)        # failed_label ("" = ok), result_notes

    def __init__(self, steps):
        super().__init__()
        self._steps = steps

    def run(self):
        import traceback
        notes = []
        total = len(self._steps)
        for idx, (label, fn) in enumerate(self._steps):
            self.progress.emit(idx, total, label)
            try:
                note = fn()
                if note:
                    notes.append(note)
            except Exception as err:
                from engine.exceptions import SVException
                log = logging.getLogger("speciesviewer")
                if isinstance(err, SVException):
                    # An expected stop with a message meant for the user
                    # ("No pictures found in the album's date range"): the
                    # sentence, not a traceback.
                    log.warning(f"“{label}” stopped: {err}")
                else:
                    log.warning(f"Step '{label}' failed: {err}\n{traceback.format_exc()}")
                # The reason leads the notes, so the message box says WHY.
                self.finished.emit(label, "\n".join([str(err)] + notes))
                return
        self.finished.emit("", "\n".join(notes))


def _run_virttrip_pipeline(parent, steps, *, title, success_msg, wait_picture=None):
    """Run an ordered list of (label, callable) album-pipeline steps in a worker
    thread behind a shared spinner. Used by retire-to-album AND Create Album's
    full-publish finish, so the two flows share one runner instead of two copies
    of the spinner/thread/finish plumbing.

    On completion it restores the active-trip svconfig context and refreshes the
    album overview + panel (both flows need this), then shows `success_msg` — or,
    on failure, names the failing step and says the completed steps are safe to
    repeat. `steps` and the config must already be set up by the caller (svconfig
    in virttrip_mode, mcfg/args built) exactly as before.
    """
    from PyQt6.QtGui import QMovie
    from utils.batch_config import generate_svconfig_ini

    logger = parent.logger
    spinner = QDialog(parent)
    spinner.setWindowTitle(title)
    spinner.setModal(True)
    from gui.wait_picture import wait_layout
    slayout = wait_layout(spinner, wait_picture, 480, 180)
    step_label = QLabel("Starting…")
    step_label.setWordWrap(True)
    slayout.addWidget(step_label, alignment=Qt.AlignmentFlag.AlignCenter)
    progress_label = QLabel("")
    slayout.addWidget(progress_label, alignment=Qt.AlignmentFlag.AlignCenter)
    movie = QMovie("spinner.gif")
    movie_label = QLabel()
    movie_label.setMovie(movie)
    slayout.addWidget(movie_label, alignment=Qt.AlignmentFlag.AlignCenter)
    movie.start()

    thread = QThread()
    worker = _RetireVirttripWorker(steps)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    def _on_progress(idx, total, label):
        step_label.setText(f"Step {idx + 1} of {total}: {label}…")
        progress_label.setText(f"{idx + 1} / {total}")

    def _on_finished(failed_label, result_notes):
        thread.quit()
        thread.wait()
        movie.stop()
        spinner.close()
        try:
            generate_svconfig_ini()       # restore active-trip context
        except Exception:
            pass
        try:
            populate_virttrip_overview(parent)
            parent.virttrip_panel.rebuild()
        except Exception:
            pass
        parent._vtpipe_thread = None
        parent._vtpipe_worker = None
        if failed_label:
            logger.warning(f"Album pipeline stopped at: {failed_label}")
            QMessageBox.critical(
                parent, title,
                f"Stopped at “{failed_label}”.\n\n"
                + (result_notes + "\n\n" if result_notes else "")
                + "See the status log for details. Fix the problem and run again — "
                  "the steps that already completed are safe to repeat.")
        else:
            QMessageBox.information(
                parent, title,
                success_msg + ("\n\n" + result_notes if result_notes else ""))

    worker.progress.connect(_on_progress)
    worker.finished.connect(_on_finished)
    parent._vtpipe_thread = thread   # keep refs alive until finished
    parent._vtpipe_worker = worker
    thread.start()
    spinner.show()


class RetireTripDialog(QDialog):
    """Confirm + parametrise retiring a trip into its successor virtual trip.

    The display title is editable (defaults from the trip's own titles); the web
    address is shown read-only because the blog + pictures are already published
    there — changing the slug would orphan live content. Also shows the explicit
    source_trip_id link that will tie the new virttrip back to this trip, so the
    retire matcher never has to guess from the folder basename again."""

    def __init__(self, parent, *, trip_name, trip_id, web_root, virttrip_root,
                 fromdate, todate, title_nl, title_en, exists):
        super().__init__(parent)
        self.setWindowTitle("Retire Trip to Album")
        self.setModal(True)
        self.resize(580, 0)
        layout = QVBoxLayout(self)

        header = ("An album already exists — it will be rebuilt end-to-end."
                  if exists else
                  f"Create the successor album from “{trip_name}”.")
        layout.addWidget(HTMLLabel(f"<p>{header}</p>"))

        self.title_nl_field = LabeledTextFieldQt(
            "Display title (Dutch)", title_nl, multiline=False, inline=True)
        self.title_en_field = LabeledTextFieldQt(
            "Display title (English)", title_en, multiline=False, inline=True)
        layout.addWidget(self.title_nl_field)
        layout.addWidget(self.title_en_field)

        def ro(label, value):
            row = QLabel(f"<b>{label}:</b> <code>{value}</code>")
            row.setTextFormat(Qt.TextFormat.RichText)
            row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(row)

        ro("Web address (fixed)", web_root)
        ro("Album folder", virttrip_root)
        ro("Link back to trip", f"source_trip_id = {trip_id}")
        ro("Date range", f"{fromdate} → {todate}")

        layout.addWidget(HTMLLabel(
            "<p>Runs every step in one go — extract days, migrate &amp; upload the "
            "blogs, build the website, make the blogs clickable, and add the trip to "
            "the master index. Stops automatically if a step fails.</p>"
            "<p>The web address is fixed: the blog and pictures are already published "
            "there, so it can't be changed here.</p>"))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        ok_btn = QPushButton("Rebuild Album" if exists else "Retire to Album")
        ok_btn.setDefault(True)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)
        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self.accept)

    def edited_titles(self):
        return self.title_nl_field.get_text(), self.title_en_field.get_text()


def retire_trip_to_virttrip(parent):
    """One action: create the successor virtual trip from the ACTIVE trip (same
    name, date range and trip text) and take it all the way to a complete,
    published virtual trip:

        svv1  make the virttrip tree
        svv2  extract its days from the master
        ----  migrate the day blogs (so the virttrip blog matches the trip blog)
        svv3  write & upload the day blogs (NL + EN + pictures)
        svv4  build the species website and upload the biota tree
        svv6  make the blogs clickable (species links + Prev/Next navigation)
        svv5  add / refresh the trip in the master trip index

    Runs the steps back-to-back in a worker thread with one spinner, stopping on
    the first error (completed steps are safe to repeat on a re-run). The svr7
    local-file delete stays a separate, later action."""
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
        read_ini(tc, trip_ini)
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

    # Resolve the three explicit identities once (WP4). trip_id = the on-disk
    # folder basename; web_slug = the URL/registry key. Both fall back to today's
    # derivation for pre-WP4 trip.ini, so nothing changes for existing trips.
    from utils.trip_identity import resolve as resolve_identity
    ident = resolve_identity(trip_db, kind="trip")
    leaf = ident.trip_id or Path(trip_db).name
    trip_id = leaf
    trip_name = ident.web_slug or leaf          # the web slug (may differ from leaf)
    fromdate = g("trip_config", "fromdate")
    todate = g("trip_config", "todate")
    # The trip's days are the truth. The stored range can be a default the trip
    # was created with (Zwin, 2026-10-17: stored 2026-10-31..11-14, pictures of
    # 2025-09-13..14), and an album built on it finds no pictures at all. A stored
    # range that covers every day stays — it may be wider on purpose.
    review = os.path.join(trip_db, "metadata", "review")
    days = sorted(d for d in (os.listdir(review) if os.path.isdir(review) else [])
                  if _re.match(r"^\d{4}-\d{2}-\d{2}$", d))
    if days and not (fromdate and todate and fromdate <= days[0] and days[-1] <= todate):
        if fromdate and todate:
            logger.warning(f"Retire Trip: the trip's stored dates {fromdate} → {todate} do not "
                           f"cover its days {days[0]} → {days[-1]}; the album uses the days.")
        fromdate, todate = days[0], days[-1]
    if not (fromdate and todate):
        QMessageBox.warning(parent, "Retire Trip", "Could not determine the trip's date range.")
        return

    web_root = (_config.get("trip", "tripbiotawebroot", fallback="").replace('"', "").replace("'", "")
                or f"/trips/{trip_name}")
    banner = g("trip_config", "trip_banner") or "keerkringvogel.jpg"
    picture = g("trip_config", "trip_picture")
    title_nl, message_nl = raw("trip_text", "title_nl", trip_name), raw("trip_text", "message_nl")
    title_en, message_en = raw("trip_text", "title_en", trip_name), raw("trip_text", "message_en")

    # The album goes in the Albums folder. None set: offer the Setup Checklist's
    # suggestion rather than inventing a third name (it used to land silently in
    # a "virttrips" folder beside the Trips folder — 2026-10-12).
    virttrips_root = get_virttrips_root()
    if not virttrips_root:
        from gui.setup_checklist import use_suggested_folder
        from utils import default_folders
        suggested = default_folders.albums_folder()
        if QMessageBox.question(
                parent, "Retire Trip to Album",
                f"There is no Albums folder yet.\n\nUse {suggested}?\n\n"
                "(Or choose another one first: Help ▸ Setup Checklist ▸ Albums folder.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return
        if not use_suggested_folder(parent, "Albums folder"):
            return
        virttrips_root = str(suggested)
    virttrip_root = os.path.join(virttrips_root, leaf)

    exists = os.path.isfile(os.path.join(virttrip_root, "virttrip.ini"))
    dlg = RetireTripDialog(
        parent, trip_name=trip_name, trip_id=trip_id, web_root=web_root,
        virttrip_root=virttrip_root, fromdate=fromdate, todate=todate,
        title_nl=title_nl, title_en=title_en, exists=exists)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    title_nl, title_en = dlg.edited_titles()   # user may have edited the display title

    # ── Config setup on the GUI thread (mutates shared _config / svconfig.ini),
    # then the pipeline runs in a worker thread so the spinner stays live. ──
    try:
        from utils.config import root_is_unset
        if root_is_unset("virttrip", "virttriprootdirectory"):
            set_setting("virttrip", "virttriprootdirectory", str(Path(virttrip_root).parent))
        set_setting("virttrip", "virttripbiotadatabase", virttrip_root)
        set_setting("virttrip", "virttripbiotawebroot",
                    web_root if web_root.endswith("/") else web_root + "/")
        os.makedirs(virttrip_root, exist_ok=True)
        # The trip's banner + picture live as named files in the trip root
        # (e.g. <trip>/bannermeeuw.jpg). virttrip.ini only stores their names,
        # so copy the actual files across — otherwise svv5AddToMaster can't find
        # them under the virttrip root and the site falls back to a default
        # banner. Don't clobber an image already present (hand-edited / restored).
        for fname in (banner, picture):
            if not fname:
                continue
            src = os.path.join(trip_db, fname)
            dst = os.path.join(virttrip_root, fname)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
                logger.info(f"retire: copied trip image {fname} → {virttrip_root}")
            elif not os.path.isfile(src) and not os.path.exists(dst):
                logger.warning(
                    f"retire: trip image '{fname}' not found in the trip root "
                    f"({trip_db}); copy it into {virttrip_root} manually or the "
                    f"website will use a default banner.")
        generate_virttrip_ini(virttrip_root, web_root, banner, picture, fromdate, todate,
                              title_nl, message_nl, title_en, message_en, logger,
                              source_trip_id=trip_id)
        generate_svconfig_ini(virttrip_mode=True)
        mcfg = _cp.ConfigParser()
        read_ini(mcfg, svconfig_path())
    except Exception as e:
        logger.warning(f"Retire trip setup failed: {e}")
        QMessageBox.critical(parent, "Retire Trip", f"Setup failed: {e}")
        return
    args = _argparse.Namespace(config=str(svconfig_path()), dir="")
    trip_pic_root = _trip_picture_root(trip_db)

    def _migrate_blogs():
        if not trip_pic_root:
            logger.warning("retire: could not locate the trip's picture folder; blogs not migrated")
            return "No trip picture folder found — blogs were not migrated."
        copied, _kept, no_blog = blogmigrate.migrate_trip_blogs(trip_pic_root, virttrip_root)
        return (f"Migrated {copied} day blog(s); "
                f"{no_blog} day(s) had none (nothing fabricated).")

    import datetime as _dt

    def _record_retirement():
        # Stamp the milestone the menu gating reads (Delete Trip enables once the
        # trip is both merged AND retired). Never raises — it is the last step and
        # a failure here must not fail a retirement that already completed.
        try:
            from utils.trip_config import set_trip_flag
            set_trip_flag(trip_db, "retired_to_album",
                          _dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
        except Exception as e:
            logger.warning(f"retire: could not record the retired_to_album milestone: {e}")
        return None

    steps = [
        ("Creating the album",          lambda: batch.svv1MakeVirtualRepository.main(args, mcfg)),
        ("Migrating the trip blogs",           _migrate_blogs),
        ("Writing and uploading the blogs",    lambda: batch.svv3WriteBlog.main(args, mcfg)),
        ("Building the website",               lambda: batch.svv4BuildWebsite.main(args, mcfg)),
        ("Making the blogs clickable",         lambda: batch.svv6RedoBlog.main(args, mcfg)),
        ("Adding the trip to the master index", lambda: batch.svv5AddToMaster.main(args, mcfg)),
        ("Recording retirement",               _record_retirement),
    ]

    logger.info(f"Retiring trip '{trip_name}' into an album…")
    _run_virttrip_pipeline(
        parent, steps,
        title="Retire Trip to Album",
        wait_picture="tortoise",       # the longest wait in SV
        success_msg=f"Album “{trip_name}” is complete.\n\nThe blogs, website and "
                    f"master trip index have all been built and uploaded.")


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
            s = sftplib.open_site(password=pwd)
            s.connect()
            remote = s.remotePrefix() + web_root.rstrip("/") + "/biotablog"
            s.rmtree(remote)
            s.disconnect()
            logger.info(f"clear blogs: removed remote {remote}")
        except Exception as e:
            logger.warning(f"clear blogs: remote biotablog: {e}")


def _master_pictures_in_range(start: str, end: str) -> int | None:
    """How many master pictures an album over [start, end] would hold; None
    when that cannot be told (no master, or the query failed) — then nothing
    is refused on its account."""
    master = (_config.get("master", "masterbiotadatabase", fallback="") or "").strip().strip('"\'')
    if not master:
        return None
    try:
        from db.master_db import MasterDB
        db = MasterDB(master)
        db.ensure_fresh()
        return sum(len(pics) for pics in db.pictures_in_daterange(start, end).values())
    except Exception as e:
        logging.getLogger("speciesviewer").info(f"Create Album: picture count failed: {e}")
        return None


class VirtualTripCreationDialog(QDialog):
    def __init__(self, parent, menu_spec, preset: dict | None = None, mode="create"):
        """preset (optional): {"folder", "fromdate", "todate", "title_nl", "note"}
        — prefills the dialog from a picked row of the "Find Trips in the Master"
        report, so the date range is not typed from memory. Every prefilled field
        stays editable. Nothing is created until Create Album is pressed.

        mode "create" starts EMPTY: one name, the folder and web address
        suggested (docs/plans/album_create_naming_plan.md), and can only make a
        new album. mode "update" loads the active album and can only write back
        into it — the same split Create Trip got, so creating can never reach
        into the album that is open."""
        super().__init__(parent)
        self.menu_spec = menu_spec
        self._preset = preset or {}
        self.logger = parent.logger
        self.passed_parent = parent
        self.mode = mode if mode in ("create", "update") else "create"
        self.setWindowTitle("Create Album" if self.mode == "create"
                            else "Select or Update Album")
        self.resize(800, 600)

        trip_root = ("" if self.mode == "create"
                     else _config.get("virttrip", "virttripbiotadatabase", fallback=""))
        trip_data = read_virttrip_ini(trip_root) if trip_root and os.path.isdir(trip_root) else {}

        main_layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ── Tab 1: Select Trip Folder ──────────────────────────────────────────
        tab1 = QWidget()
        layout1 = QVBoxLayout(tab1)
        about = ("<p><b>An album</b> is a collection of pictures the master already "
                 "holds, chosen by date range — an old holiday, or a trip that is done.</p>")
        if self.mode == "create":
            about += ("<p>Type a name and the dates. SpeciesViewer suggests the folder "
                      "and the web address; <b>Check Range</b> shows what the master "
                      "holds for those dates. Or start from <b>Pick from the "
                      "master…</b>. The picture, the banner and the texts can follow "
                      "later.</p>")
        layout1.addWidget(HTMLLabel(about))
        self._layout1 = layout1

        self.root_field = RecentDirectoryFieldQt("Album Root folder", trip_root)
        self.root_field.textChanged(self.on_trip_root_changed)
        layout1.addWidget(self.root_field)

        # (The "New album folder / Create Folder" row is gone: Create mode's name
        # panel suggests the folder and Create Album makes it.)

        self.trip_web = LabeledTextFieldQt(
            "Album Web root",
            "" if self.mode == "create" else
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
        if self.mode == "create":
            # The name panel (added once the date field exists) asks for these;
            # the fields stay as the values the save reads.
            self.root_field.setVisible(False)
            trip_web_row.setVisible(False)
        else:
            layout1.addStretch()

        tabs.addTab(tab1, "Name && Dates" if self.mode == "create" else "Album Folder")

        # ── Tab 2: Images & Dates ──────────────────────────────────────────────
        tab2 = QWidget()
        layout2 = QVBoxLayout(tab2)
        self.trip_img_field = ImageFileFieldQt("Album picture", (600, 450), self.root_field)
        self.trip_img_field.field.setText(trip_data.get("trip_picture", ""))
        layout2.addWidget(image_field_row(HTMLLabel(
            "<p><b>Album picture</b><br>"
            "This picture represents the album in the trip listings on the website.<br>"
            "Required dimensions: <b>600×450 pixels</b>.</p>"
        ), self.trip_img_field))
        layout2.addWidget(self.trip_img_field)

        self.topbar_img_field = ImageFileFieldQt("Web banner", (2000, 270), self.root_field)
        self.topbar_img_field.field.setText(trip_data.get("trip_banner", ""))
        layout2.addWidget(image_field_row(HTMLLabel(
            "<p><b>Web banner</b><br>"
            "This banner runs across the top of every blog and species page of this album.<br>"
            "Required dimensions: <b>2000×270 pixels</b>.</p>"
        ), self.topbar_img_field))
        layout2.addWidget(self.topbar_img_field)

        self.date_field = DateRangeFieldQt()
        self.date_field.set_dates(trip_data.get("fromdate", ""), trip_data.get("todate", ""))
        from gui.range_check_view import check_range_row
        range_row = check_range_row(
            self, self.date_field, "album",
            own_name=lambda: Path(self.trip_web.get_text().strip()).name)
        if self.mode == "create":
            # The range IS an album's content: dates and Check Range sit
            # beside the name.
            from gui.trip_name_panel import TripNamePanel
            self.name_panel = TripNamePanel(self.date_field, kind="album")
            self._layout1.insertWidget(1, self.name_panel)
            self._layout1.insertLayout(2, range_row)
            self._layout1.addStretch()
        else:
            layout2.addWidget(HTMLLabel(
                "<p><b>Date range</b><br>"
                "Defines the date range from which pictures will be drawn from the master database.</p>"
            ))
            layout2.addWidget(self.date_field)
            layout2.addLayout(range_row)

        tabs.addTab(tab2, "Images" if self.mode == "create" else "Images && Dates")

        # ── Tab 3: Trip Text ───────────────────────────────────────────────────
        tab3 = QWidget()
        layout3 = QVBoxLayout(tab3)
        sub_tabs = QTabWidget()

        dutch_tab = QWidget()
        dutch_layout = QVBoxLayout(dutch_tab)
        self.title_nl_field = LabeledTextFieldQt("Album title (Dutch)", trip_data.get("title_nl", ""))
        self.message_nl_field = LabeledTextFieldQt(
            "Album description (Dutch)", trip_data.get("message_nl", ""), multiline=True)
        from gui.link_insert import add_link_editing
        add_link_editing(self.message_nl_field.field)   # Ctrl+K / right-click → <a href>
        translate_nl_btn = QPushButton("Translate to English")
        translate_nl_btn.clicked.connect(self.translate_dutch_to_english)
        dutch_layout.addWidget(translate_nl_btn)
        dutch_layout.addWidget(HTMLLabel(
            "<p><b>Album title and description (Dutch)</b><br>"
            "Use the <i>Translate</i> button to generate an English version automatically.</p>"
        ))
        dutch_layout.addWidget(self.title_nl_field)
        dutch_layout.addWidget(self.message_nl_field)
        sub_tabs.addTab(dutch_tab, "Dutch Text")

        english_tab = QWidget()
        english_layout = QVBoxLayout(english_tab)
        self.title_en_field = LabeledTextFieldQt("Album title (English)", trip_data.get("title_en", ""))
        self.message_en_field = LabeledTextFieldQt(
            "Album description (English)", trip_data.get("message_en", ""), multiline=True)
        from gui.link_insert import add_link_editing
        add_link_editing(self.message_en_field.field)   # Ctrl+K / right-click → <a href>
        translate_en_btn = QPushButton("Translate to Dutch")
        translate_en_btn.clicked.connect(self.translate_english_to_dutch)
        english_layout.addWidget(translate_en_btn)
        english_layout.addWidget(HTMLLabel(
            "<p><b>Album title and description (English)</b><br>"
            "Use the <i>Translate</i> button to generate a Dutch version automatically.</p>"
        ))
        english_layout.addWidget(self.title_en_field)
        english_layout.addWidget(self.message_en_field)
        sub_tabs.addTab(english_tab, "English Text")

        layout3.addWidget(sub_tabs)
        tabs.addTab(tab3, "Album Text")

        main_layout.addWidget(tabs)
        self.tabs = tabs

        self.status_label = QLabel("")
        main_layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        # Bottom-LEFT, with the dialog's other actions rather than between two
        # input fields: this button does far more than fill the field beside it
        # (it creates the folder and rewrites every tab), and a field-level
        # position promised something much smaller than it delivers.
        pick_btn = QPushButton("Pick from the master…")
        pick_btn.setToolTip("Find the trips already in the master, then create and "
                            "fill in this album from the one you pick")
        pick_btn.clicked.connect(self.on_pick_from_master)
        btn_layout.addWidget(pick_btn)
        self.pick_hint = QLabel("")
        self.pick_hint.setStyleSheet("color: #555;")
        btn_layout.addWidget(self.pick_hint)
        btn_layout.addStretch()
        write_ini_btn = QPushButton("Update Album Settings")
        write_ini_btn.setToolTip(
            "Save this album's settings AND text (both this page and Trip "
            "Text) to virttrip.ini — WITHOUT rebuilding. Use this to update an "
            "existing album (e.g. its Preferred names). 'Create Virtual "
            "Trip' is only for building a brand-new one.")
        create_btn    = QPushButton("Create Album")
        select_btn    = QPushButton("Select")
        # Create (build a brand-new virtual trip) first; Update Trip Settings (the
        # everyday save for an existing one) after it.
        btn_layout.addWidget(create_btn)
        btn_layout.addWidget(write_ini_btn)
        btn_layout.addWidget(select_btn)
        main_layout.addLayout(btn_layout)
        # One job per screen, as for trips.
        if self.mode == "create":
            write_ini_btn.setVisible(False)
            select_btn.setVisible(False)
        else:
            create_btn.setVisible(False)
            pick_btn.setVisible(False)       # picking makes a NEW album

        write_ini_btn.clicked.connect(self.on_write_ini_clicked)
        create_btn.clicked.connect(self.on_create_clicked)
        select_btn.clicked.connect(self.on_select)

        self._apply_preset()

    def closeEvent(self, event):
        from gui.translation_validate import prompt_translation_locked
        self.passed_parent.translator.writeTranslationCache(
            on_locked=lambda e: prompt_translation_locked(self, e))
        super().closeEvent(event)

    def on_pick_from_master(self):
        """Open the candidate picker and fill this dialog from the chosen trip:
        name, dates and a factual default text. Nothing is created until
        Create Album — the name panel shows what will be made, and whether the
        name is free."""
        from gui.trip_finder_view import pick

        row = pick(self, _config)
        if row is None:
            return                                   # cancelled — change nothing
        title = self._default_title(row)
        self.date_field.set_dates(row["start"], row["end"])
        self.name_panel.name.setText(title)
        # Default text — wanted, so the album is publishable while it is being
        # worked on. OVERWRITTEN, not filled-if-empty: picking a trip means
        # "this album is now Panama". The description stays factual; the real
        # words come after working with the images.
        self.title_nl_field.set_text(title)
        self.message_nl_field.set_text(self._default_message(row, title))
        try:
            self.translate_dutch_to_english()
        except Exception as e:                       # network/translator hiccup
            self.logger.warning(f"Create Album: could not translate the default "
                                f"text to English ({e}) — fill the English tab in "
                                f"by hand.")
            self.title_en_field.set_text(title)
            self.message_en_field.set_text("")
        where = row.get("place", "")
        self.pick_hint.setText(
            f"{row['pictures']} pictures over {row['days']} days"
            + (f" — {where}" if where and not where.startswith("(no place") else ""))
        # self.logger — this module has NO module-level logger.
        self.logger.info(f"Create Album: picked {row['start']} → {row['end']} "
                         f"({row['pictures']} pictures) as '{title}'.")

    def _default_title(self, row: dict) -> str:
        """A title worth publishing during development, from what we know."""
        from engine.trip_finder import upn_prefix   # noqa: F401  (documents the source)
        place = (row.get("city") or "").strip()
        if not place:
            p = row.get("place", "")
            place = "" if p.startswith("(no place") else p.split(",")[0].strip()
        year = (row.get("start") or "")[:4]
        if not place:
            # No geocoding at all (the pre-2014 pictures) — fall back to the name
            # the user filed them under, which is the whole point of the prefix.
            # It arrives as a filing key ("2013 panama"), so make it read as a
            # title: drop the leading year, capitalise, put the year at the end.
            raw = (row.get("prefix") or "").strip()
            raw = re.sub(r"^\s*(19|20)\d{2}\s+", "", raw)
            place = raw.title() if raw else "Reis"
        return f"{place} {year}".strip()

    def _default_message(self, row: dict, title: str) -> str:
        """A factual Dutch stub — publishable during development, obviously a
        placeholder, and containing nothing that was invented."""
        place = title.rsplit(" ", 1)[0] if " " in title else title
        year = (row.get("start") or "")[:4]
        return f"Foto's van {place}, {year}." if year else f"Foto's van {place}."

    def _apply_preset(self):
        """Fill the dialog from a picked report row, then say so on screen.

        The dates come from the pictures that actually exist, which is the whole
        point — a typed range gets padded "to be safe" and then the album spans
        days that hold nothing. Everything stays editable; the folder still has
        to be created and Create still has to be pressed, so this only removes
        the remembering, not the deciding.
        """
        p = self._preset
        if not p or self.mode != "create":
            return
        if p.get("fromdate") and p.get("todate"):
            self.date_field.set_dates(p["fromdate"], p["todate"])
        name = (p.get("title_nl") or p.get("folder") or "").strip()
        if name:
            self.name_panel.name.setText(name)
        if p.get("title_nl") and not self.title_nl_field.get_text().strip():
            self.title_nl_field.set_text(p["title_nl"])
        if p.get("note"):
            self.status_label.setText(p["note"])

    def _make_album_folder(self) -> bool:
        """Create mode: check the name panel, make the album's folder, and fill
        the (hidden) fields the save reads. False = nothing made; the status
        line says why."""
        problems = self.name_panel.problems()
        # An album IS its pictures: with none in the range there is nothing to
        # build, and the pipeline used to stop half-way with a traceback
        # (2026-10-12). Say it here, before anything is made.
        start, end = self.date_field.get_dates()
        count = _master_pictures_in_range(start, end)
        if count == 0:
            problems.append(f"the master holds no pictures between {start} and {end} "
                            "— check the dates (Check Range shows what is there)")
        if problems:
            self.tabs.setCurrentIndex(0)
            self.status_label.setText("❌ Not created: " + "; ".join(problems) + ".")
            return False
        folder = self.name_panel.data_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.status_label.setText(f"❌ Could not create the folder: {e}")
            return False
        self.logger.info(f"New album folder: {folder}")
        self.root_field.set_text(str(folder))    # first: the picture fields check against it
        # The album's own copy of the default picture and banner — the files to
        # replace with real ones later.
        dist_dir = Path(__file__).resolve().parent.parent / "dist" / "includes web"
        for name, field in (("tripimage.jpg", self.trip_img_field),
                            ("tripbanner.jpg", self.topbar_img_field)):
            if field.get_filename().strip():
                continue
            try:
                if not (folder / name).exists():
                    shutil.copy(dist_dir / name, folder / name)
                field.field.setText(name)
                field.validate()
            except OSError as e:
                self.logger.warning(f"Default picture not copied ({name}): {e}")
        # Albums and trips share /trips/: to a visitor an album is a trip.
        self.trip_web.set_text("/trips/" + self.name_panel.slug())
        title = self.name_panel.trip_name()
        title = title[:1].upper() + title[1:]
        for field in (self.title_nl_field, self.title_en_field):
            if not field.get_text().strip():
                field.set_text(title)
        return True

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
        self.passed_parent.status_bar.showMessage("Album settings updated.")

    def on_create_clicked(self):
        """Create the album AND take it all the way to a published website in one
        flow (arc step 4): make repository → extract days → write+upload blogs →
        build+upload website → make the blogs clickable. It stops short of the
        trip index on purpose — publishing is fine before advertising, and
        un/advertising is a separate, deliberate action. Offline, the build steps
        fall back to local-only (they check connectivity themselves)."""
        import argparse as _argparse
        import configparser as _cp
        from utils.batch_config import generate_svconfig_ini

        if self.mode == "create" and not self._make_album_folder():
            return
        self._write_virttrip_ini()
        virttrip_root = self.root_field.get_text()
        web_root = self.trip_web.field.text()
        from utils.config import root_is_unset
        if root_is_unset("virttrip", "virttriprootdirectory"):
            set_setting("virttrip", "virttriprootdirectory", str(Path(virttrip_root).parent))
        set_setting("virttrip", "virttripbiotadatabase", virttrip_root)
        set_setting("virttrip", "virttripbiotawebroot",
                    web_root if web_root.endswith("/") else web_root + "/")

        # Point svconfig at THIS album (virttrip_mode), then build the module
        # config + args the svv* steps expect — the same setup the retire flow does.
        try:
            generate_svconfig_ini(virttrip_mode=True)
            mcfg = _cp.ConfigParser()
            read_ini(mcfg, svconfig_path())
        except Exception as e:
            self.passed_parent.logger.warning(f"Create Album setup failed: {e}")
            QMessageBox.critical(self, "Create Album", f"Setup failed: {e}")
            return
        args = _argparse.Namespace(config=str(svconfig_path()), dir="")
        name = Path(virttrip_root).name

        steps = [
            ("Creating the album",                 lambda: batch.svv1MakeVirtualRepository.main(args, mcfg)),
            ("Writing and uploading the blogs",     lambda: batch.svv3WriteBlog.main(args, mcfg)),
            ("Building and uploading the website",  lambda: batch.svv4BuildWebsite.main(args, mcfg)),
            ("Making the blogs clickable",          lambda: batch.svv6RedoBlog.main(args, mcfg)),
        ]
        self.passed_parent.logger.info(f"Creating and publishing album '{name}'…")
        # Close this dialog first; the pipeline runs on the main window behind its
        # own spinner and refreshes the album panel when done.
        self.accept()
        _run_virttrip_pipeline(
            self.passed_parent, steps,
            title="Create Album",
            success_msg=f"Album “{name}” is created and published.\n\nThe pictures "
                        f"were extracted and the blogs + website built and uploaded. "
                        f"It is not yet advertised on the trip index — do that from "
                        f"the Albums menu when you are ready.")

    def on_select(self):
        # An album belongs to one master: never open another master's in place.
        from engine.trip_owner import foreign_owner, refusal_text
        album_dir = self.root_field.get_text()
        owner = foreign_owner(album_dir, "album",
                              _config.get("master", "masterbiotadatabase", fallback=""))
        if owner:
            QMessageBox.warning(self, "Select Album", refusal_text(album_dir, "album", owner))
            return
        from utils.config import root_is_unset
        if root_is_unset("virttrip", "virttriprootdirectory"):
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


class ExportVirtualTripDialog(QDialog):
    """Zip the active virtual trip's authored content (recipe, blog drafts,
    extracted day CSVs) for transfer to another machine.

    No picture files are included — a virtual trip references the master's
    biota/ tree by path rather than duplicating it, so the ZIP stays small
    and the transfer is fast. Use Import Virtual Trip on the target machine
    to unpack it and re-extract day data against its own master."""

    def __init__(self, parent, menu_spec):
        super().__init__(parent)
        self.setWindowTitle("Export Album")
        self.resize(650, 320)
        self.menu_spec = menu_spec
        self.logger = parent.logger
        self.passed_parent = parent

        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data = read_virttrip_ini(virttrip_root) if virttrip_root else {}
        trip_name = (trip_data or {}).get("trip_name", Path(virttrip_root).name if virttrip_root else "?")

        layout = QVBoxLayout(self)

        info = QLabel(
            "<b>Export Album</b><br>"
            "Writes <tt>&lt;trip folder&gt;.zip</tt> into the selected directory. "
            "The ZIP contains <tt>virttrip.ini</tt> and everything under <tt>metadata/</tt> "
            "(blog drafts, extracted day CSVs, seeded species lists). "
            "No picture files are included and web output is excluded — "
            "an album references the master's biota tree by path, "
            "it never duplicates it. "
            "Use <i>Import Album</i> on the target machine to unpack it."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addWidget(QLabel(f"Album:  {virttrip_root or '(not set)'}"))
        layout.addWidget(QLabel(f"Trip name:     {trip_name}"))

        self.target_field = DirectoryFieldQt("Output directory (ZIP saved here)", "")
        layout.addWidget(self.target_field)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.export_btn = QPushButton("Export ZIP")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.export_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.export_btn.clicked.connect(self.on_export)
        self.close_btn.clicked.connect(self.close)

    def on_export(self):
        target = self.target_field.get_path()
        if not target or not os.path.isdir(target):
            self.status_label.setText("Select a valid output directory first.")
            return
        if not _config.has_section("utilities"):
            _config.add_section("utilities")
        _config["utilities"]["virttrip_export_target"] = target
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec)


class ImportVirtualTripDialog(QDialog):
    """Unpack a virtual trip ZIP (from Export Virtual Trip) into this
    machine's Virtual Trips root and register it in [virttrip]. No
    extraction runs here — an album reads the master live by date range, so
    its pictures resolve against whatever master THIS machine has as soon as
    it's opened. On a SmallMaster (partial, JPG-only) machine that means
    fewer pictures than the original — see the on-screen notice below."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Import Album")
        self.resize(680, 420)
        self.logger = parent.logger
        self.passed_parent = parent

        layout = QVBoxLayout(self)

        self._locked = _config.get(
            "master", "readonly_locked", fallback="no").strip().lower() == "yes"

        info = QLabel(
            "<b>Import a Album from a ZIP file</b><br>"
            "Extracts <tt>virttrip.ini</tt> and <tt>metadata/</tt> (blog drafts, "
            "day CSVs, seeded species lists) into a new folder under the Virtual "
            "Trips root, and registers it as the active album. "
            "Existing files are overwritten; nothing else is removed."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        if self._locked:
            limitation = QLabel(
                "⚠ This machine holds a partial copy of the master (SmallMaster: "
                "JPG-bearing pictures only, no RAWs). The imported album will show "
                "only the pictures present in that partial copy — it may have fewer "
                "pictures than on the machine that authored it. You can still view "
                "and publish it here."
            )
            limitation.setWordWrap(True)
            limitation.setStyleSheet("color: #8a6d00;")
            layout.addWidget(limitation)

        # ZIP file picker
        zip_row = QHBoxLayout()
        zip_row.addWidget(QLabel("ZIP file:"))
        self.zip_field = QLineEdit()
        self.zip_field.setPlaceholderText("Select an album ZIP…")
        zip_row.addWidget(self.zip_field)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_zip)
        zip_row.addWidget(browse_btn)
        layout.addLayout(zip_row)

        # Destination: <virttriprootdirectory>/<folder name from the ZIP>
        vt_root = _config.get("virttrip", "virttriprootdirectory", fallback="")
        layout.addWidget(QLabel("Albums root:"))
        self.root_edit = QLineEdit(vt_root or "(not configured — set it in Settings → Config)")
        self.root_edit.setReadOnly(bool(vt_root))
        layout.addWidget(self.root_edit)

        self.dest_label = QLabel("Select a ZIP file to see the destination folder.")
        self.dest_label.setWordWrap(True)
        self.dest_label.setStyleSheet("color: #555; font-style: italic;")
        layout.addWidget(self.dest_label)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.import_btn = QPushButton("Import")
        self.close_btn = QPushButton("Close")
        btn_layout.addWidget(self.import_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.import_btn.clicked.connect(self._on_import)
        self.close_btn.clicked.connect(self.close)
        self.zip_field.textChanged.connect(self._update_destination)

    def _browse_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Album ZIP", "", "ZIP files (*.zip);;All files (*)")
        if path:
            self.zip_field.setText(path)

    def _dest_path(self):
        vt_root = _config.get("virttrip", "virttriprootdirectory", fallback="")
        zip_path = self.zip_field.text().strip()
        if not vt_root or not zip_path:
            return None
        return str(Path(vt_root) / Path(zip_path).stem)

    def _update_destination(self):
        dest = self._dest_path()
        if not dest:
            self.dest_label.setText("Select a ZIP file to see the destination folder.")
            return
        exists = " (already exists — files will be overwritten)" if os.path.isdir(dest) else ""
        self.dest_label.setText(f"Will extract to: {dest}{exists}")

    def _on_import(self):
        zip_path = self.zip_field.text().strip()
        if not zip_path or not os.path.isfile(zip_path):
            self.status_label.setText("Select a valid ZIP file first.")
            return
        dest = self._dest_path()
        if not dest:
            self.status_label.setText(
                "Albums root is not configured. Set it in Settings → Config first.")
            return

        import zipfile as _zf
        import time as _time
        os.makedirs(dest, exist_ok=True)

        t0 = _time.time()
        try:
            with _zf.ZipFile(zip_path, "r") as zf:
                members = zf.namelist()
                total = len(members)
                last_ui = t0
                for i, member in enumerate(members):
                    zf.extract(member, dest)
                    now = _time.time()
                    if now - last_ui >= 0.5:
                        pct = 100 * i // total
                        self.status_label.setText(f"Extracting… {i:,} / {total:,} files ({pct}%)")
                        QApplication.processEvents()
                        last_ui = now
        except Exception as e:
            self.status_label.setText(f"Extract failed: {e}")
            return

        if not os.path.isfile(os.path.join(dest, "virttrip.ini")):
            self.status_label.setText(
                f"Extracted, but no virttrip.ini found under {dest} — is this a valid export?")
            return

        trip_data = read_virttrip_ini(dest) or {}
        trip_name = trip_data.get("trip_name", Path(dest).name)
        set_setting("virttrip", "virttripbiotadatabase", dest)
        web_slug = trip_name if trip_name.startswith("/trips/") else f"/trips/{trip_name}"
        set_setting("virttrip", "virttripbiotawebroot", web_slug)

        elapsed = _time.time() - t0
        self.logger.info(f"Album imported to {dest} in {elapsed:.1f}s.")

        populate_virttrip_overview(self.passed_parent)
        self.passed_parent.virttrip_panel.rebuild()

        # No day extract any more — the album reads THIS machine's master live by its
        # date range, so its pictures resolve against the local master immediately.
        note = (" This machine's SmallMaster copy is partial, so some pictures may "
                 "be missing." if self._locked else "")
        self.status_label.setText(
            f"✅ Imported in {elapsed:.1f}s. The album reads this machine's master "
            f"by date range — open it to view its pictures.{note}")


class VirttripWriteBlogDialog(QDialog):
    """Run svv3WriteBlog: auto-create blog ini files + generate + upload all days."""

    def __init__(self, parent):
        super().__init__(parent)
        self.logger       = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Write Album Blogs")
        self.resize(580, 300)

        layout = QVBoxLayout(self)

        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data  = read_virttrip_ini(virttrip_root) if virttrip_root else {}
        trip_name  = (trip_data or {}).get("trip_name", Path(virttrip_root).name if virttrip_root else "?")
        day_count  = 0
        blog_count = 0
        if virttrip_root:
            review_root = Path(virttrip_root) / "metadata" / "review"
            blog_root   = Path(virttrip_root) / "metadata" / "blog"
            if review_root.is_dir():
                days = [d for d in review_root.iterdir()
                        if d.is_dir() and (d / "data_files.csv").exists()]
                day_count  = len(days)
                blog_count = sum(1 for d in days
                                 if (blog_root / f"blog_{d.name}.ini").exists())

        common_score = _common_blog_min_score(virttrip_root) if virttrip_root else None

        # Offer to write the missing day blogs only for a trip that has none at
        # all — a retroactive trip on its first run. Once a trip HAS blogs, a day
        # without one is a deliberate gap (a day never blogged, or a migration
        # that skipped it), and filling it would invent a day that never existed.
        # source_trip_id alone can't decide this: every virttrip made before that
        # key existed has an empty one, gaps included.
        source_trip  = (trip_data or {}).get("source_trip_id", "").strip()
        self._fresh  = blog_count == 0 and not source_trip

        layout.addWidget(HTMLLabel(
            "<p><b>Write Album Blogs</b></p>"
            "<p>For every day that has extracted data, reads that day's "
            "<code>metadata/blog/blog_YYYY-MM-DD.ini</code> and uploads NL + EN blog "
            "HTML pages.</p>"
            "<p>Re-running is safe — existing ini files are preserved; "
            "HTML is always regenerated and re-uploaded.</p>"
        ))

        layout.addWidget(QLabel(f"Album:   {virttrip_root or '(not set)'}"))
        layout.addWidget(QLabel(f"Trip name:      {trip_name}"))
        layout.addWidget(QLabel(f"Days with data: {day_count}"))
        layout.addWidget(QLabel(
            f"Days with a blog: {blog_count}"
            + ("   — none yet, so the drafts below are this trip's first blogs"
               if self._fresh else
               f" of {day_count}   — the rest are days with no blog")))

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

        self.auto_create_checkbox = QCheckBox(
            "Write a first draft for days that have no blog yet")
        self.auto_create_checkbox.setChecked(self._fresh)
        self.auto_create_checkbox.setToolTip(
            "Writes a day title and a short text from the day's locations and best "
            "species, marked as a draft (amber) until you accept it in the blog editor.\n\n"
            "On by default for a trip with no blogs at all — a retroactive trip, which has "
            "no original trip to take blogs from, so this is the only way it gets any.\n\n"
            "Off by default once the trip HAS blogs: a day without one is then a day you "
            "never blogged, and writing text there would invent a day that never happened.")
        layout.addWidget(self.auto_create_checkbox)

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
        from utils import site_config
        base_url = site_config.site_base() + "/"
        try:
            from utils.runtime_files import LAST_BLOG_URL, runtime_path
            with open(runtime_path(LAST_BLOG_URL), "r") as f:
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
            "Writing album blogs...",
            "✅ All blogs written and uploaded.",
        )
        menu_spec.batch_parms["extra_config"] = [
            ("blogconfig", "minimum_score", self.min_score_spin.value()),
            ("blogconfig", "auto_create_blogs", self.auto_create_checkbox.isChecked()),
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
        self.setWindowTitle("Make the Blogs Clickable")
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
            "<p><b>Make the Blogs Clickable</b> — add species links &amp; navigation</p>"
            "<p>Re-renders the blog pages that <i>Write Album Blogs</i> already "
            "published, rewriting only the HTML:</p>"
            "<ul>"
            "<li>Adds the full <b>Previous / Next</b> navigation between days "
            "(the initial write can only link backwards).</li>"
            "<li>Refreshes the species-reference hyperlinks on every card.</li>"
            "</ul>"
            "<p><b>No pictures are moved or re-uploaded</b> — the day thumbnails already "
            "on the web are left untouched. Run this after all days have been written.</p>"
        ))

        layout.addWidget(QLabel(f"Album:  {virttrip_root or '(not set)'}"))
        layout.addWidget(QLabel(f"Trip name:     {trip_name}"))
        layout.addWidget(QLabel(f"Day blogs:     {blog_count}"))

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.open_after_checkbox = QCheckBox("Open trip blog in browser after recreating")
        self.open_after_checkbox.setChecked(True)
        layout.addWidget(self.open_after_checkbox)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        run_btn   = QPushButton("Make Clickable")
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
        from utils import site_config
        base_url = site_config.site_base() + "/"
        try:
            from utils.runtime_files import LAST_BLOG_URL, runtime_path
            with open(runtime_path(LAST_BLOG_URL), "r") as f:
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
        self.setWindowTitle("Build Album Website")
        self.resize(560, 260)

        layout = QVBoxLayout(self)

        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data = read_virttrip_ini(virttrip_root) if virttrip_root else {}
        trip_name = (trip_data or {}).get("trip_name", Path(virttrip_root).name if virttrip_root else "?")

        layout.addWidget(HTMLLabel(
            "<p><b>Build Album Website</b></p>"
            "<p>Generates species HTML pages (NL + EN) for the album, reading its "
            "pictures live from the master by date range, then uploads them via SFTP "
            "— identical to the regular trip's Build Website step.</p>"
        ))

        layout.addWidget(QLabel(f"Album:  {virttrip_root or '(not set)'}"))
        layout.addWidget(QLabel(f"Trip name:     {trip_name}"))

        self.open_after_checkbox = QCheckBox("Open the album's species overview in the browser after building")
        self.open_after_checkbox.setChecked(True)
        layout.addWidget(self.open_after_checkbox)

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
            "Building album species pages...",
            "✅ Album website built.",
        )
        run_btn.clicked.connect(self.on_run)
        close_btn.clicked.connect(self.close)

    def get_website_url(self) -> str:
        from utils import site_config
        base_url = site_config.site_base() + "/"
        try:
            from utils.runtime_files import LAST_WEBSITE_URL, runtime_path
            with open(runtime_path(LAST_WEBSITE_URL), "r") as f:
                relative_url = f.read().strip()
            return base_url.rstrip("/") + relative_url
        except FileNotFoundError:
            return base_url

    def on_run(self):
        def on_success():
            populate_virttrip_overview(self.passed_parent)
            if self.open_after_checkbox.isChecked():
                webbrowser.open(self.get_website_url())

        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(self.menu_spec, on_success=on_success)


class VirttripAddToMasterDialog(QDialog):
    """Run svv5AddToMaster: register the virtual trip in the master trip index."""

    def __init__(self, parent):
        super().__init__(parent)
        self.logger        = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Add Album to Master")
        self.resize(580, 280)

        layout = QVBoxLayout(self)

        virttrip_root = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        trip_data = read_virttrip_ini(virttrip_root) if virttrip_root else {}
        trip_name = (trip_data or {}).get("trip_name", Path(virttrip_root).name if virttrip_root else "?")
        master_db = _config.get("master", "masterbiotadatabase", fallback="")

        layout.addWidget(HTMLLabel(
            "<p><b>Add Album to Master</b></p>"
            "<p>Registers this album in the master trip index so it appears "
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

        layout.addWidget(QLabel(f"Album:  {virttrip_root or '(not set)'}"))
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
            "Registering album in master trip index...",
            "✅ Album added to master trip index and uploaded.",
        )
        if self.local_checkbox.isChecked():
            menu_spec.batch_parms["extra_config"] = [
                ("virttrip_addtomaster", "read_from_local", "true"),
            ]
        runner = BatchModuleRunner(self, self.passed_parent)
        runner.run(menu_spec)
