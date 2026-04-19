import configparser

from gui.menu import MenuSpec  # assuming class is defined elsewhere
from gui.trip_process import (ReadDirDialog, TripCreationDialog, WritePicturesDialog,
        CreateBlogDialog, BuildWebsiteDialog, FreezeDialog, WriteCommitDialog,
        CountSpeciesDialog, RedoTripBlogsDialog, RedoTripRepoDialog, MergeTripDialog,
        ConvertAvibaseDialog, SyncSidecarsDialog)
from settings.globalsettings import GlobalSettingsDialog
from settings.reposettings import RepoSettingsDialog
from settings.speciessettings import SpeciesSettingsDialog
from dialogs.setting import AboutDialog
from utils.batch_config import generate_svconfig_ini
from sharedlib.statemachine import StateMachine

import bin.sv1MakeReposity
import bin.sv2ReadDirExif
import bin.sv3WritePictures
import bin.sv4WriteBlog
import bin.sv5BuildWebsite
import bin.sv6Freeze
import bin.sv7WriteCommit
import bin.svr2CountSpecies
import bin.svr5MergeSpeciesCollections
import bin.svr8RecreateTripRepository
import bin.svr9RedoBlog
import bin.svu2avibase
import bin.svu5SyncSidecars


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

    trip_create = MenuSpec(
        callback=None,
        title="Start New Trip",
        batch_parms={
            'statusname': "MakeRepository",
            'module_called': bin.sv1MakeReposity.main,
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
            'module_called': bin.svu5SyncSidecars.main,
            'before_msg': "Synchronizing DOP and XMP sidecars...",
            'after_msg': "✅ Sidecar sync complete."
        }
    )
    sync_sidecars.callback = lambda: SyncSidecarsDialog(main_window, sync_sidecars).exec()

    read_dir = MenuSpec(
        callback=None,
        title="Read Directory",
        shortcut="Ctrl+D",
        enabled=make_enabled_checker("ReadDirExif"),
        tooltip=make_tooltip("ReadDirExif"),                
        batch_parms={
            'statusname': "ReadDirExif",
            'module_called': bin.sv2ReadDirExif.main,
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
            'module_called': bin.sv3WritePictures.main,
            'before_msg': "Reading meta data for all pictures ...",
            'after_msg': "✅ Writing Summary data."
        }
    )
    write_summary.callback = lambda: WritePicturesDialog(main_window, write_summary).exec()

    write_blog = MenuSpec(
        callback=None,
        title="Write Blog",
        enabled=make_enabled_checker("WriteBlog"),
        tooltip=make_tooltip("WriteBlog"),        
        batch_parms={
            'statusname': "WriteBlog",
            'module_called': bin.sv4WriteBlog.main,
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
            'module_called': bin.sv5BuildWebsite.main,
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
            'module_called': bin.sv6Freeze.main,
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
            'module_called': bin.sv7WriteCommit.main,
            'before_msg': "Update the trip website ...",
            'after_msg': "✅ Please review updated Website."
        }
    )
    commit_website.callback = lambda: WriteCommitDialog(main_window, commit_website).exec()    

    count_species = MenuSpec(
        callback=None,
        title="Count Species",
        showsubdirectory=False,        
        enabled=make_enabled_checker("CountSpecies"),
        tooltip=make_tooltip("CountSpecies"),        
        batch_parms={
            'statusname': "",
            'module_called': bin.svr2CountSpecies.main,
            'before_msg': "Analyzing species information ...",
            'after_msg': "✅ Species report will be shown."
        }
    )
    count_species.callback = lambda: CountSpeciesDialog(main_window, count_species).exec()    

    redo_trip_blogs = MenuSpec(
        callback=None,
        title="Recreate Trip Blogs",
        showsubdirectory=False,        
        enabled=make_enabled_checker("RedoTripBlogs"),
        tooltip=make_tooltip("RedoTripBlogs"),        
        batch_parms={
            'statusname': "",
            'module_called': bin.svr9RedoBlog.main,
            'before_msg': "Recreating blogs that have JPG pictures ...",
            'after_msg': "✅ Valid Blogs recreated."
        }
    )
    redo_trip_blogs.callback = lambda: RedoTripBlogsDialog(main_window, redo_trip_blogs).exec()

    recreate_trip = MenuSpec(
        callback=None,
        title="Recreate Trip Repository",
        showsubdirectory=False,        
        enabled=make_enabled_checker("RedoTripRepository"),
        tooltip=make_tooltip("RedoTripRepository"),        
        batch_parms={
            'statusname': "",
            'module_called': bin.svr8RecreateTripRepository.main,
            'before_msg': "Delete existing repository and rebuild it ...",
            'after_msg': "✅ Trip recreated."
        }
    )
    recreate_trip.callback = lambda: RedoTripRepoDialog(main_window, recreate_trip).exec()    

    # Master updates
    merge_trip_species = MenuSpec(
        callback=None,
        title="Merge new species to master",
        showsubdirectory=False,
        enabled=make_enabled_checker("MergeTripSpecies"),
        tooltip=make_tooltip("MergeTripSpecies"),        
        batch_parms={
            'statusname': "",
            'module_called': bin.svr5MergeSpeciesCollections.main,
            'before_msg': "List and merge species lists from master and trip ...",
            'after_msg': "✅ Review merge results, and decide to copy to master."
        }
    )
    merge_trip_species.callback = lambda: MergeTripDialog(main_window, merge_trip_species).exec() 

    # utilities
        # Master updates
    convert_avibase = MenuSpec(
        callback=None,
        title="Create an Excel to keep track of birds seen.",
        showsubdirectory=False,
        enabled=make_enabled_checker("ConvertAvibase"),
        tooltip=make_tooltip("ConvertAvibase"),        
        batch_parms={
            'statusname': "",
            'module_called': bin.svu2avibase.main,
            'before_msg': "Reading a XLS CSV file read from a copy.",
            'after_msg': "✅ XLSX checklist created."
        }
    )
    convert_avibase.callback = lambda: ConvertAvibaseDialog(main_window, convert_avibase).exec() 

    return {
        "File": {
            "Quit": MenuSpec(
                callback=main_window.close,
                shortcut="Ctrl+Q"
            ),
        },
        "Trip": {
            "Create Trip": trip_create,
            f"{MenuSpec.separator()}_1": None,
            "Sync Sidecars": sync_sidecars,
            "Read Directory": read_dir,
            "Write Summary": write_summary,
            "Write Blog": write_blog,
            "Build Website": build_website,
            "Freeze": freeze_data,
            "Write Commit": commit_website,
            f"{MenuSpec.separator()}_2": None,
            "Count Species": count_species,
            "Redo Blogs": redo_trip_blogs,
            f"{MenuSpec.separator()}_3": None,    
            "Recreate Trip": recreate_trip,     
        },
        "Master": {
            "Merge Species": merge_trip_species,
            # "Add trip": None, # Add pointer to trip from main website
            # add trip data to masterdatabase and website
        },
        "Utilities": {
        #     "BirdingBreaks": None, # PROCESS BIRDING BREAK FILES
             "AviBase": convert_avibase,
        },
        "Settings": {
            "Global Settings": MenuSpec(
                callback=lambda: GlobalSettingsDialog(main_window, config).exec(),
                shortcut="Ctrl+G",
            ),
            "Repository Settings": MenuSpec(
                callback=lambda: RepoSettingsDialog(main_window, config).exec(),
            ),
            "Species Settings": MenuSpec(
                callback=lambda: SpeciesSettingsDialog(main_window, config).exec(),
            ),            
        },
        "Help": {
            "About": MenuSpec(
                callback=lambda: AboutDialog(main_window).exec(),
            ),
        }
    }        
    