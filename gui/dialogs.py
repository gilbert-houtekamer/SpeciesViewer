import flet as ft
import os
import asyncio
from utils.photo_sorter import process_source_dir
from utils.config import get_setting, set_setting
from gui.components import create_dialog, validate_integer, directory_field, DialogManager
from gui.log_widgets import LogTailWidget
import logging
import bin.sv1MakeReposity 
import bin.sv2ReadDirExif 
import bin.sv3WritePictures 
import bin.sv4WriteBlog 
import bin.sv5BuildWebsite 
import bin.sv6Freeze 
import bin.sv7WriteCommit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SpeciesViewer")

# def show_global_settings_dialog(page, config):
#     splash_field = ft.Switch(label="Enable Splash", value=config.get("Global", "splash_enabled", fallback="yes") == "yes")
#     width_field = ft.TextField(
#         label="Window width",
#         value=config.get("Global", "default_window_width", fallback="1600"),
#         tooltip="Recommended: 1600 (range: 800–3000)",
#         keyboard_type=ft.KeyboardType.NUMBER,
#         on_change=lambda e: validate_integer(e, 800, 3000)
#     )

#     height_field = ft.TextField(
#         label="Window height",
#         value=config.get("Global", "default_window_height", fallback="1200"),
#         tooltip="Recommended: 1200 (range: 600–2250)",
#         keyboard_type=ft.KeyboardType.NUMBER,
#         on_change=lambda e: validate_integer(e, 600, 2500)
#     )

#     webpassword_field = ft.TextField(
#         label="Website password",
#         value=config.get("websettings", "sftppwd", fallback="? still needed"),
#         tooltip="Check with Web provider what password you need to use for sFTP"
#     )    

#     def save_settings(e):
#         config.set("Global", "splash_enabled", "yes" if splash_field.value else "no")
#         config.set("Global", "default_window_width", width_field.value)
#         config.set("Global", "default_window_height", height_field.value)
#         config.set("websettings", "sftppwd", webpassword_field.value)        
#         with open("sv_config.ini", "w", encoding="utf-8") as f:
#             config.write(f)
#         splash_field.page.show_snack_bar(ft.SnackBar(ft.Text("✅ Global settings saved")))

#     controls = [
#         width_field,
#         height_field,
#         ft.Divider(),        
#         splash_field,
#         ft.Divider(),
#         webpassword_field,
#         ft.ElevatedButton("Save", on_click=save_settings)
#     ]

#     create_dialog("Global Settings", controls, page)

def show_global_settings_dialog(page, config, dialog_manager):
    splash_field = ft.Switch(label="Enable Splash", value=config.get("Global", "splash_enabled", fallback="yes") == "yes")
    width_field = ft.TextField(
        label="Window width",
        value=config.get("Global", "default_window_width", fallback="1600"),
        tooltip="Recommended: 1600 (range: 800–3000)",
        keyboard_type=ft.KeyboardType.NUMBER,
        on_change=lambda e: validate_integer(e, 800, 3000)
    )
    height_field = ft.TextField(
        label="Window height",
        value=config.get("Global", "default_window_height", fallback="1200"),
        tooltip="Recommended: 1200 (range: 600–2500)",
        keyboard_type=ft.KeyboardType.NUMBER,
        on_change=lambda e: validate_integer(e, 600, 2500)
    )
    webpassword_field = ft.TextField(
        label="Website password",
        value=config.get("websettings", "sftppwd", fallback="? still needed"),
        tooltip="Check with Web provider what password you need to use for sFTP"
    )    

    def save_settings(e):
        config.set("Global", "splash_enabled", "yes" if splash_field.value else "no")
        config.set("Global", "default_window_width", width_field.value)
        config.set("Global", "default_window_height", height_field.value)
        config.set("websettings", "sftppwd", webpassword_field.value)        
        with open("sv_config.ini", "w", encoding="utf-8") as f:
            config.write(f)
        page.show_snack_bar(ft.SnackBar(ft.Text("✅ Global settings saved")))

    controls = [
        width_field,
        height_field,
        ft.Divider(),
        splash_field,
        ft.Divider(),
        webpassword_field,
        ft.ElevatedButton("Save", on_click=save_settings)
    ]

    dialog_manager.show_dialog("Global Settings", controls, dialog_id="global_settings")


