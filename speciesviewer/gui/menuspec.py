import configparser

from PyQt6.QtWidgets import QMessageBox
from gui.menu import MenuSpec  # assuming class is defined elsewhere
from gui.trip_process import (ReadSDCardDialog, ReadDirDialog, ReadSummarizeDialog, TripCreationDialog,
        WritePicturesDialog, CreateBlogDialog, BuildWebsiteDialog, FreezeDialog, WriteCommitDialog,
        CountSpeciesDialog, RedoTripBlogsDialog, RedoTripRepoDialog, MergeTripDialog,
        AddTripToMasterDialog, MasterPipelineDialog, ConvertAvibaseDialog,
        SyncSidecarsDialog, EmptyTripDataDialog, SpeciesCollectionDialog,
        TestbedDialog, BlogAdminDialog, ManageTripsDialog, PhotoMechanicDialog,
        RescanMasterCacheDialog, SmallMasterDialog, InstallSmallMasterDialog, TripMasterDashboard)
from gui.virttrip_process import (VirtualTripCreationDialog, ExtractDaysDialog,
                                   VirttripWriteBlogDialog, VirttripRedoBlogDialog,
                                   VirttripBuildWebsiteDialog,
                                   VirttripAddToMasterDialog, retire_trip_to_virttrip)
