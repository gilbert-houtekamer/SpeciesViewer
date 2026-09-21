import configparser
import logging

logger = logging.getLogger("speciesviewer")

from PyQt6.QtWidgets import QMessageBox
from gui.menu import MenuSpec  # assuming class is defined elsewhere
from gui.trip_process import (ReadSDCardDialog, ReadDirDialog, ReadSummarizeDialog, TripCreationDialog,
        PublishDayDialog,
        WritePicturesDialog, CreateBlogDialog, FreezeDialog, WriteCommitDialog,
        CountSpeciesDialog, RedoTripBlogsDialog, RedoTripRepoDialog, MergeTripDialog,
        MasterPipelineDialog, MasterRebuildWebsiteDialog, ConvertAvibaseDialog,
        SyncSidecarsDialog, EmptyTripDataDialog, SpeciesCollectionDialog,
        TestbedDialog, BlogAdminDialog, PhotoMechanicDialog,
        RescanMasterCacheDialog, SmallMasterDialog, InstallSmallMasterDialog,
        ExportTripDialog, ImportTripDialog)
from gui.virttrip_process import (VirtualTripCreationDialog,
                                   VirttripWriteBlogDialog, VirttripRedoBlogDialog,
                                   VirttripBuildWebsiteDialog,
                                   VirttripAddToMasterDialog, retire_trip_to_virttrip,
                                   ExportVirtualTripDialog, ImportVirtualTripDialog,
                                   require_full_master)
from utils.config import _config, debug_enabled
from utils.trip_config import read_trip_ini
import gui.days_process as days_process
import gui.unfiled_view as unfiled_view
import os
from settings.globalsettings import GlobalSettingsDialog
from settings.lookfeel import LookFeelDialog
from settings.reposettings import RepoSettingsDialog
from settings.speciessettings import SpeciesSettingsDialog
from settings.dbsettings import DatabaseSettingsDialog
from settings.rawsettings import RawProcessingDialog
from settings.locationsettings import LocationSettingsDialog
from settings.sidecarsettings import SidecarSettingsDialog
from settings.photolabdenoise import PhotoLabDenoiseDialog
from settings.photolablocation import PhotoLabLocationDialog
from dialogs.setting import AboutDialog
from utils.batch_config import generate_svconfig_ini
from engine.statemachine import StateMachine

import batch.sv1MakeReposity
import batch.sv2ReadDirExif
import batch.sv3WritePictures
import batch.sv4WriteBlog
# Build Website (sv5) is no longer a separate menu STEP, but its work (species
# pages + the all-species index → frozen/allspecies.csv, which sv7 reads) is
# folded into Publish Day. So the module stays wired in.
import batch.sv5BuildWebsite
import batch.sv6Freeze
import batch.sv7WriteCommit
import batch.svMasterRebuildWeb
import batch.svr2CountSpecies
import batch.svr4UpdateTrips
import batch.svr5MergeSpeciesCollections
import batch.svr6ExportTrip
import batch.svr7ArchiveVerdict
import batch.svr8RecreateTripRepository
import batch.svr9RedoBlog
import batch.svu2avibase
import batch.svu5SyncSidecars
import batch.svu8PhotoMechanic
import batch.svu6EmptyTripData
import batch.svu9SmallMaster
import batch.svu7Testbed
import batch.svv1MakeVirtualRepository
import batch.svv3WriteBlog
import batch.svv4BuildWebsite
import batch.svv7ExportVirtualTrip
import engine.exifcache as exifcache
from utils.ini_io import read_ini