# def show_repositories_dialog(page, config):
#     master_field, master_browse = directory_field("[master] Database", page=page, config_section="master", config_key="masterbiotadatabase")
#     trip_field, trip_browse = directory_field("[trip] Database", page=page, config_section="trip", config_key="tripbiotadatabase")

#     master_web = ft.TextField(label="[master] Web root", value=config.get("master", "masterbiotawebroot", fallback=""))
#     trip_web = ft.TextField(label="[trip] Web root", value=config.get("trip", "tripbiotawebroot", fallback=""))

#     def save_repos(e):
#         config.set("master", "masterbiotadatabase", master_field.value)
#         config.set("master", "masterbiotawebroot", master_web.value)
#         config.set("trip", "tripbiotadatabase", trip_field.value)
#         config.set("trip", "tripbiotawebroot", trip_web.value)
#         with open("sv_config.ini", "w", encoding="utf-8") as f:
#             config.write(f)

#         page.show_snack_bar(ft.SnackBar(ft.Text("✅ Repository settings saved")))

#     controls = [
#         master_browse, # ft.Column([master_field, master_browse], spacing=4),
#         master_web,
#         ft.Divider(),
#         trip_browse, # ft.Column([trip_field, trip_browse], spacing=4),
#         trip_web,
#         ft.ElevatedButton("Save", on_click=save_repos)
#     ]

#     create_dialog("Repositories", controls, page)

def show_repositories_dialog(page, config, dialog_manager):
    master_field, master_browse = directory_field("[master] Database", page=page, config_section="master", config_key="masterbiotadatabase")
    trip_field, trip_browse = directory_field("[trip] Database", page=page, config_section="trip", config_key="tripbiotadatabase")

    master_web = ft.TextField(label="[master] Web root", value=config.get("master", "masterbiotawebroot", fallback=""))
    trip_web = ft.TextField(label="[trip] Web root", value=config.get("trip", "tripbiotawebroot", fallback=""))

    def save_repos(e):
        config.set("master", "masterbiotadatabase", master_field.value)
        config.set("master", "masterbiotawebroot", master_web.value)
        config.set("trip", "tripbiotadatabase", trip_field.value)
        config.set("trip", "tripbiotawebroot", trip_web.value)
        with open("sv_config.ini", "w", encoding="utf-8") as f:
            config.write(f)
        page.show_snack_bar(ft.SnackBar(ft.Text("✅ Repository settings saved")))

    controls = [
        master_browse,
        master_web,
        ft.Divider(),
        trip_browse,
        trip_web,
        ft.ElevatedButton("Save", on_click=save_repos)
    ]

    dialog_manager.show_dialog("Repositories", controls, dialog_id="repositories_settings")


def show_my_dialog(page, config, dialog_manager):
    text_field = ft.TextField(label="This is a dialog to test a GUI mechanism")

    dialog_manager.show_dialog(
        title="My Custom Dialog",
        content_controls=[text_field],
        dialog_id="my_custom_dialog",
        on_close=lambda: print("Dialog closed. Cleanups go here.")
    )



def show_about_dialog(page,  config, dialog_manager):
    def dismiss(e):
        page.dialog.open = False
        page.update()
   
    about_text = (
        "📷 Photo Sorter\n\n"
        "Photo Sorter is a utility to organize and label photos by metadata.\n"
        "It was designed to help process photo collections by color tags, creation dates,\n"
        "and generate blog content automatically.\n\n"
        "Version: 0.1.0\n"
        "Author: You\n"
        "Built with ❤️ using Flet."
    )

    dialog_manager.show_dialog = ft.AlertDialog(
        title=ft.Text("About SpeciesViewer"),
        content=ft.Text(about_text),
        actions=[ft.TextButton("OK", on_click=dismiss)],
        modal=True
    )