from utils.config import _config, debug_enabled
import os
from settings.globalsettings import GlobalSettingsDialog
from settings.reposettings import RepoSettingsDialog
from settings.speciessettings import SpeciesSettingsDialog
from settings.dbsettings import DatabaseSettingsDialog
from settings.rawsettings import RawProcessingDialog
from settings.locationsettings import LocationSettingsDialog
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
import batch.svv2ExtractDays
import batch.svv3WriteBlog
import batch.svv4BuildWebsite
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
        title="Start New Trip",
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
        title="Sync Sidecars",
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
        title="Read & Summarize",
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
        title="Write Blog",
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
        title="Build Website",
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
        title="Recreate Trip Blogs",
        showsubdirectory=False,
        batch_parms={
            'statusname': "",
            'module_called': batch.svr9RedoBlog.main,
            'before_msg': "Recreating blogs that have JPG pictures ...",
            'after_msg': "✅ Valid Blogs recreated."
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
        title="Merge trip species to master",
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
        title="Add trip website to master",
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
        title="Add Trip Pictures to Master",
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
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
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
        title="Create Virtual Trip",
        batch_parms={
            'statusname': "",
            'module_called': batch.svv1MakeVirtualRepository.main,
            'before_msg': "Creating virtual trip directory structure...",
            'after_msg': "✅ Virtual trip created. Fill in virttrip.ini details and select a date range."
        }
    )
    virttrip_create.callback = lambda: VirtualTripCreationDialog(main_window, virttrip_create).exec()

    extract_days = MenuSpec(
        callback=None,
        title="Extract Days from Master",
        batch_parms={
            'statusname': "",
            'module_called': batch.svv2ExtractDays.main,
            'before_msg': "Extracting per-day CSVs from master...",
            'after_msg': "✅ Day extraction complete. Check metadata/review/ for results.",
            # Without this the batch writes svconfig with [repository] BiotaDatabase
            # taken from the active REGULAR trip, so svv2ExtractDays reads the wrong
            # root (e.g. trips/<active>) and finds no virttrip.ini. virttrip_mode
            # points BiotaDatabase at [virttrip] virttripbiotadatabase instead.
            'virttrip_mode': True,
        }
    )
    extract_days.callback = lambda: ExtractDaysDialog(main_window, extract_days).exec()

    rating_editor = MenuSpec(
        callback=lambda: main_window.open_rating_editor(),
        title="Rating Editor",
        showsubdirectory=False,
    )

    # ── Trip retirement: from the ACTIVE trip, create the successor virtual trip
    # (same name/range/text), build it (svv1), extract its master days (svv2),
    # and migrate the day blogs — one action. The svr7 delete stays separate.
    retire_trip_item = MenuSpec(
        callback=lambda: retire_trip_to_virttrip(main_window),
        title="Retire Trip to Virtual Trip",
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

    # Audit the master admin for ghost records — data_files.csv rows whose file
    # is not at the recorded path (dangling = nowhere; elsewhere = wrong path).
    def _audit_admin():
        import os
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt
        import engine.admin_audit as admin_audit
        master = _config.get("master", "masterbiotadatabase", fallback="").replace('"', "").replace("'", "")
        if not master:
            QMessageBox.warning(main_window, "Audit Admin", "No master is configured.")
            return
        out = os.path.join(master, "admin", "status", "ghost_files.csv")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            r = admin_audit.find_ghost_files(master, out_csv=out)
        finally:
            QApplication.restoreOverrideCursor()
        if not r.get("ok"):
            QMessageBox.warning(main_window, "Audit Admin", r.get("error", "Failed."))
            return
        pct = (r["ghosts"] / r["total"] * 100) if r["total"] else 0
        QMessageBox.information(
            main_window, "Audit Admin — ghost records",
            f"Scanned {r['total']} admin file-records.\n\n"
            f"Ghost records: {r['ghosts']} ({pct:.1f}%)\n"
            f"  • {r['dangling']} dangling (file nowhere in the master)\n"
            f"  • {r['elsewhere']} wrong-path (file exists elsewhere)\n\n"
            f"Details written to:\n{out}")

    audit_admin_item = MenuSpec(
        callback=_audit_admin,
        title="Audit Admin (find ghost records)…",
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

    # Dry-run "what would a clean rebuild change?" — compares the current admin
    # against the actual tree (paths + folder species). Non-destructive; writes
    # rebuild_*.csv reports for review before any real rebuild.
    def _rebuild_admin_dryrun():
        import os
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt
        import engine.admin_rebuild as admin_rebuild
        master = _config.get("master", "masterbiotadatabase", fallback="").replace('"', "").replace("'", "")
        if not master:
            QMessageBox.warning(main_window, "Rebuild Admin", "No master is configured.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            r = admin_rebuild.plan_rebuild(master)
        finally:
            QApplication.restoreOverrideCursor()
        if not r.get("ok"):
            QMessageBox.warning(main_window, "Rebuild Admin", r.get("error", "Failed."))
            return
        bd = r.get("add_breakdown", {})
        add_detail = (" (" + ", ".join(f"{v} {k}" for k, v in sorted(bd.items())) + ")") if bd else ""
        QMessageBox.information(
            main_window, "Rebuild Admin — dry run (what would change)",
            f"A clean rebuild from the current tree would:\n\n"
            f"  • REMOVE {r['remove']} dead lines (file nowhere)\n"
            f"  • REPOINT {r['repoint']} stale paths (file exists elsewhere)\n"
            f"  • CHANGE species on {r['species_change']} pictures (folder ≠ admin)\n"
            f"  • ADD {r['add']} files not in the admin{add_detail}\n"
            f"        — report only, not applied: 'attach' belongs to an existing\n"
            f"        picture, 'junk?' has no frame number, 'new?' looks real.\n\n"
            f"Heal Admin applies remove + repoint + species. Nothing was changed.\n"
            f"Details written to:\n{r['out_dir']}\\rebuild_*.csv")

    rebuild_admin_item = MenuSpec(
        callback=_rebuild_admin_dryrun,
        title="Rebuild Admin (dry-run compare)…",
        showsubdirectory=False,
    )

    # Apply the heal: remove dead rows + repoint stale paths in the master admin.
    # Re-runs the dry-run first (fresh), confirms, backs up both CSVs, then writes.
    def _heal_admin():
        from PyQt6.QtWidgets import QApplication, QMessageBox
        from PyQt6.QtCore import Qt
        import engine.admin_rebuild as admin_rebuild
        master = _config.get("master", "masterbiotadatabase", fallback="").replace('"', "").replace("'", "")
        if not master:
            QMessageBox.warning(main_window, "Heal Admin", "No master is configured.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            plan = admin_rebuild.plan_rebuild(master)
        finally:
            QApplication.restoreOverrideCursor()
        if not plan.get("ok"):
            QMessageBox.warning(main_window, "Heal Admin", plan.get("error", "Failed."))
            return
        if plan["remove"] == 0 and plan["repoint"] == 0 and plan["species_change"] == 0:
            QMessageBox.information(main_window, "Heal Admin", "Nothing to heal.")
            return
        msg = (f"Apply to the master admin (data_files.csv / data_pictures.csv)?\n\n"
               f"  • REMOVE {plan['remove']} dead file rows\n"
               f"  • REPOINT {plan['repoint']} stale paths → real biota location\n"
               f"  • RECLASSIFY {plan['species_change']} pictures to their folder species\n"
               f"  • DROP phantom pictures whose files are all gone\n\n"
               f"(The {plan['add']} adds are NOT applied — mostly DxO renders + junk.)\n\n"
               f"Both CSVs are backed up first to admin/status/backup/.\n"
               f"This rewrites the master admin. Continue?")
        if QMessageBox.question(main_window, "Heal Admin — apply (destructive)", msg,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            r = admin_rebuild.execute_heal(master)
        except Exception as e:
            r = {"ok": False, "error": f"{type(e).__name__}: {e} (CSV open in Excel / Dropbox lock?)"}
        finally:
            QApplication.restoreOverrideCursor()
        if not r.get("ok"):
            QMessageBox.warning(main_window, "Heal Admin", r.get("error", "Failed."))
            return
        QMessageBox.information(
            main_window, "Heal Admin — done",
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
        title="Heal Admin (apply fixes)…",
        showsubdirectory=False,
    )

    # Move orphan sidecars (XMP/DOP whose image was deleted) to a recoverable
    # trash; their now-dangling admin rows are swept by the next Heal Admin run.
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
                f"Run Heal Admin afterward to clear their admin rows.",
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
            f"Run Heal Admin to clear their (now-dangling) admin rows.")

    clean_orphans_item = MenuSpec(
        callback=_clean_orphan_sidecars,
        title="Clean Orphan Sidecars (move to trash)…",
        showsubdirectory=False,
    )

    # File ▸ Admin. Heal Admin is a real fix and always shown; the diagnostics
    # are gated behind the "Enable debug functionality" flag (Global Settings).
    # Read at build time — toggling the flag takes effect on restart.
    admin_menu = {"Heal Admin (apply fixes)…": heal_admin_item}
    if debug_enabled():
        admin_menu = {
            "Trip Status Console…": trip_status_console,
            "Merged Status (all machines)…": merged_status_console,
            f"{MenuSpec.separator()}_admin_0": None,
            "Audit Admin (find ghost records)…": audit_admin_item,
            "Audit Species Counters (dry-run)…": audit_species_item,
            "Rebuild Admin (dry-run compare)…": rebuild_admin_item,
            "Heal Admin (apply fixes)…": heal_admin_item,
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
        "Trip": {
            "Read SD-cards": read_sdcard,
            f"{MenuSpec.separator()}_sd": None,
            "Create Trip": trip_create,
            f"{MenuSpec.separator()}_1": None,
            "Sync Sidecars": sync_sidecars,
            "Read & Summarize": read_summarize,
            "Write Blog": write_blog,
            "Build Website": build_website,
            "Freeze": freeze_data,
            "Write Commit": commit_website,
            f"{MenuSpec.separator()}_advanced": None,
            # Demoted: Read & Summarize runs both in one step. These remain for a
            # read-only CSV refresh (Rating Editor / translation panel) or debugging.
            "Read Directory": read_dir,
            "Write Summary": write_summary,
            f"{MenuSpec.separator()}_2": None,
            "Edit Trip Species List": edit_trip_species,
            "Count Species": count_species,
            "Manage Blogs": manage_blogs,
            "Redo Blogs": redo_trip_blogs,
            f"{MenuSpec.separator()}_3": None,
            "Empty Trip Data": empty_trip_data,
            "Recreate Trip": recreate_trip,
            f"{MenuSpec.separator()}_retire": None,
            "Retire Trip to Virtual Trip": retire_trip_item,
            "Delete Local Files Backed Up in Master…": delete_trip_item,
            f"{MenuSpec.separator()}_4": None,
            "Rating Editor": rating_editor,
        },
        "Master": {
            "Trip to Master Dashboard…": trip_master_dashboard,
            f"{MenuSpec.separator()}_master_dash": None,
            "Edit Species List": edit_master_species,
            f"{MenuSpec.separator()}_master_0": None,
            "Merge Species": merge_trip_species,
            "Add trip website to master": add_trip_to_master,
            "Manage Trip Publication…": manage_trips,
            f"{MenuSpec.separator()}_master_1": None,
            "Add Trip Pictures to Master": add_trip_pictures,
            "Add Pictures to Master…": add_pictures_to_master_item,
            f"{MenuSpec.separator()}_master_2": None,
            "Species Without a Website Picture…": no_display_item,
        },
        "Virtual Trips": {
            "Create Virtual Trip": virttrip_create,
            f"{MenuSpec.separator()}_vt1": None,
            "Extract Days from Master": extract_days,
            f"{MenuSpec.separator()}_vt2": None,
            "Write Virtual Trip Blogs": MenuSpec(
                callback=lambda: VirttripWriteBlogDialog(main_window).exec(),
                title="Write Virtual Trip Blogs",
            ),
            "Recreate Virtual Trip Blogs": MenuSpec(
                callback=lambda: VirttripRedoBlogDialog(main_window).exec(),
                title="Recreate Virtual Trip Blogs",
            ),
            "Build Virtual Trip Website": MenuSpec(
                callback=lambda: VirttripBuildWebsiteDialog(main_window).exec(),
                title="Build Virtual Trip Website",
            ),
            f"{MenuSpec.separator()}_vt3": None,
            "Rating Editor": MenuSpec(
                callback=lambda: main_window.open_virttrip_rating_editor(),
                title="Rating Editor",
                showsubdirectory=False,
            ),
            f"{MenuSpec.separator()}_vt4": None,
            "Add Virtual Trip to Master": MenuSpec(
                callback=lambda: VirttripAddToMasterDialog(main_window).exec(),
                title="Add Virtual Trip to Master",
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
            "About": MenuSpec(
                callback=lambda: AboutDialog(main_window).exec(),
            ),
        }
    }        
    