import configparser
import logging

logger = logging.getLogger("speciesviewer")

from PyQt6.QtWidgets import QMessageBox
from gui.menu import MenuSpec  # assuming class is defined elsewhere
from gui.trip_process import (ReadSDCardDialog, ReadDirDialog, ReadSummarizeDialog, TripCreationDialog,
        PublishDayDialog,
        WritePicturesDialog, CreateBlogDialog, BuildWebsiteDialog, FreezeDialog, WriteCommitDialog,
        CountSpeciesDialog, RedoTripBlogsDialog, RedoTripRepoDialog, MergeTripDialog,
        AddTripToMasterDialog, MasterPipelineDialog, ConvertAvibaseDialog,
        SyncSidecarsDialog, EmptyTripDataDialog, SpeciesCollectionDialog,
        TestbedDialog, BlogAdminDialog, ManageTripsDialog, PhotoMechanicDialog,
        RescanMasterCacheDialog, SmallMasterDialog, InstallSmallMasterDialog, TripMasterDashboard)
from gui.virttrip_process import (VirtualTripCreationDialog,
                                   VirttripWriteBlogDialog, VirttripRedoBlogDialog,
                                   VirttripBuildWebsiteDialog,
                                   VirttripAddToMasterDialog, retire_trip_to_virttrip,
                                   ExportVirtualTripDialog, ImportVirtualTripDialog,
                                   require_full_master)
from utils.config import _config, debug_enabled
import gui.days_process as days_process
import os
from settings.globalsettings import GlobalSettingsDialog
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
import batch.sv5BuildWebsite
import batch.sv6Freeze
import batch.sv7WriteCommit
import batch.svr2CountSpecies
import batch.svr4UpdateTrips
import batch.svr5MergeSpeciesCollections
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