def show_photo_sorter_overlay(page, update_status_callback, add_to_global_log):
    FIELD_WIDTH = 900

    source_field, source_browse = directory_field("Source folder", page=page, config_section="PhotoSorter", config_key="last_source_folder")
    dest_field, dest_browse = directory_field("Destination folder", page=page, config_section="PhotoSorter", config_key="last_dest_folder")

    log_box = ft.TextField(value="", multiline=True, read_only=True, width=FIELD_WIDTH, height=400)

    # Load saved defaults
    source_field.value = get_setting("PhotoSorter", "last_source_folder", "")
    dest_field.value = get_setting("PhotoSorter", "last_dest_folder", "")

    picker_source = ft.FilePicker()
    picker_dest = ft.FilePicker()
    page.overlay.extend([picker_source, picker_dest])

    def add_log_entry(msg):
        log_box.value += "\n" + msg
        add_to_global_log(msg)
        page.update()

    def browse_source(e):
        def result(ev):
            if ev.files:
                path = os.path.dirname(ev.files[0].path)
                source_field.value = path
                page.update()
        picker_source.on_result = result
        picker_source.pick_files(dialog_title="Pick any file in source folder")

    def browse_dest(e):
        def result(ev):
            if ev.files:
                path = os.path.dirname(ev.files[0].path)
                dest_field.value = path
                page.update()
        picker_dest.on_result = result
        picker_dest.pick_files(dialog_title="Pick any file in destination folder")

    def run_sorter(e):
        src = source_field.value.strip()
        dst = dest_field.value.strip()
        if not os.path.isdir(src) or not os.path.isdir(dst):
            add_log_entry("⚠ Invalid folder path.")
            update_status_callback("Error: Invalid folders.")
        else:
            add_log_entry(f"🔧 Running Photo Sorter for {os.path.basename(src)}...")
            try:
                process_source_dir(src, dst, "Oostzee zeilzomer", "Baltic Sea sailing summer", log_callback=add_log_entry)
                add_log_entry("✅ Done!")
                update_status_callback("Photo Sorter completed.")
                set_setting("PhotoSorter", "last_source_folder", src)
                set_setting("PhotoSorter", "last_dest_folder", dst)
            except Exception as err:
                add_log_entry(f"❌ Error: {err}")
                update_status_callback("Error in Photo Sorter.")

    controls = [
        # source_field,
        source_browse,
        # dest_field,
        dest_browse,
        ft.ElevatedButton("Run Photo Sorter", on_click=run_sorter),
        log_box
    ]

    create_dialog("Photo Sorter", controls, page)

def image_file_field(label: str, expected_size: tuple, root_field: ft.TextField, page: ft.Page, value: str = ""):
    import os
    from PIL import Image
    from tkinter import Tk, filedialog
    import flet as ft

    field = ft.TextField(label=label, width=720)

    status_icon = ft.Icon(name=ft.icons.ERROR, color=ft.colors.RED, tooltip="Select a .JPG with correct size")
    browse_btn = ft.IconButton(icon=ft.icons.FOLDER, tooltip="Browse", on_click=lambda e: pick_file())

    def validate(path):
        if not os.path.isfile(path) or not path.lower().endswith(".jpg"):
            status_icon.name = ft.icons.ERROR
            status_icon.color = ft.colors.RED
            status_icon.tooltip = f"'{os.path.basename(path)}' is not a valid .JPG file"
        else:
            try:
                with Image.open(path) as img:
                    if img.size == expected_size:
                        status_icon.name = ft.icons.CHECK_CIRCLE
                        status_icon.color = ft.colors.GREEN
                        status_icon.tooltip = ""
                    else:
                        status_icon.name = ft.icons.ERROR
                        status_icon.color = ft.colors.RED
                        status_icon.tooltip = f"Size mismatch: expected {expected_size}, got {img.size}"
            except Exception as err:
                status_icon.name = ft.icons.ERROR
                status_icon.color = ft.colors.RED
                status_icon.tooltip = f"Error reading image: {err}"

        page.update()

    def pick_file():
        initial = root_field.value.strip()
        if not os.path.isdir(initial):
            page.show_snack_bar(ft.SnackBar(ft.Text("❌ Select valid Trip root first")))
            return
        root = Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        selected = filedialog.askopenfilename(
            initialdir=initial,
            filetypes=[("JPG images", "*.jpg")],
            title=f"Select {label}"
        )
        root.destroy()
        if selected:
            field.value = os.path.abspath(selected)
            validate(field.value)
            page.update()

    field.on_change = lambda e: validate(field.value)
    validate(field.value)  # Initial validation

    row = ft.Row([field, browse_btn, status_icon], spacing=10, alignment=ft.MainAxisAlignment.START)
    return field, row

