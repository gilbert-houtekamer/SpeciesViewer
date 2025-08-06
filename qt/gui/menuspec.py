from gui.menu import MenuSpec  # assuming class is defined elsewhere
from gui.trip_process import ReadDirDialog, TripCreationDialog, WritePicturesDialog, CreateBlogDialog
from settings.globalsettings import GlobalSettingsDialog
from settings.reposettings import RepoSettingsDialog
from dialogs.setting import AboutDialog

import bin.sv1MakeReposity
import bin.sv2ReadDirExif
import bin.sv3WritePictures
import bin.sv4WriteBlog
import bin.sv5BuildWebsite
import bin.sv6Freeze
import bin.sv7WriteCommit

def build_menu_spec(main_window):
    config = main_window.config

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
    trip_create.callback = lambda: TripCreationDialog(main_window, config, trip_create).exec()

    read_dir = MenuSpec(
        callback=None,
        title="Read Directory",
        shortcut="Ctrl+D",
        batch_parms={
            'statusname': "ReadDirExif",
            'module_called': bin.sv2ReadDirExif.main,
            'before_msg': "Reading picture directory...",
            'after_msg': "✅ Directory scan complete."
        }
    )
    read_dir.callback = lambda: ReadDirDialog(main_window, config, read_dir).exec()

    write_summary = MenuSpec(
        callback=None,
        title="Write Summary",
        batch_parms={
            'statusname': "WritePictures",
            'module_called': bin.sv3WritePictures.main,
            'before_msg': "Reading meta data for all pictures ...",
            'after_msg': "✅ Writing Summary data. Review messages above."
        }
    )
    write_summary.callback = lambda: WritePicturesDialog(main_window, config, write_summary).exec()

    write_blog = MenuSpec(
        callback=None,
        title="Write Blog",
        batch_parms={
            'statusname': "WriteBlog",
            'module_called': bin.sv4WriteBlog.main,
            'before_msg': "Create a blog entry ...",
            'after_msg': "✅ Blog successfully created. Review messages above."
        }
    )
    write_blog.callback = lambda: CreateBlogDialog(main_window, config, write_blog).exec()

    return {
        "File": {
            "Quit": MenuSpec(
                callback=main_window.close,
                shortcut="Ctrl+Q"
            ),
        },
        "Trip": {
            "Create Trip": trip_create,
            "Read Directory": read_dir,
            "Write Summary": write_summary,
            "Write Blog": write_blog
        },        
        "Settings": {
            "Global Settings": MenuSpec(
                callback=lambda: GlobalSettingsDialog(main_window, config).exec(),
                shortcut="Ctrl+G",
            ),
            "Repository Settings": MenuSpec(
                callback=lambda: RepoSettingsDialog(main_window, config).exec(),
            ),
        },
        "Help": {
            "About": MenuSpec(
                callback=lambda: AboutDialog(main_window).exec(),
            ),
        }
    }        
    

# def build_menu_spec(main_window):
#     config = main_window.config
 
#     return {
#         "File": {
#             "Quit": MenuSpec(
#                 callback=main_window.close,
#                 shortcut="Ctrl+Q"
#             ),
#         },
#         "Trip": {
#             "Create Trip": MenuSpec(
#                 callback=lambda: TripCreationDialog(config, main_window).exec(),
#                 # shortcut="Ctrl+N",
#                 title="Start New Trip",
#                 batch_parms = {
#                 'statusname': "MakeRepository",
#                 'module_called': bin.sv1MakeReposity.main,
#                 'before_msg': "Creating trip data structures...",
#                 'after_msg': "✅ Trip creation complete. Please review the log messages."
#                 }
#             ),
#             "Read Directory": MenuSpec(
#                 callback=lambda: ReadDirDialog(main_window, config).exec(),
#                 title="Read Directory",
#                 shortcut="Ctrl+D",
#                 # enabled=lambda: config.repo_available,
#                 batch_parms = {
#                 'statusname': "ReadDirExif",
#                 'module_called': bin.sv2ReadDirExif.main, 
#                 'before_msg': "Reading picture directory...",
#                 'after_msg': "✅ Directory scan complete."
#                 }                          
#             ),
#             "Write Summary": MenuSpec(
#                 callback=lambda: WritePicturesDialog(main_window, config).exec(),
#                 title="Write Summary",
#                 # shortcut="Ctrl+D",
#                 # enabled=lambda: config.repo_available,
#                 batch_parms = {
#                 'statusname': "WritePictures",
#                 'module_called': bin.sv3WritePictures.main,
#                 'before_msg': "Reading meta data for all pictures ...",
#                 'after_msg': "✅ Writing Summary data. Review messages above."
#                 }                
#             ),
#             "Write Blog": MenuSpec(
#                 callback=lambda: CreateBlogDialog(main_window, config).exec(),
#                 title="Write Blog",
#                 # shortcut="Ctrl+D",
#                 # enabled=lambda: config.repo_available,
#                 batch_parms = {
#                 'statusname': "WriteBlog",
#                 'module_called': bin.sv4WriteBlog.main,             
#                 'before_msg':  "Create a blog entry ...",
#                 'after_msg': "✅ Blog successfully created. Review messages above.",
#                 }                
#             ),                                   
#         },
#         "Settings": {
#             "Global Settings": MenuSpec(
#                 callback=lambda: GlobalSettingsDialog(main_window, config).exec(),
#                 shortcut="Ctrl+G",
#             ),
#             "Repository Settings": MenuSpec(
#                 callback=lambda: RepoSettingsDialog(main_window, config).exec(),
#             ),
#         },
#         "Help": {
#             "About": MenuSpec(
#                 callback=lambda: AboutDialog(main_window).exec(),
#             ),
#         }
#     }