def build_menu_spec(main_window):
    # Load config and statemachine
    generate_svconfig_ini()
    module_config = configparser.ConfigParser()
    module_config.read("svconfig.ini")

    sm = StateMachine(
        module_config.get("repository", "BiotaDatabase"),
        module_config.get("project", "PictureDirectory"),
        readState=True
    )

    def make_enabled_checker(statusname):
        return lambda: sm.stateOKtorun(statusname)

    def make_tooltip(statusname):
        try:
            return sm.explainAbort(statusname, raiseSV=False)
        except:
            return None    

    # ── Read SD-cards: the front-stage ingest (M0). Copies pictures from a
    # folder (e.g. a mounted card) into the project pictures dir. Runs the copy
    # directly on a worker thread — no statemachine, no svconfig plumbing —
    # because it precedes any day/trip structure.
    read_sdcard = MenuSpec(
        callback=None,
        title="Read SD-cards",
        showsubdirectory=False,
    )
    read_sdcard.callback = lambda: ReadSDCardDialog(main_window, read_sdcard).exec()

    trip_create = MenuSpec(
        callback=None,
        title="Create or Select Trip",
        batch_parms={
            'statusname': "MakeRepository",
            'module_called': batch.sv1MakeReposity.main,
            'before_msg': "Creating trip data structures...",
            'after_msg': "✅ Trip creation complete. Please review the log messages."
        }
    )
    trip_create.callback = lambda: TripCreationDialog(main_window, trip_create).exec()

    sync_sidecars = MenuSpec(
        callback=None,
        title="Sync Lightroom & PhotoLab Metadata",
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

    build_website = MenuSpec(
        callback=None,
        title="3. Build Website",
        enabled=make_enabled_checker("BuildWebsite"),
        tooltip=make_tooltip("BuildWebsite"),        
        batch_parms={
            'statusname': "BuildWebsite",
            'module_called': batch.sv5BuildWebsite.main,
            'before_msg': "Build updates for later publication ...",
            'after_msg': "✅ Website updates prepared."
        }
    )
    build_website.callback = lambda: BuildWebsiteDialog(main_window, build_website).exec()    
    
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
        batch.sv6Freeze.main(args, _config)
        batch.sv7WriteCommit.main(args, _config)

    publish_day = MenuSpec(
        callback=None,
        title="4. Publish Day",
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

    # Same SpeciesCollectionDialog the Write Summary flow exposes,
    # pointed at <tripbiotadatabase>/metadata/active/speciescollection.csv.
    def _edit_trip_species():
        trip_db = _config.get("trip", "tripbiotadatabase", fallback="")
        if not trip_db:
            return
        csv_path = os.path.join(trip_db, "metadata", "active", "speciescollection.csv")
        dlg = SpeciesCollectionDialog(main_window, csv_path)
        dlg.exec()
        # A trip-wide name edit → offer to re-summarize all days (see
        # MainWindow.rerun_summary_all_days). Master species edits are NOT hooked
        # here — that's a separate pipeline.
        if getattr(dlg, "_saved_changes", False):
            main_window.rerun_summary_all_days("Species names were changed.")

    edit_trip_species = MenuSpec(
        callback=_edit_trip_species,
        title="Edit Trip Species List",
        showsubdirectory=False,
    )

    count_species = MenuSpec(
        callback=None,
        title="Count Species",
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svr2CountSpecies.main,
            'before_msg': "Analyzing species information ...",
            'after_msg': "✅ Species report will be shown."
        }
    )
    count_species.callback = lambda: CountSpeciesDialog(main_window, count_species).exec()

    manage_blogs = MenuSpec(
        callback=lambda: BlogAdminDialog(main_window).exec(),
        title="Manage Blogs",
        showsubdirectory=False,
    )

    redo_trip_blogs = MenuSpec(
        callback=None,
        title="Make the Blogs Clickable",
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
        title="Empty Trip Data",
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svu6EmptyTripData.main,
            'before_msg': "Clearing trip data ...",
            'after_msg': "✅ Trip data cleared."
        }
    )
    empty_trip_data.callback = lambda: EmptyTripDataDialog(main_window, empty_trip_data).exec()

    recreate_trip = MenuSpec(
        callback=None,
        title="Recreate Trip Repository",
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
        title="1. Publish Species Names",
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

    add_trip_to_master = MenuSpec(
        callback=None,
        title="2. List Trip on the Site",
        tooltip="Add the trip's website to the master trip index",
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svr4UpdateTrips.main,
            'before_msg': "Updating master trip admin and tripindex pages, then uploading ...",
            'after_msg': "✅ Master trip admin updated and uploaded."
        }
    )
    add_trip_to_master.callback = lambda: AddTripToMasterDialog(main_window, add_trip_to_master).exec()

    manage_trips = MenuSpec(
        callback=lambda: ManageTripsDialog(main_window).exec(),
        title="Manage Trip Publication",
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
    master_pipeline_steps = [
        ("1. Read & Summarize",                master_ingest_spec),
        ("2. Build Master Website & Freeze",   master_build_spec),
        ("3. Commit to Master",                master_commit_spec),
    ]
    add_trip_pictures = MenuSpec(
        callback=None,
        title="3. Merge Pictures into Master",
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
        showsubdirectory=False,
    )

    trip_master_dashboard = MenuSpec(
        callback=lambda: TripMasterDashboard(
            main_window,
            {
                'redo_blogs':          redo_trip_blogs,
                'merge_species':       merge_trip_species,
                'add_website':         add_trip_to_master,
                'add_pictures_steps':  master_pipeline_steps,
            }
        ).exec(),
        title="Trip to Master Dashboard",
        showsubdirectory=False,
    )

    # Edit the master speciescollection.csv directly — same dialog the
    # Write Summary trip flow exposes, just pointed at the master path.
    def _edit_master_species():
        master_db = _config.get("master", "masterbiotadatabase", fallback="")
        if not master_db:
            return
        csv_path = os.path.join(master_db, "metadata", "active", "speciescollection.csv")
        SpeciesCollectionDialog(main_window, csv_path).exec()

    edit_master_species = MenuSpec(
        callback=_edit_master_species,
        title="Edit Species List",
        showsubdirectory=False,
    )

    def _manage_caches():
        from gui.cache_manager import ManageCachesDialog
        ManageCachesDialog(main_window).exec()

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

    export_virttrip = MenuSpec(
        callback=None,
        title="Export Album",
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
        callback=lambda: require_full_master(main_window)
                         and ImportVirtualTripDialog(main_window).exec(),
        title="Import Album",
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
        from gui.album_publish_dialog import AlbumPublishDialog
        AlbumPublishDialog(main_window).exec()

    find_trips = MenuSpec(
        callback=_find_trips,
        title="Find Trips in the Master…",
        tooltip="Report: date ranges that look like trips, to use when creating one",
        showsubdirectory=False,
    )

    rating_editor = MenuSpec(
        callback=lambda: main_window.open_rating_editor(),
        title="Trip Species Viewer",
        showsubdirectory=False,
    )

    # ── Trip retirement: from the ACTIVE trip, create the successor virtual trip
    # (same name/range/text), build it (svv1), extract its master days (svv2),
    # and migrate the day blogs — one action. The svr7 delete stays separate.
    retire_trip_item = MenuSpec(
        callback=lambda: require_full_master(main_window)
                         and retire_trip_to_virttrip(main_window),
        title="Retire Trip to Album",
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
        showsubdirectory=False,
    )

    def _species_no_display():
        import gui.master_no_display as nd
        nd.show(main_window, _config)

    no_display_item = MenuSpec(
        callback=_species_no_display,
        title="Species Without a Website Picture…",
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
        showsubdirectory=False,
    )

    # File ▸ Admin. The Refresh Master Database pair (Review → Apply) reconciles
    # the master CSVs with what is on disk; Apply is the only destructive action
    # and is always shown. The consoles/testbed diagnostics are gated behind the
    # "Enable debug functionality" flag (Global Settings). Read at build time —
    # toggling the flag takes effect on restart.
    admin_menu = {
        "Refresh Master Database — Review…": refresh_review_item,
        "Refresh Master Database — Apply…": heal_admin_item,
        f"{MenuSpec.separator()}_admin_maint": None,
        "Add Missing Place Names…": add_place_names_item,
        "Fill Picture GPS from Files…": consolidate_gps_item,
    }
    if debug_enabled():
        admin_menu = {
            "Trip Status Console…": trip_status_console,
            "Merged Status (all machines)…": merged_status_console,
            f"{MenuSpec.separator()}_admin_0": None,
            "Refresh Master Database — Review…": refresh_review_item,
            "Refresh Master Database — Apply…": heal_admin_item,
            f"{MenuSpec.separator()}_admin_maint": None,
            "Add Missing Place Names…": add_place_names_item,
            "Fill Picture GPS from Files…": consolidate_gps_item,
            "Audit Species Counters (dry-run)…": audit_species_item,
            "Clean Orphan Sidecars (move to trash)…": clean_orphans_item,
            f"{MenuSpec.separator()}_admin_1": None,
            "Call Testbed for trip day": testbed,
        }

    return {
        "File": {
            "Settings": {
                "Global Settings": MenuSpec(
                    callback=lambda: GlobalSettingsDialog(main_window).exec(),
                    shortcut="Ctrl+G",
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
            "Admin": admin_menu,
            f"{MenuSpec.separator()}_file": None,
            "Quit": MenuSpec(
                callback=main_window.close,
                shortcut="Ctrl+Q"
            ),
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
                title="Review & Rate Pictures…", showsubdirectory=False),
            "Add to Master": MenuSpec(
                callback=lambda: days_process.add_day_to_master(main_window),
                title="Add to Master", showsubdirectory=False),
            f"{MenuSpec.separator()}_days1": None,
            "Remove from list": MenuSpec(
                callback=lambda: days_process.remove_day_from_list(main_window),
                title="Remove from List", showsubdirectory=False),
        },
        "Trip": {
            "Create Trip": trip_create,
            f"{MenuSpec.separator()}_setup0": None,
            "Read SD-cards": read_sdcard,
            f"{MenuSpec.separator()}_setup": None,
            "Sync Sidecars": sync_sidecars,
            f"{MenuSpec.separator()}_pipeline": None,
            # Numbered day pipeline (titles carry the 1.–4. prefixes). Publish Day
            # merges Freeze + Write Commit (the individual steps live under
            # Advanced ▸ below).
            "Read & Summarize": read_summarize,
            "Write Blog": write_blog,
            "Build Website": build_website,
            "Publish Day": publish_day,
            f"{MenuSpec.separator()}_2": None,
            # Review & edit
            "Rating Editor": rating_editor,
            "Edit Trip Species List": edit_trip_species,
            "Count Species": count_species,
            "Redo Blogs": redo_trip_blogs,
            f"{MenuSpec.separator()}_maint": None,
            # Maintenance
            "Manage Blogs": manage_blogs,
            "Empty Trip Data": empty_trip_data,
            "Recreate Trip": recreate_trip,
            f"{MenuSpec.separator()}_retire": None,
            # Retire
            "Retire Trip to Album": retire_trip_item,
            "Delete Local Files Backed Up in Master…": delete_trip_item,
            f"{MenuSpec.separator()}_advanced": None,
            # Advanced: the individual pipeline steps, for a read-only CSV refresh
            # (Read Directory / Write Summary) or the freeze-only edge case.
            "Advanced (legacy)": {
                "Read Directory": read_dir,
                "Write Summary": write_summary,
                f"{MenuSpec.separator()}_adv1": None,
                "Freeze Data": freeze_data,
                "Write Website Commit": commit_website,
            },
        },
        "Master": {
            "Trip to Master Dashboard…": trip_master_dashboard,
            f"{MenuSpec.separator()}_master_dash": None,
            "Edit Species List": edit_master_species,
            f"{MenuSpec.separator()}_master_0": None,
            # Publish the active trip to the master, in order.
            "Publish Trip to Master": {
                "1. Publish species names": merge_trip_species,
                "2. List trip on the site": add_trip_to_master,
                "3. Merge pictures into master": add_trip_pictures,
            },
            "Manage Trip Publication…": manage_trips,
            "Add pictures (no trip)…": add_pictures_to_master_item,
            f"{MenuSpec.separator()}_master_2": None,
            "Species Viewer…": MenuSpec(
                callback=lambda: main_window.open_master_species_viewer(),
                title="Species Viewer",
                showsubdirectory=False),
            "Species Map…": MenuSpec(
                callback=lambda: main_window.open_species_map(),
                title="Species Map",
                tooltip="Distribution map — pick 1–4 species and see their GPS points",
                showsubdirectory=False),
            "Species Without a Website Picture…": no_display_item,
        },
        "Albums": {
            "Find Trips in the Master…": find_trips,
            "Create Album": virttrip_create,
            f"{MenuSpec.separator()}_vt2": None,
            "Write Album Blogs": MenuSpec(
                callback=lambda: VirttripWriteBlogDialog(main_window).exec(),
                title="Write Album Blogs",
            ),
            "Recreate Album Blogs": MenuSpec(
                callback=lambda: VirttripRedoBlogDialog(main_window).exec(),
                title="Make the Blogs Clickable",
                tooltip="Add species links & navigation",
            ),
            "Build Album Website": MenuSpec(
                callback=lambda: VirttripBuildWebsiteDialog(main_window).exec(),
                title="Build Album Website",
            ),
            f"{MenuSpec.separator()}_vt3": None,
            "Rating Editor": MenuSpec(
                callback=lambda: main_window.open_virttrip_rating_editor(),
                title="Rating Editor",
                showsubdirectory=False,
            ),
            f"{MenuSpec.separator()}_vt_sync": None,
            "Sync Placed GPS into the Index…": MenuSpec(
                callback=lambda: __import__(
                    "gui.place_sync", fromlist=["open_place_sync_for_active_album"]
                ).open_place_sync_for_active_album(main_window),
                title="Sync Placed GPS into the Index…",
                tooltip="For this album, copy the place name and movable flag a "
                        "hand-placed coordinate already carries in its sidecar into "
                        "the master index (fixes cards showing raw coordinates)",
                showsubdirectory=False),
            f"{MenuSpec.separator()}_vt4": None,
            # One place for both directions (list / remove), showing the current
            # state. Supersedes the advertise-only "Add Album to Master"; never a
            # Trip door — the album equivalent of Trip's publication actions.
            "Manage Album Publication…": MenuSpec(
                callback=lambda: _manage_album_publication(),
                title="Manage Album Publication…",
                tooltip="List this album on the site's trip index, or remove it from there",
            ),
            f"{MenuSpec.separator()}_vt5": None,
            "Export Album": export_virttrip,
            "Import Album": import_virttrip,
            f"{MenuSpec.separator()}_vt6": None,
            "Delete Album…": MenuSpec(
                callback=lambda: _delete_album(),
                title="Delete Album…",
                tooltip="Delete the active album (backed up first; the master is untouched)",
            ),
        },
        "Utilities": {
        #     "BirdingBreaks": None, # PROCESS BIRDING BREAK FILES
             "AviBase": convert_avibase,
             "PhotoMechanic": photo_mechanic,
             "Small Master Database": small_master,
             "Install Small Master": install_small_master,
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
    