def read_trip_ini(trip_root: str):
    import os
    from configparser import ConfigParser

    ini_path = os.path.join(trip_root, "trip.ini")
    if not os.path.isfile(ini_path):
        return None  # No ini file yet

    parser = ConfigParser()
    parser.read(ini_path, encoding="utf-8")

    result = {
        "trip_banner": parser.get("trip_config", "trip_banner", fallback=""),
        "trip_picture": parser.get("trip_config", "trip_picture", fallback=""),
        "fromdate": parser.get("trip_config", "fromdate", fallback=""),
        "todate": parser.get("trip_config", "todate", fallback=""),
        "title_nl": parser.get("trip_text", "title_nl", fallback=""),
        "message_nl": parser.get("trip_text", "message_nl", fallback=""),
        "title_en": parser.get("trip_text", "title_en", fallback=""),
        "message_en": parser.get("trip_text", "message_en", fallback=""),
    }
    return result

def generate_svconfig_ini(config, trip_root: str, svconfig_path: str = None):
    import os
    from configparser import ConfigParser
    from datetime import datetime

    if svconfig_path is None:
        svconfig_path = "svconfig.ini"

    trip_db = config.get("trip", "tripbiotadatabase", fallback="")
    trip_web = config.get("trip", "tripbiotawebroot", fallback="")
    master_db = config.get("master", "masterbiotadatabase", fallback="")
    species_csv = os.path.join(master_db, "metadata", "active", "species.csv")
    project_files = config.get("project", "picturedirectory", fallback="")
    webpassword  = config.get("websettings","Sftppwd", fallback="? still needed" )

    cfg = ConfigParser()

    cfg["repository"] = {
        "BiotaDatabase": trip_db,
        "BiotaWebRoot": trip_web,
        "ReferenceSpecies": species_csv
    }

    cfg["master"] = {
        "MasterBiotaDatabase": master_db
    }

    cfg["project"] = {
        "PictureDirectory": project_files
    }
    
    cfg["debug"] = {
        "Classification": "0",
        "SpeciesFound": "0"
        # Optional: "WebSiteGeneration": "0"
    }

    cfg["websettings"] = {
        "Sftppwd": webpassword  
    }

    try:
        with open(svconfig_path, "w", encoding="utf-8") as f:
            cfg.write(f)
        logger.info(f"✅ svconfig.ini written to {svconfig_path}")
        return
    except Exception as err:
        logger.info(f"❌ Failed to write svconfig.ini: {err}")
        return 
    
def call_batch_module(config, update_status_callback, page, logger, module_called, before_msg, after_msg):
    import configparser

    # Get your shared logger
    # logger = logging.getLogger("speciesviewer")
    print("All handlers BEFORE remove:", logger.handlers)

    # 🛑 Remove StreamHandlers and store them
    original_stream_handlers = [
        h for h in logger.handlers if isinstance(h, logging.StreamHandler)
    ]
    for h in original_stream_handlers:
        logger.removeHandler(h)

    print("All handlers AFTER remove:", logger.handlers)

    try:
        # ✅ Your call runs quietly now (only GUI gets logs if you added it)
        generate_svconfig_ini(config, "")
        args = None
        module_config = configparser.ConfigParser()
        module_config.read("svconfig.ini")
        update_status_callback(before_msg)
        logger.info(before_msg)
        page.update()
        module_called(args, module_config)
        logger.info(after_msg) 
        update_status_callback(after_msg)
    finally:
        # 🔁 Restore original terminal handlers after execution
        for h in original_stream_handlers:
            logger.addHandler(h)
    
    print("All handlers AFTER restored:", logger.handlers)


