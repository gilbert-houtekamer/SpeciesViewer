import configparser

from PyQt6.QtWidgets import QMessageBox
from gui.menu import MenuSpec  # assuming class is defined elsewhere
from gui.trip_process import (ReadSDCardDialog, ReadDirDialog, ReadSummarizeDialog, TripCreationDialog,
        WritePicturesDialog, CreateBlogDialog, BuildWebsiteDialog, FreezeDialog, WriteCommitDialog,
        CountSpeciesDialog, RedoTripBlogsDialog, RedoTripRepoDialog, MergeTripDialog,
        AddTripToMasterDialog, MasterPipelineDialog, ConvertAvibaseDialog,
        SyncSidecarsDialog, EmptyTripDataDialog, SpeciesCollectionDialog,
        TestbedDialog, BlogAdminDialog, PhotoMechanicDialog,
        RescanMasterCacheDialog, SmallMasterDialog, InstallSmallMasterDialog, TripMasterDashboard)
from gui.virttrip_process import (VirtualTripCreationDialog, ExtractDaysDialog,
                                   VirttripWriteBlogDialog, VirttripBuildWebsiteDialog,
                                   VirttripAddToMasterDialog)
from utils.config import _config
import os
from settings.globalsettings import GlobalSettingsDialog
from settings.reposettings import RepoSettingsDialog
from settings.speciessettings import SpeciesSettingsDialog
from settings.dbsettings import DatabaseSettingsDialog
from settings.rawsettings import RawProcessingDialog
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
            'after_msg': "✅ Day extraction complete. Check metadata/review/ for results."
        }
    )
    extract_days.callback = lambda: ExtractDaysDialog(main_window, extract_days).exec()

    rating_editor = MenuSpec(
        callback=lambda: main_window.open_rating_editor(),
        title="Rating Editor",
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
        webbrowser.open(Path(index).as_uri())
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
        webbrowser.open(Path(merged).as_uri())
        QMessageBox.information(main_window, "Merged Status",
                               f"Merged overview for: {', '.join(machines)}\n{merged}")

    merged_status_console = MenuSpec(
        callback=_merged_status_console,
        title="Merged Status (all machines)…",
        showsubdirectory=False,
    )

    return {
        "File": {
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
            f"{MenuSpec.separator()}_master_1": None,
            "Add Trip Pictures to Master": add_trip_pictures,
            f"{MenuSpec.separator()}_master_2": None,
            "Trip Status Console…": trip_status_console,
            "Merged Status (all machines)…": merged_status_console,
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
            "Build Virtual Trip Website": MenuSpec(
                callback=lambda: VirttripBuildWebsiteDialog(main_window).exec(),
                title="Build Virtual Trip Website",
            ),
            f"{MenuSpec.separator()}_vt3": None,
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
             "Call Testbed for trip day": testbed,
        },
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
            f"{MenuSpec.separator()}_settings_caches": None,
            "Manage Caches…": manage_caches,
            "Rescan master EXIF cache": rescan_master_cache,
        },
        "Help": {
            "About": MenuSpec(
                callback=lambda: AboutDialog(main_window).exec(),
            ),
        }
    }        
    