def build_menu_spec(main_window):
    # Load config and statemachine
    generate_svconfig_ini()
    module_config = configparser.ConfigParser()
    read_ini(module_config, "svconfig.ini")

    sm = StateMachine(
        module_config.get("repository", "BiotaDatabase"),
        module_config.get("project", "PictureDirectory"),
        readState=True
    )

    def make_enabled_checker(statusname):
        # A pipeline step needs an ACTIVE trip as well as its statemachine's OK:
        # with no trip selected the (empty) statemachine reports "OK to run the
        # first step", which would leave the whole trip pipeline clickable in the
        # no-trip state. has_active_trip is defined below and resolved at call time.
        return lambda: has_active_trip() and sm.stateOKtorun(statusname)

    def make_tooltip(statusname):
        try:
            return sm.explainAbort(statusname, raiseSV=False)
        except:
            return None

    # ── Menu-gating predicates (Phase 1). They read the LIVE config / window
    # state and are re-evaluated every time a menu opens (see gui/menu.py
    # aboutToShow), so an item greys as soon as the relevant selection changes.
    # "configured" mirrors the existing self-guards: a non-empty, quote-stripped
    # path. Items with no precondition (Settings, Create Trip, Read SD-cards,
    # Testbed, Sync Sidecars, …) are deliberately left ungated.
    def _cfg(section, key):
        return _config.get(section, key, fallback="").replace('"', "").replace("'", "").strip()

    def has_master():
        return bool(_cfg("master", "masterbiotadatabase"))

    def has_full_master():
        # BigBoy (full master), not a SmallMaster install (readonly_locked=yes) —
        # the non-dialog twin of virttrip_process.require_full_master.
        return has_master() and _config.get(
            "master", "readonly_locked", fallback="no").strip().lower() != "yes"

    def has_active_trip():
        return bool(_cfg("trip", "tripbiotadatabase"))

    def has_active_album():
        return bool(_cfg("virttrip", "virttripbiotadatabase"))

    def has_active_day():
        return bool(getattr(main_window, "active_day_folder", ""))

    def has_trip_and_master():
        return has_active_trip() and has_master()

    # ── Phase 2: lifecycle-milestone gates for the To Master chain. Cheap reads:
    # merge-done comes from the marker MasterPipelineDialog already writes
    # (<master>/admin/master_commits.csv, keyed by the trip's picture root);
    # retire-done from the retired_to_album flag stamped into trip.ini; the
    # "days published" state from a short-memoised scan of the trip's day folders.
    def _active_picture_root():
        pic = _cfg("project", "picturedirectory")
        return os.path.dirname(pic.rstrip("/\\")) if pic else ""

    def _norm(p):
        return os.path.normpath(p).lower() if p else ""

    def has_merged():
        master = _cfg("master", "masterbiotadatabase")
        target = _norm(_active_picture_root())
        if not master or not target:
            return False
        path = os.path.join(master, "admin", "master_commits.csv")
        if not os.path.isfile(path):
            return False
        try:
            import csv as _csv
            with open(path, "r", encoding="utf-8") as f:
                for r in _csv.DictReader(f, delimiter=";"):
                    if _norm((r.get("picture_directory") or "").strip()) == target:
                        return True
        except OSError:
            return False
        return False

    def has_retired():
        trip_db = _cfg("trip", "tripbiotadatabase")
        if not trip_db:
            return False
        try:
            return bool((read_trip_ini(trip_db) or {}).get("retired_to_album", ""))
        except Exception:
            return False

    def _scan_trip_publish(trip_db):
        # (any_day_published, all_jpg_days_published). A day is "publishable" when
        # its picture folder holds >0 JPGs; "published" when its WriteCommit
        # statemachine step is done. all_jpg_days_published is vacuously True with
        # no JPG day, so Merge also requires any_day_published (real work done).
        if not trip_db:
            return False, False
        try:
            import re as _re
            from utils.trip_identity import resolve_trip_picture_root
            image_root = resolve_trip_picture_root(trip_db, kind="trip")
            if not image_root:
                return False, False
            image_root = os.fspath(image_root)
            review_root = os.path.join(trip_db, "metadata", "review")
            names = os.listdir(image_root)
        except Exception:
            return False, False
        # Only real trip days count — folders named YYYY-MM-DD, the same convention
        # the retire flow and date-range derivation use. Auxiliary folders like
        # "iphone" carry JPGs but are never part of the day pipeline, so counting
        # them as unpublished JPG-days would keep Merge greyed forever.
        _date_re = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        any_pub, all_jpg_pub = False, True
        for name in names:
            day_dir = os.path.join(image_root, name)
            if not _date_re.match(name) or not os.path.isdir(day_dir):
                continue
            try:
                has_jpg = any(f.lower().endswith(".jpg") for f in os.listdir(day_dir))
            except OSError:
                has_jpg = False
            try:
                sm_day = StateMachine(trip_db, os.path.join(review_root, name), readState=True)
                published = bool(sm_day.Taskstate.get("WriteCommit", [False])[0])
            except Exception:
                published = False
            if published:
                any_pub = True
            if has_jpg and not published:
                all_jpg_pub = False
        return any_pub, all_jpg_pub

    def _pub_state():
        # Memoise the day scan for a few seconds so opening a menu doesn't re-walk
        # the day folders every time; a Publish Day run rebuilds the whole menu.
        import time
        trip_db = _cfg("trip", "tripbiotadatabase")
        now = time.monotonic()
        c = getattr(main_window, "_publish_state_cache", None)
        if c and c[0] == trip_db and (now - c[1]) < 3.0:
            return c[2], c[3]
        st = _scan_trip_publish(trip_db)
        main_window._publish_state_cache = (trip_db, now, st[0], st[1])
        return st

    def any_day_published():
        return _pub_state()[0]

    def merge_ready():
        if not has_trip_and_master():
            return False
        any_pub, all_jpg_pub = _pub_state()
        return any_pub and all_jpg_pub

    def retire_ready():
        return has_full_master() and has_active_trip() and has_merged()

    def delete_trip_ready():
        return has_merged() and has_retired()

    # ── Read SD-cards: the front-stage ingest (M0). Copies pictures from a
    # folder (e.g. a mounted card) into the project pictures dir. Runs the copy
    # directly on a worker thread — no statemachine, no svconfig plumbing —
    # because it precedes any day/trip structure.
    read_sdcard = MenuSpec(
        callback=None,
        title="Read SD-cards",
        showsubdirectory=False,
    )
    def _open_read_sdcard():
        # Non-modal: the copy/scan run on their own QThread (see
        # ReadSDCardDialog), so there's no reason to freeze the rest of SV
        # while a card reads. _try_start_background_op stops a second one
        # (any of the three read modes) starting at the same time.
        if not main_window._try_start_background_op("Read SD-cards"):
            return
        dlg = ReadSDCardDialog(main_window, read_sdcard)
        main_window._read_sdcard_dlg = dlg  # keep alive while non-modal
        dlg.finished.connect(lambda *_: (main_window._end_background_op(),
                                         setattr(main_window, "_read_sdcard_dlg", None)))
        dlg.show()
    read_sdcard.callback = _open_read_sdcard

    # ── The other ingest lane: a card whose trip is not decided yet. Copies into
    # <CurrentPictures>/<year>/<year> unfiled/YYYY-MM-DD and claims nothing — no
    # trip, no Days entry — so a stack of cards can be emptied without answering
    # "which trip?" once per card. The inbox screen files each day afterwards.
    read_sdcard_unfiled = MenuSpec(
        callback=lambda: unfiled_view.read_card_into_unfiled(main_window),
        title="Read card into unfiled…",
        tooltip="Empty a card now, decide which trip it belongs to later",
        showsubdirectory=False,
    )
    # No longer a window: this switches the mode strip to Unfiled, where the
    # inbox now lives as a panel. Kept in the menu because it is still how you
    # ask for it by name when the strip is not where you are looking.
    unfiled_inbox = MenuSpec(
        callback=lambda: unfiled_view.open_unfiled_inbox(main_window),
        title="Unfiled pictures",
        tooltip="Show the unfiled inbox — days that belong to no trip yet",
        showsubdirectory=False,
    )

    # Two entries, not one screen with three jobs: Create starts empty and can only
    # write into a folder it makes itself; Select or Update loads the trip you pick
    # and can only write back into that one. The single "Create or Select" screen
    # inherited the active trip's settings as defaults, which is how greecedb's
    # identity was overwritten. See docs/plans/trip_dialog_and_ingest_lanes.md.
    trip_create = MenuSpec(
        callback=None,
        title="Create Trip",
        batch_parms={
            'statusname': "MakeRepository",
            'module_called': batch.sv1MakeReposity.main,
            'before_msg': "Creating trip data structures...",
            'after_msg': "✅ Trip creation complete. Please review the log messages."
        }
    )
    trip_create.callback = lambda: TripCreationDialog(
        main_window, trip_create, mode="create").exec()

    trip_select = MenuSpec(
        callback=None,
        title="Select or Update Trip…",
        tooltip="Switch to another trip, or change this one's dates, texts and "
                "pictures folder",
        showsubdirectory=False,
    )
    # Shares trip_create's batch_parms: only Create Trip runs the repository build,
    # and this entry does not show that button.
    trip_select.batch_parms = trip_create.batch_parms
    trip_select.callback = lambda: TripCreationDialog(
        main_window, trip_select, mode="update").exec()

    sync_sidecars = MenuSpec(
        callback=None,
        title="Sync Lightroom & PhotoLab Metadata",
        enabled=has_active_trip,
        batch_parms={
            'statusname': "SyncSidecars",
            'module_called': batch.svu5SyncSidecars.main,
            'before_msg': "Synchronizing DOP and XMP sidecars...",
            'after_msg': "✅ Sidecar sync complete."
        }
    )
    sync_sidecars.callback = lambda: SyncSidecarsDialog(main_window, sync_sidecars).exec()

    read_dir = MenuSpec(
        callback=None,
        title="Read Directory",
        enabled=make_enabled_checker("ReadDirExif"),
        tooltip=make_tooltip("ReadDirExif"),                
        batch_parms={
            'statusname': "ReadDirExif",
            'module_called': batch.sv2ReadDirExif.main,
            'before_msg': "Reading picture directory...",
            'after_msg': "✅ Directory scan complete."
        }
    )
    read_dir.callback = lambda: ReadDirDialog(main_window, read_dir).exec()

    write_summary = MenuSpec(
        callback=None,
        title="Write Summary",
        enabled=make_enabled_checker("WritePictures"),
        tooltip=make_tooltip("WritePictures"),             
        batch_parms={
            'statusname': "WritePictures",
            'module_called': batch.sv3WritePictures.main,
            'before_msg': "Reading meta data for all pictures ...",
            'after_msg': "✅ Writing Summary data."
        }
    )
    write_summary.callback = lambda: WritePicturesDialog(main_window, write_summary).exec()

    # ── Read & Summarize: the default trip ingest (sv2 + sv3 in one step) ──
    # The EXIF cache makes re-reading cheap, so fresh data is the sensible
    # default. _read_summarize is the module the trip-overview column-run and
    # context-menu reuse (same chaining as the master pipeline's _master_ingest);
    # the single-day double-click / menu click opens ReadSummarizeDialog, which
    # runs the two steps via ChainedBatchRunner so both statemachine keys are
    # set in order. statusname here is informational — eligibility uses the
    # STATE_COLUMNS gate (ReadDirExif) and state is set per run_keys.
    def _read_summarize(args, _config):
        batch.sv2ReadDirExif.main(args, _config)
        batch.sv3WritePictures.main(args, _config)

    read_summarize = MenuSpec(
        callback=None,
        title="1. Read & Summarize",
        shortcut="Ctrl+D",
        enabled=make_enabled_checker("ReadDirExif"),
        tooltip=make_tooltip("ReadDirExif"),
        batch_parms={
            'statusname': "WritePictures",
            'module_called': _read_summarize,
            'before_msg': "Reading and summarizing picture directory...",
            'after_msg': "✅ Read & Summarize complete.",
        }
    )
    read_summarize.callback = lambda: ReadSummarizeDialog(main_window, read_summarize).exec()

    write_blog = MenuSpec(
        callback=None,
        title="2. Write Blog",
        enabled=make_enabled_checker("WriteBlog"),
        tooltip=make_tooltip("WriteBlog"),        
        batch_parms={
            'statusname': "WriteBlog",
            'module_called': batch.sv4WriteBlog.main,
            'before_msg': "Create a blog entry ...",
            'after_msg': "✅ Blog successfully created."
        }
    )
    write_blog.callback = lambda: CreateBlogDialog(main_window, write_blog).exec()

    # Build Website (sv5) retired — Freeze (sv6) already builds the website, so a
    # separate step was redundant. Publish Day (Freeze + Commit) is the publish
    # action now. The freeze_data / commit_website specs below stay for the
    # Advanced ▸ Legacy submenu.
    freeze_data = MenuSpec(
        callback=None,
        title="Freeze Data",
        enabled=make_enabled_checker("Freeze"),
        tooltip=make_tooltip("Freeze"),        
        batch_parms={
            'statusname': "Freeze",
            'module_called': batch.sv6Freeze.main,
            'before_msg': "Freeze all collected data ...",
            'after_msg': "✅ Data frozen."
        }
    )
    freeze_data.callback = lambda: FreezeDialog(main_window, freeze_data).exec()    

    commit_website = MenuSpec(
        callback=None,
        title="Write Website Commit",
        enabled=make_enabled_checker("WriteCommit"),
        tooltip=make_tooltip("WriteCommit"),
        batch_parms={
            'statusname': "WriteCommit",
            'module_called': batch.sv7WriteCommit.main,
            'before_msg': "Update the trip website ...",
            'after_msg': "✅ Please review updated Website."
        }
    )
    commit_website.callback = lambda: WriteCommitDialog(main_window, commit_website).exec()

    # ── Publish Day: Freeze (sv6) + Write Website Commit (sv7) in one step ──
    # Mirrors Read & Summarize: the trip-overview column-run / context-menu reuse
    # this chained module; the menu click opens PublishDayDialog, which runs both
    # via ChainedBatchRunner so the two statemachine keys (Freeze, then
    # WriteCommit) are set in order. statusname is informational — eligibility
    # uses the STATE_COLUMNS gate (Freeze) and state is set per run_keys.
    def _publish_day(args, _config):
        # Publish Day = Freeze (sv6) + Write Commit (sv7). Freeze now builds the
        # complete website (incl. the all-species index → frozen/allspecies.csv
        # that sv7 reads), so the retired "Build Website" step is not needed here.
        batch.sv6Freeze.main(args, _config)
        batch.sv7WriteCommit.main(args, _config)

    publish_day = MenuSpec(
        callback=None,
        title="3. Publish Day",
        enabled=make_enabled_checker("Freeze"),
        tooltip=make_tooltip("Freeze"),
        batch_parms={
            'statusname': "WriteCommit",
            'module_called': _publish_day,
            'before_msg': "Publishing the day (freeze + website commit) ...",
            'after_msg': "✅ Day published.",
        }
    )
    publish_day.callback = lambda: PublishDayDialog(main_window, publish_day).exec()

    # ONE species list. It used to be two entries on two files — "Edit Trip
    # Species List" and the master's "Edit Species List" — and which one you
    # happened to open decided who saw the correction. Now the shared master
    # list is always the one edited, and an active trip's working copy is
    # brought along so its pipeline steps see the new names too.
    def _edit_species_list():
        master_db = _config.get("master", "masterbiotadatabase", fallback="")
        trip_db = _config.get("trip", "tripbiotadatabase", fallback="")
        # No master configured (a standalone repo) → the trip's own list IS the
        # shared list, so edit that.
        base = master_db or trip_db
        if not base:
            return
        csv_path = os.path.join(base, "metadata", "active", "speciescollection.csv")
        if not os.path.exists(csv_path):
            QMessageBox.warning(main_window, "Edit Species List",
                                f"Species list not found:\n{csv_path}")
            return
        dlg = SpeciesCollectionDialog(main_window, csv_path)
        dlg.exec()
        if not getattr(dlg, "_saved_changes", False):
            return

        # Mirror the edited rows into the active trip's copy. Appending is
        # enough: a later row wins on read, so this updates the names without
        # rewriting the trip's file or disturbing anything else in it.
        changed = getattr(dlg, "changed_names", set())
        if trip_db and master_db and changed:
            trip_csv = os.path.join(trip_db, "metadata", "active", "speciescollection.csv")
            if os.path.exists(trip_csv):
                try:
                    from engine.speciesclassification import appendRowsToSpeciesCollection
                    rows = [r for r in dlg.all_rows
                            if (r.get("ScientificName") or "").strip() in changed]
                    appendRowsToSpeciesCollection(trip_csv, rows)
                except Exception as e:
                    logger.warning(f"Could not mirror species names into the trip: {e}")

        # A name change → offer to re-summarize all days so it reaches the
        # pipeline (see MainWindow.rerun_summary_all_days).
        if trip_db:
            main_window.rerun_summary_all_days("Species names were changed.")

    edit_trip_species = MenuSpec(
        callback=_edit_species_list,
        title="Edit Species List",
        enabled=has_active_trip,
        showsubdirectory=False,
    )

    count_species = MenuSpec(
        callback=None,
        title="Count Species",
        enabled=has_active_trip,
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svr2CountSpecies.main,
            'before_msg': "Analyzing species information ...",
            'after_msg': "✅ Species report will be shown."
        }
    )
    def _show_count_species():
        # show(), not exec(): the report window is independent, so the Trip
        # overview stays clickable and you can chase a day's species errors
        # with the report still open beside it. main_window keeps the only
        # reference — the dialog has no Qt parent to keep it alive.
        dlg = CountSpeciesDialog(main_window, count_species)
        main_window._count_species_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    count_species.callback = _show_count_species

    manage_blogs = MenuSpec(
        callback=lambda: BlogAdminDialog(main_window).exec(),
        title="Manage Blogs",
        showsubdirectory=False,
    )

    redo_trip_blogs = MenuSpec(
        callback=None,
        title="4. Make the Blogs Clickable",
        enabled=any_day_published,
        tooltip="Add species links & navigation",
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svr9RedoBlog.main,
            'before_msg': "Adding species links & navigation to the blogs ...",
            'after_msg': "✅ Blogs are now clickable (species links + navigation)."
        }
    )
    redo_trip_blogs.callback = lambda: RedoTripBlogsDialog(main_window, redo_trip_blogs).exec()

    empty_trip_data = MenuSpec(
        callback=None,
        title="Reset Trip",
        enabled=has_active_trip,
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svu6EmptyTripData.main,
            'before_msg': "Resetting trip ...",
            'after_msg': "✅ Trip reset — ready to customize again."
        }
    )
    empty_trip_data.callback = lambda: EmptyTripDataDialog(main_window, empty_trip_data).exec()

    recreate_trip = MenuSpec(
        callback=None,
        title="Recreate Trip Repository",
        enabled=has_active_trip,
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svr8RecreateTripRepository.main,
            'before_msg': "Delete existing repository and rebuild it ...",
            'after_msg': "✅ Trip recreated."
        }
    )
    recreate_trip.callback = lambda: RedoTripRepoDialog(main_window, recreate_trip).exec()    

    # Master updates
    merge_trip_species = MenuSpec(
        callback=None,
        title="Publish Species Names",
        enabled=has_trip_and_master,
        tooltip="Merge the trip's species into the master",
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svr5MergeSpeciesCollections.main,
            'before_msg': "List and merge species lists from master and trip ...",
            'after_msg': "✅ Review merge results, and decide to copy to master."
        }
    )
    merge_trip_species.callback = lambda: MergeTripDialog(main_window, merge_trip_species).exec()

    # Publication of the ACTIVE trip, both directions in one dialog (the album
    # has the identical door under Albums — same class, see gui/publish_dialog).
    # The old preview-and-remove table over EVERY published entry now lives under
    # Master ▸ Manage Site Publication, for listings whose repository is gone.
    def _manage_trip_publication():
        from gui.publish_dialog import TripPublishDialog
        TripPublishDialog(main_window).exec()

    manage_trips = MenuSpec(
        callback=_manage_trip_publication,
        title="5. Manage Trip Publication…",
        tooltip="List this trip on the site's trip index, or remove it from there",
        enabled=has_active_trip,
        showsubdirectory=False,
    )

    def _manage_site_publication():
        from gui.site_publication_dialog import SitePublicationDialog
        SitePublicationDialog(main_window).exec()

    manage_site_publication = MenuSpec(
        callback=_manage_site_publication,
        title="Manage Site Publication…",
        tooltip="Everything listed on the site's trip index — remove a listing "
                "whose trip or album you can no longer open",
        enabled=has_master,
        showsubdirectory=False,
    )

    # ── Master pipeline: ingest a trip's pictures into master itself ──
    # Three sequential steps wrapped in MasterPipelineDialog. Each
    # step's MenuSpec sets master_mode=True so generate_svconfig_ini
    # writes BiotaDatabase = master path (and MasterBiotaDatabase
    # blank) — the same "no trip" config the scripts have always
    # accepted from the CLI.
    def _master_ingest(args, _config):
        batch.sv2ReadDirExif.main(args, _config)
        batch.sv3WritePictures.main(args, _config)

    def _master_build(args, _config):
        # Build Website (all-species index → allspecies.csv) then Freeze.
        batch.sv5BuildWebsite.main(args, _config)
        batch.sv6Freeze.main(args, _config)

    master_ingest_spec = MenuSpec(
        callback=None,
        title="Read & Summarize (master)",
        showsubdirectory=False,
        batch_parms={
            'statusname':     "",
            'module_called':  _master_ingest,
            'before_msg':     "Reading picture directory and writing summary into master...",
            'after_msg':      "✅ Master ingest complete.",
            'master_mode':    True,
        },
    )
    master_build_spec = MenuSpec(
        callback=None,
        title="Build & Freeze (master)",
        showsubdirectory=False,
        batch_parms={
            'statusname':     "",
            'module_called':  _master_build,
            'before_msg':     "Building master species pages and freezing...",
            'after_msg':      "✅ Master pages built and frozen.",
            'master_mode':    True,
        },
    )
    master_commit_spec = MenuSpec(
        callback=None,
        title="Commit to Master",
        showsubdirectory=False,
        batch_parms={
            'statusname':     "",
            'module_called':  batch.sv7WriteCommit.main,
            'before_msg':     "Committing frozen artifact to master active CSVs...",
            'after_msg':      "✅ Master commit complete.",
            'master_mode':    True,
        },
    )
    # Step 1 runs the REGULAR Count Species — deliberately without
    # master_mode, so it reads the trip's day summaries and the trip's frozen
    # baseline. With master_mode it would scan the master's own review tree and
    # report on the whole collection. It goes first because the master is still
    # pre-trip at that moment, which is what makes the lifer verdict true.
    def _count_and_archive(args, _config):
        batch.svr2CountSpecies.main(args, _config)
        batch.svr7ArchiveVerdict.main(args, _config)

    master_verdict_spec = MenuSpec(
        callback=None,
        title="Count Species (final verdict)",
        showsubdirectory=False,
        batch_parms={
            'statusname':     "",
            'module_called':  _count_and_archive,
            'before_msg':     "Counting species and archiving the trip's verdict...",
            'after_msg':      "✅ Species verdict archived in the master.",
        },
    )
    master_pipeline_steps = [
        ("1. Count Species (final verdict)",   master_verdict_spec, "verdict"),
        ("2. Read & Summarize",                master_ingest_spec,  "ingest"),
        ("3. Build Master Website & Freeze",   master_build_spec,   "freeze"),
        ("4. Commit to Master",                master_commit_spec,  "commit"),
    ]
    add_trip_pictures = MenuSpec(
        callback=None,
        title="Merge Pictures into Master",
        enabled=merge_ready,
        tooltip="Copy the trip's pictures into the master",
        showsubdirectory=False,
    )
    add_trip_pictures.callback = lambda: MasterPipelineDialog(
        main_window, master_pipeline_steps).exec()

    # Add pictures from ANY folder to the master (trip-independent), via an
    # ephemeral micro-trip. First slice: scan + report only.
    def _add_pictures_to_master():
        from gui.add_to_master import AddToMasterDialog
        # Non-modal session controller — the user edits in the Trip tab while it
        # stays open. Keep a reference so it isn't garbage-collected.
        dlg = getattr(main_window, "_add_to_master_dialog", None)
        if dlg is None:
            dlg = AddToMasterDialog(main_window)
            main_window._add_to_master_dialog = dlg
        # Master mode: editable folder (e.g. leftovers from a moved trip).
        dlg.open_session()
    add_pictures_to_master_item = MenuSpec(
        callback=_add_pictures_to_master,
        title="Add Pictures to Master…",
        enabled=has_master,
        showsubdirectory=False,
    )

    def _open_lifecycle_board():
        # The lifecycle board is a VIEW over the same MenuSpecs + gating predicates
        # the menu uses (built here at click time, so specs defined further down
        # resolve fine). Both the menu and this board stay — companions.
        from gui.trip_lifecycle_board import TripLifecycleBoard

        def _raise_yellow_tab(match):
            # Bring a yellow-panel tab forward (best-effort). The pipeline steps and
            # Customize Album point here rather than firing a gated batch action —
            # the yellow panel is where the user picks what to run per day.
            try:
                tabs = getattr(main_window, "yellow_tabs", None)
                if tabs is not None:
                    for i in range(tabs.count()):
                        if match in tabs.tabText(i):
                            tabs.setCurrentIndex(i)
                            break
            except Exception as e:
                logger.info(f"lifecycle board: raise '{match}' tab: {e}")

        model = [
            {"title": "Trip", "sub": "the editable working repo",
             "gate": ("no edits possible", lambda: all(_pub_state())),
             "groups": [
                 # The pipeline steps are re-runnable any time and are driven per-day
                 # from the Active Trip yellow panel — so these cards NAVIGATE there
                 # (always enabled given a trip) rather than firing a gated batch step.
                 # The progress bar still shows how many days each step has reached.
                 {"label": "Pipeline — open the Active Trip panel to run per day", "blocks": [
                     {"title": "Read & Summarize", "desc": "Read photos, recognise species",
                      "on_click": lambda: _raise_yellow_tab("Active Trip"),
                      "enabled": has_active_trip, "close_after": True, "prog": "read"},
                     {"title": "Write Blog", "desc": "Generate the day blogs",
                      "on_click": lambda: _raise_yellow_tab("Active Trip"),
                      "enabled": has_active_trip, "close_after": True, "prog": "blog"},
                     {"title": "Publish Day", "desc": "Freeze & publish each day",
                      "on_click": lambda: _raise_yellow_tab("Active Trip"),
                      "enabled": has_active_trip, "close_after": True, "prog": "publish"},
                 ]},
                 {"label": "Publication (one state, trip → album)", "blocks": [
                     {"title": "Publish trip", "desc": "Advertise / un-advertise on the site",
                      "spec": manage_trips},
                 ]},
             ]},
            {"title": "To Master", "sub": "into the shared collection",
             "gate": ("no more changes", has_merged),
             "groups": [{"label": "Merge", "blocks": [
                 {"title": "Publish Species Names", "desc": "Push new species names to the master site",
                  "spec": merge_trip_species},
                 {"title": "Merge Pictures into Master", "desc": "Copy the trip's pictures into the master",
                  "spec": add_trip_pictures, "done": has_merged},
             ]}]},
            {"title": "Album", "sub": "live master date-range view",
             "gate": ("changes now happen in the album", has_retired),
             "groups": [{"label": "Create & edit", "blocks": [
                 {"title": "Retire Trip to Album", "desc": "Create the album from the master",
                  "spec": retire_trip_item, "done": has_retired},
                 {"title": "Customize Album", "desc": "Show the album in the yellow panel",
                  "on_click": lambda: _raise_yellow_tab("Album"), "enabled": has_retired,
                  "close_after": True},
             ]}]},
            {"title": "Clean up", "sub": "reclaim local space",
             "gate": None,
             "groups": [{"label": "Delete", "blocks": [
                 {"title": "Delete Local Files", "desc": "Remove local originals already in the master",
                  "spec": delete_trip_item, "warn": True},
                 {"title": "Delete Trip", "desc": "Permanently delete the trip repository",
                  "spec": delete_trip_repo_item, "warn": True},
             ]}]},
        ]
        TripLifecycleBoard(main_window, model).exec()

    trip_master_dashboard = MenuSpec(
        callback=_open_lifecycle_board,
        title="Trip to Master Dashboard",
        enabled=has_active_trip,
        showsubdirectory=False,
    )

    # Edit the master speciescollection.csv directly — same dialog the
    # Write Summary trip flow exposes, just pointed at the master path.
    # Same one list as the Trip menu's entry — see _edit_species_list.
    edit_master_species = MenuSpec(
        callback=_edit_species_list,
        title="Edit Species List",
        enabled=has_master,
        showsubdirectory=False,
    )

    def _manage_caches():
        from gui.cache_manager import ManageCachesDialog
        ManageCachesDialog(main_window).exec()

    def _rebuild_all_websites():
        from gui.rebuild_all_dialog import RebuildAllWebsitesDialog
        RebuildAllWebsitesDialog(main_window).exec()

    manage_caches = MenuSpec(
        callback=_manage_caches,
        title="Manage caches…",
        showsubdirectory=False,
    )

    # Warm the SEPARATE master cache (exif_cache_master.json) by reading EXIF
    # for every picture under <master>/biota. Kept apart from the day/trip
    # exif_cache.json so the ~89MB master cache never slows Read Directory.
    def _rescan_master_exif_cache(args, _mod_config):
        master = exifcache.master_repository(_mod_config)
        root = os.path.join(master, "biota")
        exifcache.rescan(root, exifcache.master_cache_path(_mod_config))

    rescan_master_cache = MenuSpec(
        callback=None,
        title="Rescan master EXIF cache",
        enabled=has_master,
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': _rescan_master_exif_cache,
            'before_msg': "Reading EXIF for all master pictures into the cache...",
            'after_msg': "✅ Master EXIF cache updated.",
        }
    )
    rescan_master_cache.callback = lambda: RescanMasterCacheDialog(
        main_window, rescan_master_cache).exec()

    # utilities
        # Master updates
    convert_avibase = MenuSpec(
        callback=None,
        title="Create an Excel to keep track of birds seen.",
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svu2avibase.main,
            'before_msg': "Reading a XLS CSV file read from a copy.",
            'after_msg': "✅ XLSX checklist created."
        }
    )
    convert_avibase.callback = lambda: ConvertAvibaseDialog(main_window, convert_avibase).exec()

    photo_mechanic = MenuSpec(
        callback=None,
        title="Create PhotoMechanic keyword files for next trip.",
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svu8PhotoMechanic.main,
            'before_msg': "Generating PhotoMechanic keyword snap files...",
            'after_msg': "✅ PhotoMechanic snap files created."
        }
    )
    photo_mechanic.callback = lambda: PhotoMechanicDialog(main_window, photo_mechanic).exec()

    small_master = MenuSpec(
        callback=None,
        title="Create Small Master Database",
        enabled=has_master,
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svu9SmallMaster.main,
            'before_msg': "Copying master biota files (JPG/XMP only)...",
            'after_msg': "✅ SmallMaster copy complete.",
        }
    )
    small_master.callback = lambda: SmallMasterDialog(main_window, small_master).exec()

    install_small_master = MenuSpec(
        callback=lambda: InstallSmallMasterDialog(main_window).exec(),
        title="Install Small Master Database",
    )

    export_trip = MenuSpec(
        callback=None,
        title="Export Trip",
        enabled=has_active_trip,
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svr6ExportTrip.main,
            'before_msg': "Zipping the trip's settings, species list and translations...",
            'after_msg': "✅ Trip export complete.",
        }
    )
    export_trip.callback = lambda: ExportTripDialog(main_window, export_trip).exec()

    import_trip = MenuSpec(
        callback=lambda: ImportTripDialog(main_window).exec(),
        title="Import Trip",
    )

    def _restore_work():
        """Import Trip's larger sibling: the trip identity is only the first
        part of coming home — the days' culling is the rest, and it lives on the
        SV INDEX card rather than in the trip export."""
        from gui.work_restore_dialog import RestoreWorkDialog
        from utils.trip_identity import resolve_trip_picture_root
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="").strip()
        if not trip_root:
            QMessageBox.warning(main_window, "Restore work from a card",
                                "Open the trip you want to restore into first.")
            return
        pic = resolve_trip_picture_root(trip_root, kind="trip")
        RestoreWorkDialog(main_window, trip_root, pic).exec()

    restore_work = MenuSpec(
        callback=_restore_work,
        title="Restore Work from Card",
        enabled=has_active_trip,
    )

    def _empty_card():
        """The other end of Read SD-cards: a card whose pictures have all been
        judged can have its keepers saved to a second card and then be re-used.
        Needs the trip because the verdicts live beside the trip's pictures."""
        from pathlib import Path
        from gui.card_empty_dialog import EmptyCardDialog
        from utils.trip_identity import resolve_trip_picture_root
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="").strip()
        if not trip_root:
            QMessageBox.warning(main_window, "Empty a card",
                                "Open the trip the card was read into first.")
            return
        pic = resolve_trip_picture_root(trip_root, kind="trip")
        # The PICTURE folder's name ("2026 North Greece"), not the repo id
        # ("greecedb"). This ends up written on a physical card that gets read
        # months later, and it should match the prefix on every file inside it.
        EmptyCardDialog(main_window, Path(pic).name or Path(trip_root).name,
                        pic).exec()

    empty_card = MenuSpec(
        callback=_empty_card,
        title="Empty a Card",
        tooltip="Check that a card's pictures are all dealt with, save the "
                "keepers to a second card, and mark it safe to format",
        enabled=has_active_trip,
        showsubdirectory=False,
    )

    export_virttrip = MenuSpec(
        callback=None,
        title="Export Album",
        enabled=has_active_album,
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svv7ExportVirtualTrip.main,
            'before_msg': "Zipping album (recipe + blog drafts + day data)...",
            'after_msg': "✅ Album export complete.",
            'virttrip_mode': True,
        }
    )
    export_virttrip.callback = lambda: ExportVirtualTripDialog(main_window, export_virttrip).exec()

    import_virttrip = MenuSpec(
        callback=lambda: ImportVirtualTripDialog(main_window).exec(),
        title="Import Album",
        enabled=has_master,
    )

    testbed = MenuSpec(
        callback=None,
        title="Call Testbed for trip day",
        batch_parms={
            'statusname': "",
            'module_called': batch.svu7Testbed.main,
            'before_msg': "Running testbed for trip day...",
            'after_msg': "✅ Testbed run complete.",
        }
    )
    testbed.callback = lambda: TestbedDialog(main_window, testbed).exec()

    virttrip_create = MenuSpec(
        callback=None,
        title="Create Album",
        enabled=has_full_master,
        batch_parms={
            'statusname': "",
            'module_called': batch.svv1MakeVirtualRepository.main,
            'before_msg': "Creating album directory structure...",
            'after_msg': "✅ Album created. Fill in the album details and select a date range."
        }
    )
    virttrip_create.callback = lambda: (
        require_full_master(main_window)
        and VirtualTripCreationDialog(main_window, virttrip_create).exec())

    # Read-only companion to Create: the date range is the thing you cannot
    # remember, and the master already knows it. Report only — see
    # gui/trip_finder_view.py.
    def _find_trips():
        if not require_full_master(main_window):
            return
        import gui.trip_finder_view as tf
        tf.show(main_window, _config)

    def _delete_album():
        from gui.album_delete_dialog import AlbumDeleteDialog
        AlbumDeleteDialog(main_window).exec()

    def _manage_album_publication():
        from gui.publish_dialog import AlbumPublishDialog
        AlbumPublishDialog(main_window).exec()

    find_trips = MenuSpec(
        callback=_find_trips,
        title="Find Trips in the Master…",
        enabled=has_full_master,
        tooltip="Report: date ranges that look like trips, to use when creating one",
        showsubdirectory=False,
    )

    rating_editor = MenuSpec(
        callback=lambda: main_window.open_rating_editor(),
        title="Trip Species Viewer",
        enabled=has_active_trip,
        showsubdirectory=False,
    )

    # ── Trip retirement: from the ACTIVE trip, create the successor virtual trip
    # (same name/range/text), build it (svv1), extract its master days (svv2),
    # and migrate the day blogs — one action. The svr7 delete stays separate.
    retire_trip_item = MenuSpec(
        callback=lambda: require_full_master(main_window)
                         and retire_trip_to_virttrip(main_window),
        title="Retire Trip to Album",
        enabled=retire_ready,
        showsubdirectory=False,
    )

    # ── Retirement step 2: delete the trip's local files that are already in the
    # master. Ground-truth driven (engine/trip_delete_scan): walk the ACTUAL
    # files on disk under the trip picture root and classify each against the
    # master. A file is deletable only when a byte-identical copy is confirmed in
    # the master (its UPN is in the master AND its exact name is in the master
    # file list) — the master is the backup. 'keep' (not in master) and 'held'
    # (UPN in master but this exact local render is not) files are never touched.
    # Shows the full report; the explicit "Delete N files" button is the confirm.
    def _delete_trip_in_master():
        from PyQt6.QtWidgets import (QApplication, QMessageBox, QDialog, QVBoxLayout,
                                     QPlainTextEdit, QDialogButtonBox)
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QFont
        import engine.trip_delete_scan as tds
        master = _config.get("master", "masterbiotadatabase", fallback="").replace('"', "").replace("'", "")
        trip_db = _config.get("trip", "tripbiotadatabase", fallback="").replace('"', "").replace("'", "")
        picdir = _config.get("project", "picturedirectory", fallback="").replace('"', "").replace("'", "")
        picture_root = os.path.dirname(picdir.rstrip("/\\")) if picdir else ""
        if not master or not trip_db or not picture_root:
            QMessageBox.warning(main_window, "Delete Trip Files",
                                "Select an active trip and a master first.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            report = tds.scan_trip(picture_root, master, trip_db)
            detail = tds.write_detail_csv(
                report, os.path.join(trip_db, "metadata", "trip_delete_dryrun.csv")) \
                if report.get("ok") else None
        finally:
            QApplication.restoreOverrideCursor()
        if not report.get("ok"):
            QMessageBox.warning(main_window, "Delete Trip Files", report.get("error", "Failed."))
            return
        t = report["totals"]
        n_files = t["deletable_files"]
        text = tds.format_report(report)
        if detail:
            text += f"\n\nPer-file detail written to:\n{detail}"

        dlg = QDialog(main_window)
        dlg.setWindowTitle("Delete Trip Files Backed Up in Master")
        dlg.resize(760, 520)
        lay = QVBoxLayout(dlg)
        view = QPlainTextEdit(text, readOnly=True)
        view.setFont(QFont("Consolas", 10))
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(view)
        bb = QDialogButtonBox()
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        if n_files:
            del_btn = bb.addButton(f"Delete {n_files} files",
                                   QDialogButtonBox.ButtonRole.AcceptRole)
            del_btn.setStyleSheet("color: white; background-color: #b00020; padding: 4px 10px;")
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted or not n_files:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            nf, npx, errs = tds.execute_scan_delete(report)
        finally:
            QApplication.restoreOverrideCursor()
        for e in errs[:20]:
            main_window.logger.warning(f"delete: {e}")
        extra = f"\n{len(errs)} files could not be deleted (see log)." if errs else ""
        kept_id = t.get('keep_with_species', 0)
        id_note = (f" ({kept_id} have a species — rescue via Add Pictures to Master "
                   f"before retiring the whole trip)") if kept_id else ""
        QMessageBox.information(main_window, "Delete Trip Files",
                               f"Deleted {npx} pictures ({nf} files).\n"
                               f"Kept {t['keep_pictures']} pictures not in the master{id_note} and "
                               f"{t['held_pictures']} held local renders.{extra}")

    delete_trip_item = MenuSpec(
        callback=_delete_trip_in_master,
        title="Delete Local Files Backed Up in Master…",
        enabled=has_merged,
        showsubdirectory=False,
    )

    # ── Delete Trip: permanently remove the trip REPOSITORY (its metadata copy),
    # reusing the vetted retire flow (engine.trip_retire_scan pre-flight + explicit
    # confirm). Operates on the ACTIVE trip. Blocker B3 refuses to retire the active
    # trip (the GUI must not be left pointing at a deleted repo); retire_trip_dialog
    # now DE-SELECTS the active trip up-front (clears the trip config + repaints the
    # overview + rebuilds the menu → the no-trip state), which satisfies B3 and lets
    # the delete proceed in place.
    def _delete_trip_repo():
        from gui.trip_retire import retire_trip_dialog
        trip_db = _config.get("trip", "tripbiotadatabase", fallback="").replace('"', "").replace("'", "")
        if not trip_db:
            QMessageBox.warning(main_window, "Delete Trip", "Select an active trip first.")
            return
        retire_trip_dialog(main_window, trip_db)

    delete_trip_repo_item = MenuSpec(
        callback=_delete_trip_repo,
        title="Delete Trip…",
        enabled=delete_trip_ready,
        tooltip="Permanently delete the trip repository (its metadata copy). "
                "CurrentPictures, the master and the album are untouched.",
        showsubdirectory=False,
    )

    # ── Trip Status Console: cross-machine (virt)trip lifecycle overview ──
    # Scans this machine's trips, scores each against the master (which pictures
    # are already integrated / which would be lost), writes the per-machine shard
    # CSV, renders the local private HTML console, and optionally uploads the
    # shard to 'includes web/status/' so the other machine can be merged in later.
    def _trip_status_console():
        import webbrowser
        from pathlib import Path
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt
        import engine.trip_registry as tr
        import engine.trip_registry_html as trh
        import engine.trip_thumbs as tt
        from utils.config import MACHINE_NAME, get_trips_root, get_virttrips_root

        master = _config.get("master", "masterbiotadatabase", fallback="")
        if not master:
            QMessageBox.warning(main_window, "Trip Status Console",
                                "No master database is configured.")
            return
        yesno = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        want_thumbs = QMessageBox.question(
            main_window, "Trip Status Console",
            "Generate picture thumbnails for the drill-down?\n"
            "(slower — extracts RAW previews)", yesno,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        want_upload = QMessageBox.question(
            main_window, "Trip Status Console",
            "Upload this machine's status shard to the web?", yesno,
            QMessageBox.StandardButton.Yes) == QMessageBox.StandardButton.Yes

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        upload_msg = ""
        try:
            rows = tr.build_registry(MACHINE_NAME, get_trips_root(),
                                     get_virttrips_root(), master)
            out_dir = os.path.join(master, "admin", "status")
            written = tr.write_registry_csv(
                rows, os.path.join(out_dir, f"trip_registry_{MACHINE_NAME}.csv"))
            console_dir = os.path.join(out_dir, "console")
            thumb_map = tt.generate_thumbs(rows, console_dir) if want_thumbs else None
            index = trh.render_console(rows, console_dir, MACHINE_NAME, thumb_map)
            if want_upload:
                web_root = _config.get("master", "masterbiotawebroot", fallback="/nature/")
                pwd = _config.get("websettings", "sftppwd", fallback="")
                remote = tr.upload_status_shard(written, web_root, pwd)
                upload_msg = ("\nShard uploaded to the web." if remote
                              else "\nShard upload FAILED (see log).")
        finally:
            QApplication.restoreOverrideCursor()
        from utils.open_path import open_in_browser
        open_in_browser(index)
        QMessageBox.information(main_window, "Trip Status Console",
                                f"Console opened in your browser:\n{index}{upload_msg}")

    trip_status_console = MenuSpec(
        callback=_trip_status_console,
        title="Trip Status Console…",
        enabled=has_master,
        showsubdirectory=False,
    )

    # Pull every machine's shard from the web and show the integrated overview
    # (one row per trip, a column per machine). No local scan — fast.
    def _merged_status_console():
        import webbrowser
        from pathlib import Path
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt
        import engine.trip_registry as tr

        master = _config.get("master", "masterbiotadatabase", fallback="")
        if not master:
            QMessageBox.warning(main_window, "Merged Status",
                                "No master database is configured.")
            return
        web_root = _config.get("master", "masterbiotawebroot", fallback="/nature/")
        pwd = _config.get("websettings", "sftppwd", fallback="")
        console_dir = os.path.join(master, "admin", "status", "console")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            merged, machines = tr.build_merged_console(master, web_root, pwd, console_dir)
        finally:
            QApplication.restoreOverrideCursor()
        if not merged:
            QMessageBox.warning(main_window, "Merged Status",
                                "No shards found on the web (see log).")
            return
        from utils.open_path import open_in_browser
        open_in_browser(merged)
        QMessageBox.information(main_window, "Merged Status",
                               f"Merged overview for: {', '.join(machines)}\n{merged}")

    merged_status_console = MenuSpec(
        callback=_merged_status_console,
        title="Merged Status (all machines)…",
        enabled=has_master,
        showsubdirectory=False,
    )

    # Species-counter audit (QA, report only): species.csv counts vs the actual
    # pictures — lists ghosts (with recovered last-known file names), count
    # drift and orphaned best-picks. Never modifies anything.
    def _audit_species_counters():
        import os
        from PyQt6.QtWidgets import (QApplication, QMessageBox, QDialog, QVBoxLayout,
                                     QPlainTextEdit, QPushButton)
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QFont
        import engine.species_counter_audit as sca
        master = _config.get("master", "masterbiotadatabase", fallback="").replace('"', "").replace("'", "")
        if not master:
            QMessageBox.warning(main_window, "Audit Species Counters", "No master is configured.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rep = sca.audit(master)
            text = sca.format_report(rep)
            out = os.path.join(master, "admin", "status", "species_counter_audit.txt")
            try:
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(text)
            except Exception:
                out = None
        finally:
            QApplication.restoreOverrideCursor()
        if not rep.get("ok"):
            QMessageBox.warning(main_window, "Audit Species Counters", rep.get("error", "Failed."))
            return
        if out:
            text += f"\n\nReport written to:\n{out}"
        dlg = QDialog(main_window)
        dlg.setWindowTitle("Audit Species Counters (dry-run, no changes)")
        dlg.resize(840, 580)
        lay = QVBoxLayout(dlg)
        view = QPlainTextEdit(text, readOnly=True)
        view.setFont(QFont("Consolas", 9))
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(view)
        btn = QPushButton("Close")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
        dlg.exec()

    audit_species_item = MenuSpec(
        callback=_audit_species_counters,
        title="Audit Species Counters (dry-run)…",
        enabled=has_master,
        showsubdirectory=False,
    )

    def _species_no_display():
        import gui.master_no_display as nd
        nd.show(main_window, _config)

    no_display_item = MenuSpec(
        callback=_species_no_display,
        title="Species Without a Website Picture…",
        enabled=has_master,
        showsubdirectory=False,
    )

    # APPLY step of Refresh Master Database: remove dead rows + repoint stale
    # paths + reclassify in the master admin. Re-runs the plan fresh, confirms,
    # backs up both CSVs, then writes. This is the only destructive Admin action.
    def _heal_admin():
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt
        import engine.admin_rebuild as admin_rebuild
        master = _config.get("master", "masterbiotadatabase", fallback="").replace('"', "").replace("'", "")
        if not master:
            QMessageBox.warning(main_window, "Refresh Master Database — Apply","No master is configured.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            plan = admin_rebuild.plan_rebuild(master)
        finally:
            QApplication.restoreOverrideCursor()
        if not plan.get("ok"):
            QMessageBox.warning(main_window, "Refresh Master Database — Apply",plan.get("error", "Failed."))
            return
        if plan["remove"] == 0 and plan["repoint"] == 0 and plan["species_change"] == 0:
            QMessageBox.information(main_window, "Refresh Master Database — Apply", "Nothing to refresh.")
            return
        msg = (f"Apply to the master admin (data_files.csv / data_pictures.csv)?\n\n"
               f"  • REMOVE {plan['remove']} dead file rows\n"
               f"  • REPOINT {plan['repoint']} stale paths → real biota location\n"
               f"  • RECLASSIFY {plan['species_change']} pictures to their folder species\n"
               f"  • DROP phantom pictures whose files are all gone\n\n"
               f"(The {plan['add']} adds are NOT applied — mostly DxO renders + junk.)\n\n"
               f"Both CSVs are backed up first to admin/status/backup/.\n"
               f"This rewrites the master admin. Continue?")
        if QMessageBox.question(main_window, "Refresh Master Database — Apply (destructive)", msg,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        logger.info("Refreshing the master database — saving a safety backup first, then tidying up…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            r = admin_rebuild.execute_heal(master)
        except Exception as e:
            r = {"ok": False, "error": f"{type(e).__name__}: {e} (CSV open in Excel / Dropbox lock?)"}
        finally:
            QApplication.restoreOverrideCursor()
        if not r.get("ok"):
            QMessageBox.warning(main_window, "Refresh Master Database — Apply",r.get("error", "Failed."))
            return
        logger.info(
            f"Master database refreshed and safely backed up. Cleared "
            f"{r['removed_files']} stale entries, fixed {r['repointed']} moved pictures, "
            f"and tidied {r['pictures_dropped']} empty ones. Tip: run Count Species to "
            f"update the totals.")
        QMessageBox.information(
            main_window, "Refresh Master Database — Apply — done",
            f"Removed {r['removed_files']} dead file rows.\n"
            f"Repointed {r['repointed']} paths.\n"
            f"Reclassified {r.get('species_changed', 0)} pictures to their folder species.\n"
            f"Dropped {r['pictures_dropped']} phantom pictures "
            f"(cleaned {r['files_cleaned']} Files lists).\n\n"
            f"Backups in: {r['backup_dir']}\n\n"
            f"Next: re-run Count Species to refresh per-species counts; the master "
            f"DB cache rebuilds itself.")

    heal_admin_item = MenuSpec(
        callback=_heal_admin,
        title="Refresh Master Database — Apply…",
        enabled=has_master,
        showsubdirectory=False,
    )

    # REVIEW step of Refresh Master Database. Non-destructive. Always shows the
    # fast summary of what a refresh would change (compares the admin against the
    # actual tree: remove / repoint / add / species). Then optionally writes the
    # full SUGGESTED data_files.csv / data_pictures.csv (for a row-by-row Excel
    # diff) plus a per-picture display_status.csv (black-image suspects) into a
    # timestamped folder under admin/status/. The master is never touched; folders
    # are kept so runs can be compared over time.
    def _refresh_review():
        import os
        from datetime import datetime
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt, QUrl
        from PyQt6.QtGui import QDesktopServices
        import engine.admin_rebuild as admin_rebuild
        master = _config.get("master", "masterbiotadatabase", fallback="").replace('"', "").replace("'", "")
        if not master:
            QMessageBox.warning(main_window, "Refresh Master Database — Review", "No master is configured.")
            return
        out = os.path.join(master, "admin", "status",
                           "review_" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        # Fast pass: plan only (one tree scan, small diff reports).
        logger.info("Reviewing the master database — comparing it with the pictures on disk…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            plan = admin_rebuild.plan_rebuild(master, out_dir=out)
        except Exception as e:
            plan = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            QApplication.restoreOverrideCursor()
        if not plan.get("ok"):
            QMessageBox.warning(main_window, "Refresh Master Database — Review", plan.get("error", "Failed."))
            return
        logger.info(
            f"Review done — nothing has been changed. A refresh would clear "
            f"{plan['remove']} entries for pictures no longer on disk and fix "
            f"{plan['repoint']} that have moved.")
        bd = plan.get("add_breakdown", {})
        add_detail = (" (" + ", ".join(f"{v} {k}" for k, v in sorted(bd.items())) + ")") if bd else ""
        summary = (f"A refresh would change the master database:\n\n"
                   f"  • REMOVE {plan['remove']} dead file rows (file nowhere)\n"
                   f"  • REPOINT {plan['repoint']} stale paths (file moved)\n"
                   f"  • RECLASSIFY {plan['species_change']} pictures (folder ≠ admin)\n"
                   f"  • ADD {plan['add']} files not in the admin{add_detail} — not applied\n\n"
                   f"Nothing has changed. Diff reports written to:\n{out}\\rebuild_*.csv\n\n"
                   f"Also write the full SUGGESTED data_files.csv / data_pictures.csv for an\n"
                   f"Excel comparison, plus the black-image scan? (slower, a few minutes)")
        if QMessageBox.question(main_window, "Refresh Master Database — Review", summary,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        # Heavy pass: suggested CSVs + display-status scan.
        disp = {}
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            heal = admin_rebuild.execute_heal(master, out_dir=out, report_dir=out)
            if heal.get("ok"):
                disp = admin_rebuild.scan_display_status(
                    master, out_csv=os.path.join(out, "display_status.csv"))
        except Exception as e:
            heal = {"ok": False, "error": f"{type(e).__name__}: {e} (CSV open in Excel / Dropbox lock?)"}
        finally:
            QApplication.restoreOverrideCursor()
        if not heal.get("ok"):
            QMessageBox.warning(main_window, "Refresh Master Database — Review", heal.get("error", "Failed."))
            return
        bs = disp.get("by_status", {}) if disp.get("ok") else {}
        logger.info(f"Saved a suggested copy you can open in Excel to compare: {out}")
        if disp.get("ok"):
            logger.info(f"Picture check: {bs.get('black', 0)} still show no image "
                        f"(these disappear once you Apply).")
        active = os.path.join(master, "metadata", "active")
        msg = (f"Wrote the SUGGESTED CSVs (master untouched):\n{out}\n\n"
               f"  data_files.csv   : {heal['kept_files']} rows "
               f"(−{heal['removed_files']} dead, {heal['repointed']} repointed)\n"
               f"  data_pictures.csv: −{heal['pictures_dropped']} phantom pictures\n\n"
               f"Black-image scan (display_status.csv):\n"
               f"  black {bs.get('black', 0)} · shows-via-RAW {bs.get('no_bestjpg', 0)} · "
               f"has-JPG {bs.get('jpg_ok', 0)}\n\n"
               f"Compare in Excel against the live CSVs in:\n{active}\n\n"
               f"Open the folder now?")
        if QMessageBox.question(main_window, "Refresh Master Database — Review", msg,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.Yes) == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(out))

    refresh_review_item = MenuSpec(
        callback=_refresh_review,
        title="Refresh Master Database — Review…",
        enabled=has_master,
        showsubdirectory=False,
    )

    # Move orphan sidecars (XMP/DOP whose image was deleted) to a recoverable
    # trash; their now-dangling admin rows are swept by the next Apply run.
    def _clean_orphan_sidecars():
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt
        import engine.admin_rebuild as admin_rebuild
        master = _config.get("master", "masterbiotadatabase", fallback="").replace('"', "").replace("'", "")
        if not master:
            QMessageBox.warning(main_window, "Clean Orphan Sidecars", "No master is configured.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = admin_rebuild.find_orphan_sidecars(master)
        finally:
            QApplication.restoreOverrideCursor()
        n = info.get("orphan_files", 0)
        if n == 0:
            QMessageBox.information(main_window, "Clean Orphan Sidecars", "No orphan sidecars found.")
            return
        by = info.get("by_ext", {})
        detail = ", ".join(f"{v} .{k}" for k, v in sorted(by.items()))
        if QMessageBox.question(
                main_window, "Clean Orphan Sidecars",
                f"Move {n} orphan sidecar(s) ({detail}) to a recoverable trash "
                f"folder?\n\nThese have no image left in their picture group.\n"
                f"Run Refresh Master Database — Apply afterward to clear their admin rows.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            r = admin_rebuild.clean_orphan_sidecars(master)
        except Exception as e:
            r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            QApplication.restoreOverrideCursor()
        if not r.get("ok"):
            QMessageBox.warning(main_window, "Clean Orphan Sidecars", r.get("error", "Failed."))
            return
        QMessageBox.information(
            main_window, "Clean Orphan Sidecars — done",
            f"Moved {r['moved']} orphan sidecar(s) to:\n{r['trash']}\n\n"
            f"Run Refresh Master Database — Apply to clear their (now-dangling) admin rows.")

    clean_orphans_item = MenuSpec(
        callback=_clean_orphan_sidecars,
        title="Clean Orphan Sidecars (move to trash)…",
        enabled=has_master,
        showsubdirectory=False,
    )

    def _add_place_names():
        from gui.master_geocode import open_master_geocode
        open_master_geocode(main_window)

    # A data REPAIR tool, so it belongs beside the other master-data maintenance
    # (Refresh / Audit / Clean Orphans), not in the Master menu, which is about
    # publishing and viewers. Shown to everyone, not gated behind debug: it is
    # review-first (nothing is written until the table is confirmed) and it only
    # touches pictures that have NO place name at all.
    add_place_names_item = MenuSpec(
        callback=_add_place_names,
        title="Add Missing Place Names…",
        enabled=has_master,
        tooltip="Look up the place for master pictures that have GPS but no "
                "location, city or country — they show as bare coordinates on "
                "the species map",
        showsubdirectory=False,
    )

    # Fill each picture's coordinate from its camera files. The master pipeline does
    # not always copy the camera's fix up from the file EXIF to the picture row, so the
    # picture can read empty while the RAW/JPG holds a coordinate — and everything that
    # reads the PICTURE row (the species distribution map, the album No-GPS counts) then
    # goes blind to it. Additive only (never changes/removes an existing coordinate).
    def _consolidate_gps():
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt
        from db.master_db import MasterDB
        master = _config.get("master", "masterbiotadatabase", fallback="").replace('"', "").replace("'", "")
        if not master:
            QMessageBox.warning(main_window, "Fill Picture GPS from Files", "No master is configured.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            db = MasterDB(master)
            db.ensure_fresh()
            report = db.consolidate_picture_gps(apply=False)
        finally:
            QApplication.restoreOverrideCursor()
        n = report["candidates"]
        if n == 0:
            QMessageBox.information(main_window, "Fill Picture GPS from Files",
                "Every picture already carries its own coordinate — nothing to fill.")
            return
        msg = (f"{n} master picture(s) have a camera coordinate in their files but an "
               f"empty coordinate on the picture itself.\n\n"
               f"The species distribution map and the album No-GPS counts read the "
               f"picture's coordinate, so those pictures are invisible to them until "
               f"this is filled.\n\n"
               f"Copy the camera coordinate up into the picture rows now?\n"
               f"(Additive only — it never changes or removes an existing coordinate.)")
        if QMessageBox.question(main_window, "Fill Picture GPS from Files", msg,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        logger.info(f"Filling {n} picture coordinate(s) from their camera files…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        r = None
        try:
            r = db.consolidate_picture_gps(apply=True)
        except Exception as e:
            QMessageBox.warning(main_window, "Fill Picture GPS from Files", f"{type(e).__name__}: {e}")
        finally:
            QApplication.restoreOverrideCursor()
        if r:
            logger.info(f"Filled {r['applied']} picture coordinate(s) from the camera files.")
            QMessageBox.information(main_window, "Fill Picture GPS from Files",
                f"Filled {r['applied']} picture coordinate(s). They now appear on the "
                f"species distribution map and count as having GPS.")

    consolidate_gps_item = MenuSpec(
        callback=_consolidate_gps,
        title="Fill Picture GPS from Files…",
        enabled=has_master,
        showsubdirectory=False,
    )

    def _tracklog_geotag():
        from gui.tracklog_geotag_dialog import open_tracklog_geotag
        open_tracklog_geotag(main_window)

    # A third data-repair tool alongside the two above: where those two fill
    # a picture's coordinate from data the master ALREADY has (a camera file,
    # an address lookup), this one sources GPS/altitude from an EXTERNAL
    # tracklog file (GPX / OM-1 log) — the only way to cover a day the
    # camera's own GPS was never enabled, and the only source for altitude
    # (the camera never records it). Review-first like its siblings.
    tracklog_geotag_item = MenuSpec(
        callback=_tracklog_geotag,
        title="Geotag from Tracklog (GPX/GPS log)…",
        enabled=has_master,
        tooltip="Fill missing GPS (a day the camera's GPS wasn't enabled) and/or "
                "altitude (which the camera never records) by matching pictures "
                "to a GPX/GPS tracklog by time",
        showsubdirectory=False,
    )

    # Force a full website re-render (every taxon/species page, not just the ones
    # changed this run) + upload — for when a SHARED TEMPLATE changed and must reach
    # all master pages at once. HTML-only + publish-only: it never freezes or commits
    # master data (deliberately separate from the data pipeline). master_mode=True so
    # the batch runs against the master repository config.
    master_rebuild_web_spec = MenuSpec(
        callback=None,
        title="Rebuild Entire Website",
        showsubdirectory=False,
        batch_parms={
            'statusname':     "",
            'module_called':  batch.svMasterRebuildWeb.main,
            'before_msg':     "Re-rendering and uploading every master page…",
            'after_msg':      "✅ Master website fully rebuilt and uploaded.",
            'master_mode':    True,
        },
    )
    rebuild_master_web_item = MenuSpec(
        callback=lambda: MasterRebuildWebsiteDialog(main_window, master_rebuild_web_spec).exec(),
        title="Rebuild Entire Website (all pages)…",
        enabled=has_master,
        tooltip="Re-render every master species/taxon page (e.g. after a template "
                "change) and upload. HTML only — no master data is changed.",
        showsubdirectory=False,
    )

    # Master-data repair (the Refresh Master Database pair + the two GPS fixers)
    # is grouped under Master ▸ Maintain Master Database — always visible; Apply is
    # the only destructive action. The consoles / testbed diagnostics live under
    # File ▸ Developer Tools, shown only when "Enable debug functionality" is on
    # (Global Settings; read at build time, so toggling takes effect on restart).
    maintain_master_menu = {
        "Refresh Master Database — Review…": refresh_review_item,
        "Refresh Master Database — Apply…": heal_admin_item,
        f"{MenuSpec.separator()}_maint_gps": None,
        "Add Missing Place Names…": add_place_names_item,
        "Fill Picture GPS from Files…": consolidate_gps_item,
        "Geotag from Tracklog (GPX/GPS log)…": tracklog_geotag_item,
        f"{MenuSpec.separator()}_maint_web": None,
        "Rebuild Entire Website (all pages)…": rebuild_master_web_item,
        "Manage Site Publication…": manage_site_publication,
    }
    developer_tools_menu = {
        "Trip Status Console…": trip_status_console,
        "Merged Status (all machines)…": merged_status_console,
        f"{MenuSpec.separator()}_dev_0": None,
        "Audit Species Counters (dry-run)…": audit_species_item,
        "Clean Orphan Sidecars (move to trash)…": clean_orphans_item,
        f"{MenuSpec.separator()}_dev_1": None,
        "Call Testbed for trip day": testbed,
    }

    return {
        "File": {
            "Settings": {
                "Global Settings": MenuSpec(
                    callback=lambda: GlobalSettingsDialog(main_window).exec(),
                    shortcut="Ctrl+G",
                ),
                "Look & Feel…": MenuSpec(
                    callback=lambda: LookFeelDialog(main_window).exec(),
                ),
                "Repository Settings": MenuSpec(
                    callback=lambda: RepoSettingsDialog(main_window).exec(),
                ),
                "Species Settings": MenuSpec(
                    callback=lambda: SpeciesSettingsDialog(main_window).exec(),
                ),
                "Database Settings": MenuSpec(
                    callback=lambda: DatabaseSettingsDialog(main_window).exec(),
                ),
                "Decode Quality…": MenuSpec(
                    callback=lambda: RawProcessingDialog(main_window).exec(),
                ),
                "Location Settings…": MenuSpec(
                    callback=lambda: LocationSettingsDialog(main_window).exec(),
                ),
                # The three DxO/Lightroom options were scattered (the program
                # path sat in Global Settings, the other two loose in this menu).
                # Grouped, because they are one subject: how we hand pictures to
                # PhotoLab and Lightroom, and what those two read back.
                "Interfaces to Lightroom and PhotoLab": {
                    "PhotoLab Location…": MenuSpec(
                        callback=lambda: PhotoLabLocationDialog(main_window).exec(),
                        tooltip="Where DxO PhotoLab is installed",
                    ),
                    "PhotoLab Denoise…": MenuSpec(
                        callback=lambda: PhotoLabDenoiseDialog(main_window).exec(),
                        tooltip="Which denoise method to stamp into the .dop on a crop",
                    ),
                    "Sidecar Behaviour…": MenuSpec(
                        callback=lambda: SidecarSettingsDialog(main_window).exec(),
                        tooltip="What is written to the .dop and .xmp, and how to run PhotoLab",
                    ),
                },
                f"{MenuSpec.separator()}_settings_caches": None,
                "Manage Caches…": manage_caches,
                "Rescan master EXIF cache": rescan_master_cache,
            },
            **({"Developer Tools": developer_tools_menu} if debug_enabled() else {}),
            f"{MenuSpec.separator()}_file": None,
            "Quit": MenuSpec(
                callback=main_window.close,
                shortcut="Ctrl+Q"
            ),
        },
        # Every way of BROWSING a collection, in one place — the same two views
        # (taxonomy tree, flat all-species index) over each of the three scopes.
        # They used to be buried one per owning menu (Trips / Albums / Master),
        # which hid them behind the pipeline actions they sit beside.
        "Browser": {
            "Trip Browser…": MenuSpec(
                callback=lambda: main_window.open_trip_biota_browser(),
                title="Trip Browser",
                enabled=lambda: bool(_cfg("trip", "tripbiotadatabase")
                                     or _cfg("repository", "biotadatabase")),
                tooltip="Browse this trip by taxonomy (Kingdom → Species), read-only; "
                        "drill to a species to see its pictures",
                showsubdirectory=False),
            "Trip All Species…": MenuSpec(
                callback=lambda: main_window.open_trip_biota_browser(start_mode="allspecies"),
                title="All Species in the Trip",
                enabled=lambda: bool(_cfg("trip", "tripbiotadatabase")
                                     or _cfg("repository", "biotadatabase")),
                tooltip="Flat, searchable index of every species in this trip; "
                        "click one to see its pictures",
                showsubdirectory=False),
            f"{MenuSpec.separator()}_browse_album": None,
            "Album Browser…": MenuSpec(
                callback=lambda: main_window.open_album_biota_browser(),
                title="Album Browser",
                enabled=has_active_album,
                tooltip="Browse this album by taxonomy (Kingdom → Species), read-only; "
                        "a live date-range window on the master",
                showsubdirectory=False),
            "Album All Species…": MenuSpec(
                callback=lambda: main_window.open_album_biota_browser(start_mode="allspecies"),
                title="All Species in the Album",
                enabled=has_active_album,
                tooltip="Flat, searchable index of every species in this album; "
                        "click one to see its pictures",
                showsubdirectory=False),
            f"{MenuSpec.separator()}_browse_master": None,
            "Master Browser…": MenuSpec(
                callback=lambda: main_window.open_biota_browser(),
                title="Master Browser",
                enabled=has_master,
                tooltip="Browse the master by taxonomy (Kingdom → Species); "
                        "drill to a species to see its pictures",
                showsubdirectory=False),
            "Master All Species…": MenuSpec(
                callback=lambda: main_window.open_biota_browser(start_mode="allspecies"),
                title="All Species in the Master",
                enabled=has_master,
                tooltip="Flat, searchable index of every master species; "
                        "click one to see its pictures",
                showsubdirectory=False),
        },
        "Days": {
            "Register existing day…": MenuSpec(
                callback=lambda: days_process.register_existing_day(main_window),
                title="Register Existing Day…", showsubdirectory=False),
            "Copy day from card…": MenuSpec(
                callback=lambda: days_process.copy_day_from_card(main_window),
                title="Copy Day from Card…", showsubdirectory=False),
            f"{MenuSpec.separator()}_days0": None,
            "Review & rate pictures…": MenuSpec(
                callback=lambda: days_process.review_day(main_window),
                title="Review & Rate Pictures…", enabled=has_active_day, showsubdirectory=False),
            "Add to Master": MenuSpec(
                callback=lambda: days_process.add_day_to_master(main_window),
                title="Add to Master", enabled=has_active_day, showsubdirectory=False),
            f"{MenuSpec.separator()}_days1": None,
            "Remove from list": MenuSpec(
                callback=lambda: days_process.remove_day_from_list(main_window),
                title="Remove from List", enabled=has_active_day, showsubdirectory=False),
        },
        "Trips": {
            "Select or Update Trip…": trip_select,
            "Create Trip": trip_create,
            # Carrying a trip between machines is the other way to get one here:
            # prepare it on the big machine, import it on the laptop, export it
            # back when you come home.
            "Export Trip…": export_trip,
            "Import Trip…": import_trip,
            "Restore Work from Card…": restore_work,
            f"{MenuSpec.separator()}_setup0": None,
            "Read SD-cards": read_sdcard,
            "Empty a Card": empty_card,
            f"{MenuSpec.separator()}_setup": None,
            "Sync Sidecars": sync_sidecars,
            f"{MenuSpec.separator()}_pipeline": None,
            # The trip's whole publishing cycle. 1–3 run per day; 4–5 act on the
            # WHOLE trip and are re-run each publishing pass (make the blogs
            # clickable, (re)list the trip on the site) — this is how a trip is
            # worked on the laptop. Publish Day merges Freeze + Write Commit and
            # builds the website (Build Website retired; the individual Freeze /
            # Write Website Commit steps live under Advanced ▸ Legacy below).
            "Read & Summarize": read_summarize,
            "Write Blog": write_blog,
            "Publish Day": publish_day,
            "Make the Blogs Clickable": redo_trip_blogs,      # 4.
            "Manage Trip Publication…": manage_trips,          # 5.
            f"{MenuSpec.separator()}_2": None,
            # Review & edit this trip
            "Rating Editor": rating_editor,                    # shows "Trip Species Viewer"
            # Browsing this trip by taxonomy lives in the Browser menu.
            "Edit Species List": edit_trip_species,            # same list as under Master
            "Publish Species Names": merge_trip_species,       # also under To Master
            "Count Species": count_species,
            f"{MenuSpec.separator()}_advanced": None,
            # Advanced: rare maintenance + the individual pipeline steps for a
            # read-only CSV refresh / the freeze-only edge case.
            "Advanced": {
                "Manage Blogs": manage_blogs,
                "Reset Trip": empty_trip_data,
                "Recreate Trip": recreate_trip,
                f"{MenuSpec.separator()}_adv0": None,
                "Legacy": {
                    "Read Directory": read_dir,
                    "Write Summary": write_summary,
                    f"{MenuSpec.separator()}_adv1": None,
                    "Freeze Data": freeze_data,
                    "Write Website Commit": commit_website,
                },
            },
        },
        # The whole "into the master" phase (architecture lifecycle steps 4 & 7):
        # the real master merge, then winding the trip down once it lives in the
        # master + an album.
        "To Master": {
            "Trip to Master Dashboard…": trip_master_dashboard,
            f"{MenuSpec.separator()}_tm_dash": None,
            "Publish Species Names": merge_trip_species,
            "Merge Pictures into Master": add_trip_pictures,
            f"{MenuSpec.separator()}_tm_retire": None,
            "Retire Trip to Album": retire_trip_item,
            "Delete Local Files Backed Up in Master…": delete_trip_item,
            f"{MenuSpec.separator()}_tm_del": None,
            "Delete Trip…": delete_trip_repo_item,
        },
        # The master as a collection: edit the shared name list, add pictures
        # directly, view it, and keep it healthy. The trip→master merge lives in
        # the To Master menu; Publish Species Names is shared with it.
        "Master": {
            "Edit Species List": edit_master_species,
            f"{MenuSpec.separator()}_master_0": None,
            "Add Pictures to Master…": add_pictures_to_master_item,
            f"{MenuSpec.separator()}_master_1": None,
            "Species Viewer…": MenuSpec(
                callback=lambda: main_window.open_master_species_viewer(),
                title="Species Viewer",
                enabled=has_master,
                showsubdirectory=False),
            # Browsing the master by taxonomy / as a flat species index lives in
            # the Browser menu.
            "Species Map…": MenuSpec(
                callback=lambda: main_window.open_species_map(),
                title="Species Map",
                enabled=has_master,
                tooltip="Distribution map — pick 1–4 species and see their GPS points",
                showsubdirectory=False),
            "Species Without a Website Picture…": no_display_item,
            f"{MenuSpec.separator()}_master_maint": None,
            "Maintain Master Database": maintain_master_menu,
        },
        "Albums": {
            "Find Trips in the Master…": find_trips,
            "Create Album": virttrip_create,
            # Browsing this album by taxonomy lives in the Browser menu.
            f"{MenuSpec.separator()}_vt2": None,
            "Write Album Blogs": MenuSpec(
                callback=lambda: VirttripWriteBlogDialog(main_window).exec(),
                title="Write Album Blogs",
                enabled=has_active_album,
            ),
            "Make the Blogs Clickable": MenuSpec(
                callback=lambda: VirttripRedoBlogDialog(main_window).exec(),
                title="Make the Blogs Clickable",
                enabled=has_active_album,
                tooltip="Add species links & navigation",
            ),
            "Build Album Website": MenuSpec(
                callback=lambda: VirttripBuildWebsiteDialog(main_window).exec(),
                title="Build Album Website",
                enabled=has_active_album,
            ),
            "Manage Album Publication…": MenuSpec(
                callback=lambda: _manage_album_publication(),
                title="Manage Album Publication…",
                tooltip="List this album on the site's trip index, or remove it from there",
            ),
            f"{MenuSpec.separator()}_vt3": None,
            "Album Species Viewer": MenuSpec(
                callback=lambda: main_window.open_virttrip_rating_editor(),
                title="Album Species Viewer",
                showsubdirectory=False,
            ),
            f"{MenuSpec.separator()}_vt_sync": None,
            "Sync Placed GPS into the Index…": MenuSpec(
                callback=lambda: __import__(
                    "gui.place_sync", fromlist=["open_place_sync_for_active_album"]
                ).open_place_sync_for_active_album(main_window),
                title="Sync Placed GPS into the Index…",
                enabled=has_active_album,
                tooltip="For this album, copy the place name and movable flag a "
                        "hand-placed coordinate already carries in its sidecar into "
                        "the master index (fixes cards showing raw coordinates)",
                showsubdirectory=False),
            f"{MenuSpec.separator()}_vt5": None,
            "Export Album": export_virttrip,
            "Import Album": import_virttrip,
            f"{MenuSpec.separator()}_vt6": None,
            "Delete Album…": MenuSpec(
                callback=lambda: _delete_album(),
                title="Delete Album…",
                enabled=has_active_album,
                tooltip="Delete the active album (backed up first; the master is untouched)",
            ),
        },
        "Utilities": {
             # Emptying a card is not a trip action — the trip is exactly what is
             # not decided yet. These two live here, away from the trip pipeline:
             # read cards in, cull, then file each day under a trip.
             "Read card into unfiled…": read_sdcard_unfiled,
             "Unfiled pictures…": unfiled_inbox,
             f"{MenuSpec.separator()}_util_unfiled": None,
        #     "BirdingBreaks": None, # PROCESS BIRDING BREAK FILES
             "AviBase": convert_avibase,
             "PhotoMechanic": photo_mechanic,
             "Small Master Database": small_master,
             "Install Small Master": install_small_master,
             f"{MenuSpec.separator()}_util_rebuild": None,
             "Rebuild all Albums and the Master website…": MenuSpec(
                 callback=lambda: _rebuild_all_websites(),
                 title="Rebuild all Albums and the Master website…",
                 tooltip="Re-render and re-upload the album sites and/or the master, "
                         "so a fix to a page template or the rendering code reaches them all",
                 showsubdirectory=False),
        },
        "Help": {
            "Connection status…": MenuSpec(
                callback=lambda: __import__(
                    "gui.connection_status", fromlist=["ConnectionStatusDialog"]
                ).ConnectionStatusDialog(main_window).exec(),
            ),
            "About": MenuSpec(
                callback=lambda: AboutDialog(main_window).exec(),
            ),
        }
    }        
    