def generate_trip_ini(
    trip_root: str,
    trip_name: str,
    banner_filename: str,
    picture_filename: str,
    from_date: str,
    to_date: str,
    title_nl: str,
    message_nl: str,
    title_en: str,
    message_en: str,
    config,
    logger
):
    import os
    from datetime import datetime

    ini_path = os.path.join(trip_root, "trip.ini")

    master_web = config.get("master", "masterbiotawebroot", fallback="/nature/")
    master_disk = config.get("master", "masterbiotadatabase", fallback="E:\\Dropbox\\naturedb")
    trip_url_name = config.get("trip", "tripbiotawebroot", fallback=trip_name)

    ini_text = f"""\
[trip_config]
master_repository_web = {master_web}        ; config: master / masterbiotawebroot
master_repository_disk = {master_disk}      ; config: master / masterbiotadatabase
trip_banner = {banner_filename}             ; the filename of the 2000x270 jpg
trip_picture = {picture_filename}           ; the filename of the 600x450 jpg
created = {datetime.now().strftime('%Y-%m-%d %H:%M')}           ; time this file is created/updated
trip_name = {trip_url_name}                 ; config: trip / tripbiotawebroot
fromdate = {from_date}                      ; user input
todate = {to_date}
oneblog = day                               ; hardcoded for now

[trip_text]
title_nl = {title_nl}
message_nl = {message_nl}
title_en = {title_en}
message_en = {message_en}
"""

    try:
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(ini_text)
        logger.info(f"✅ trip.ini written to {ini_path}")
        return
    except Exception as err:
        logger.info(f"❌ Failed to write trip.ini: {err}")
        return 

def show_create_trip_dialog(page, config, update_status_callback):
    from gui.logger_logic import pause_toggle, GUIHandler, setup_logger, attach_gui_handler, detach_gui_handler

    def cleanup(e):
        detach_gui_handler(logger, "createtrip")       

    root_field, root_row = directory_field("Trip root folder", page=page, config_section="trip", config_key="tripbiotadatabase")

    trip_root = config.get("trip", "tripbiotadatabase", fallback="")
    trip_data = read_trip_ini(trip_root) if os.path.isdir(trip_root) else {}

    # Example prefill:
    title_nl = ft.TextField(label="Trip title (Dutch)", value=trip_data.get("title_nl", ""), width=900)
    message_nl = ft.TextField(label="Trip description (Dutch)", value=trip_data.get("message_nl", ""), multiline=True, min_lines=3, max_lines=6, width=900)

    title_en = ft.TextField(label="Trip title (English)", value=trip_data.get("title_en", ""), width=900)
    message_en = ft.TextField(label="Trip description (English)", value=trip_data.get("message_en", ""), multiline=True, min_lines=3, max_lines=6, width=900)

    trip_image_field, trip_image_row = image_file_field("Trip Image (600×450 JPG)", (600, 450), root_field, page, value=trip_data.get("trip_picture", ""))
    topbar_field, topbar_row = image_file_field("Trip Top Web Bar (2000×270 JPG)", (2000, 270), root_field, page, value=trip_data.get("trip_banner", ""))

    from_date = ft.TextField(label="From date (YYYY-MM-DD)", width=300, value=trip_data.get("fromdate", ""))
    to_date = ft.TextField(label="To date (YYYY-MM-DD)", width=300, value=trip_data.get("todate", ""))

    local_log_box = ft.TextField(
        label="Log for batch call",
        multiline=True,
        read_only=True,
        min_lines=8,
        max_lines=12,
        width=900,
        text_style=ft.TextStyle(size=11, font_family="Courier New")
    )
    local_gui_handler = GUIHandler(local_log_box)

    logger = logging.getLogger("speciesviewer")    
    attach_gui_handler(logger, "createtrip", local_log_box)

    create_dialog("Create Trip", [
        ft.Text("blabla — what Trips are about...", width=800),
        root_row,
        trip_image_row,
        topbar_row,
        ft.Row([from_date, to_date], spacing=20),
        title_nl,
        message_nl,
        title_en,
        message_en,
        ft.Row([
            ft.ElevatedButton("Generate trip.ini", on_click=lambda e: generate_trip_ini(
                trip_root=root_field.value,
                trip_name=title_en.value,
                banner_filename=os.path.basename(topbar_field.value),
                picture_filename=os.path.basename(trip_image_field.value),
                from_date=from_date.value,
                to_date=to_date.value,
                title_nl=title_nl.value,
                message_nl=message_nl.value,
                title_en=title_en.value,
                message_en=message_en.value,
                config=config,  # 👈 Make sure this is actually `ConfigParser`, not `page`,
                logger=logger
            )),
            ft.TextButton("Create Trip Directories", on_click=lambda e: call_batch_module(
                config, update_status_callback, page, logger,
                bin.sv1MakeReposity.main,
                "Create a new trip database", "Trip created, please review the messages"
                ))
        ], alignment='start', spacing=20),
        local_log_box
    ], page)
    logger.info("Local box initialized\n")


    
def show_read_dir_dialog(page, config, update_status_callback):
    from gui.logger_logic import GUIHandler, attach_gui_handler, detach_gui_handler
    import logging

    def cleanup(e):
        detach_gui_handler(logger, "readdir_dialog")       

    local_log_box = ft.TextField(
        label="Log for batch call",
        multiline=True,
        read_only=True,
        min_lines=8,
        max_lines=40,
        width=1500,
        text_style=ft.TextStyle(size=11, font_family="Courier New")
    )
    local_gui_handler = GUIHandler(local_log_box)
    local_log_box._Control__page = page      # not supported, but works

    logger = logging.getLogger("speciesviewer")
    attach_gui_handler(logger, "readdir_dialog", local_log_box)

    dialog = create_dialog("Read Picture Directory", [
        ft.Text("Scan a directory with photo files and extract metadata (Exif tags) for each image.", width=800),
        ft.Row([
            ft.TextButton("Read Directory", on_click=lambda e: call_batch_module(
                config,
                update_status_callback,
                page,
                logger,
                bin.sv2ReadDirExif.main,
                "Read directory with picture files",
                "The directory was read, please review the messages"
            ))
        ], alignment='start', spacing=20),
        local_log_box
    ], page, on_close=cleanup)


def show_write_pictures_dialog(page, config, update_status_callback):
    from gui.logger_logic import GUIHandler, attach_gui_handler, detach_gui_handler
    import logging

    def cleanup(e):
        detach_gui_handler(logger, "writepict_dialog")           

    local_log_box = ft.TextField(
        label="Log for batch call",
        multiline=True,
        read_only=True,
        min_lines=8,
        max_lines=40,
        width=1500,
        text_style=ft.TextStyle(size=11, font_family="Courier New")
    )
    local_gui_handler = GUIHandler(local_log_box)
    local_log_box._Control__page = page      # not supported, but works    

    logger = logging.getLogger("speciesviewer")
    attach_gui_handler(logger, "writepict_dialog", local_log_box)

    create_dialog("Write Picture Entries", [
        ft.Text("Add species tags and metadata to photo entries based on earlier readings.", width=800),
        ft.Row([
            ft.TextButton("Write Picture Data", on_click=lambda e: call_batch_module(
                config,
                update_status_callback,
                page,
                logger,
                bin.sv3WritePictures.main,
                "Write Pictures with Species information",
                "Please review the messages"
            ))
        ], alignment='start', spacing=20),
        local_log_box
    ], page, on_close=cleanup)

def show_write_blog_dialog(page, config, update_status_callback):
    import os
    import configparser
    from pathlib import Path
    from gui.logger_logic import GUIHandler, attach_gui_handler, detach_gui_handler
    import logging

    def cleanup(e):
        detach_gui_handler(logger, "blog_dialog")           

    logger = logging.getLogger("speciesviewer")

    # Extract picture directory and trip.ini info
    tripdir = config.get("trip", "tripbiotadatabase")
    trip_ini_path = os.path.join(tripdir, "trip.ini")
    picturedir = config.get("project", "picturedirectory")
    blog_ini_path = os.path.join(picturedir, "blog.ini")

    trip_config = configparser.ConfigParser()
    trip_config.read(trip_ini_path)

    blog_config = configparser.ConfigParser()
    if os.path.exists(blog_ini_path):
        blog_config.read(blog_ini_path)
        created_blog_ini = False                        # cannot message here, remember
    else:
        created_blog_ini = True
        blog_config["blog_config"] = {
            "only_new": "False",
            "minimum_score": "25",
            "column_width": "300"
        }
        blog_config["blog_selection"] = {
            "species": "biota"
        }
        blog_config["blog_text"] = {
            "titel_nl": f"{trip_config.get("trip_text", "title_nl")} foto's van {Path(picturedir).stem}",
            "message_nl": f"Deze post is gemaakt op basis van oudere foto's tijdens de {trip_config.get("trip_text", "title_nl")}",
            "titel_en": f"{trip_config.get("trip_text", "title_en")} photos from {Path(picturedir).stem}",
            "message_en": f"This post is based on older photos taken during the {trip_config.get("trip_text", "title_en")}",
        }

     

    # GUI fields for editable blog.ini values
    titel_nl = ft.TextField(label="Titel NL", value=blog_config["blog_text"]["titel_nl"])
    message_nl = ft.TextField(label="Message NL", value=blog_config["blog_text"]["message_nl"])
    titel_en = ft.TextField(label="Titel EN", value=blog_config["blog_text"]["titel_en"])
    message_en = ft.TextField(label="Message EN", value=blog_config["blog_text"]["message_en"])
    only_new = ft.Checkbox(label="Only New", value=blog_config["blog_config"].getboolean("only_new"))
    species = ft.TextField(label="Species", value=blog_config["blog_selection"]["species"])

    minimum_score = ft.TextField(
        label="Minimum Score (0–200)",
        value=blog_config["blog_config"]["minimum_score"],
        on_change=lambda e: validate_inputs()
    )
    column_width = ft.TextField(
        label="Column Width (150–600)",
        value=blog_config["blog_config"]["column_width"],
        on_change=lambda e: validate_inputs()
    )

    def save_blog_ini(e):
        # your logic to save blog.ini
        pass

    save_button = ft.TextButton("Save blog.ini", on_click=save_blog_ini)    

    # Validation logic
    def validate_inputs():
        try:
            score = int(minimum_score.value)
            width = int(column_width.value)
            score_valid = 0 <= score <= 200
            width_valid = 150 <= width <= 600

            if not score_valid:
                minimum_score.error_text = "Score must be between 0 and 200"
            else:
                minimum_score.error_text = None

            if not width_valid:
                column_width.error_text = "Width must be between 150 and 600"
            else:
                column_width.error_text = None

            save_button.disabled = not (score_valid and width_valid)
        except ValueError:
            minimum_score.error_text = "Must be a number"
            column_width.error_text = "Must be a number"
            save_button.disabled = True

        page.update()    

    def save_blog_ini(e):
        blog_config["blog_config"]["only_new"] = str(only_new.value)
        blog_config["blog_config"]["minimum_score"] = minimum_score.value
        blog_config["blog_config"]["column_width"] = column_width.value
        blog_config["blog_selection"]["species"] = species.value
        blog_config["blog_text"]["titel_nl"] = titel_nl.value
        blog_config["blog_text"]["message_nl"] = message_nl.value
        blog_config["blog_text"]["titel_en"] = titel_en.value
        blog_config["blog_text"]["message_en"] = message_en.value

        with open(blog_ini_path, "w") as configfile:
            blog_config.write(configfile)
        validate_inputs()

        update_status_callback("Saved blog.ini configuration.")
        logger.info("Saved blog.ini configuration.")

    local_log_box = ft.TextField(
        label="Log for blog creation",
        multiline=True,
        read_only=True,
        min_lines=8,
        max_lines=12,
        width=900,
        text_style=ft.TextStyle(size=11, font_family="Courier New")
    )
    attach_gui_handler(logger, "blog_dialog", local_log_box)
    local_log_box._Control__page = page      # not supported, but works        

    create_dialog("Configure & Generate Blog", [
        ft.Text("Blog Configuration Inputs", width=800),
        titel_nl, message_nl,
        titel_en, message_en,
        minimum_score, column_width,
        # only_new, species,
        ft.Row([
            ft.TextButton("Save blog.ini", on_click=save_blog_ini),
            ft.TextButton("Generate Blog", on_click=lambda e: call_batch_module(
                config,
                update_status_callback,
                page,
                logger,
                bin.sv4WriteBlog.main,
                "Create a Web blog with pictures",
                "Please review the messages"
            ), disabled=not os.path.exists(blog_ini_path)),
        ], alignment="start", spacing=20 ),
        local_log_box
    ], page, on_close=cleanup)

    if created_blog_ini:
        logger.info("Initialize blog.ini configuration.")
    if not save_button.disabled:
        logger.info("blog.ini has valid, but possible default, contents.")
    else:
        logger.info("blog.ini has not valid, correct this first.")


def show_build_website_dialog(page, config, update_status_callback):
    from gui.logger_logic import GUIHandler, attach_gui_handler, detach_gui_handler
    import logging
    
    def cleanup(e):
        detach_gui_handler(logger, "website_dialog") 

    local_log_box = ft.TextField(
        label="Log for batch call",
        multiline=True,
        read_only=True,
        min_lines=8,
        max_lines=40,
        width=1200,
        text_style=ft.TextStyle(size=11, font_family="Courier New")
    )
    local_gui_handler = GUIHandler(local_log_box)
    local_log_box._Control__page = page      # not supported, but works    

    logger = logging.getLogger("speciesviewer")
    attach_gui_handler(logger, "website_dialog", local_log_box)

    create_dialog("Build Website", [
        ft.Text("Prepare all web files and site structure for publication.", width=800),
        ft.Row([
            ft.TextButton("Build Website", on_click=lambda e: call_batch_module(
                config,
                update_status_callback,
                page,
                logger,
                bin.sv5BuildWebsite.main,
                "Build updates for later publication",
                "Please review the messages"
            ))
        ], alignment='start', spacing=20),
        local_log_box
    ], page, on_close=cleanup)

def show_freeze_dialog(page, config, update_status_callback):
    from gui.logger_logic import GUIHandler, attach_gui_handler, detach_gui_handler
    import logging
    
    def cleanup(e):
        detach_gui_handler(logger, "freeze_dialog") 

    local_log_box = ft.TextField(
        label="Log for batch call",
        multiline=True,
        read_only=True,
        min_lines=8,
        max_lines=40,
        width=1200,
        text_style=ft.TextStyle(size=11, font_family="Courier New")
    )
    local_gui_handler = GUIHandler(local_log_box)
    local_log_box._Control__page = page      # not supported, but works        

    logger = logging.getLogger("speciesviewer")
    attach_gui_handler(logger, "freeze_dialog", local_log_box)

    create_dialog("Freeze Trip Data", [
        ft.Text("Finalize and lock down all collected information for archival or publishing.", width=800),
        ft.Row([
            ft.TextButton("Freeze Data", on_click=lambda e: call_batch_module(
                config,
                update_status_callback,
                page,
                logger,
                bin.sv6Freeze.main,
                "Freeze all collected data",
                "Please review the messages"
            ))
        ], alignment='start', spacing=20),
        local_log_box
    ], page, on_close=cleanup)

def show_commit_dialog(page, config, update_status_callback):
    from gui.logger_logic import GUIHandler, attach_gui_handler, detach_gui_handler
    import logging

    def cleanup(e):
        detach_gui_handler(logger, "commit_dialog") 

    local_log_box = ft.TextField(
        label="Log for batch call",
        multiline=True,
        read_only=True,
        min_lines=8,
        max_lines=40,
        width=1200,
        text_style=ft.TextStyle(size=11, font_family="Courier New")
    )
    local_gui_handler = GUIHandler(local_log_box)
    local_log_box._Control__page = page      # not supported, but works        

    logger = logging.getLogger("speciesviewer")
    attach_gui_handler(logger, "commit_dialog", local_log_box)

    create_dialog("Write Website Commit", [
        ft.Text("Push final updates to the live trip website.", width=800),
        ft.Row([
            ft.TextButton("Commit Trip Website", on_click=lambda e: call_batch_module(
                config,
                update_status_callback,
                page,
                logger,
                bin.sv7WriteCommit.main,
                "Update the trip website",
                "Please review the messages"
            ))
        ], alignment='start', spacing=20),
        local_log_box
    ], page, on_close=cleanup)
