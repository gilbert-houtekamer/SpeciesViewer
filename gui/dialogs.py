import flet as ft
import os
import asyncio
from utils.photo_sorter import process_source_dir
from utils.config import get_setting, set_setting
from gui.components import create_dialog, validate_integer, directory_field
from gui.log_widgets import LogTailWidget
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SpeciesViewer")

def show_global_settings_dialog(page, config):
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
        tooltip="Recommended: 1200 (range: 600–2250)",
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
        splash_field.page.show_snack_bar(ft.SnackBar(ft.Text("✅ Global settings saved")))

    controls = [
        width_field,
        height_field,
        ft.Divider(),        
        splash_field,
        ft.Divider(),
        webpassword_field,
        ft.ElevatedButton("Save", on_click=save_settings)
    ]

    create_dialog("Global Settings", controls, page)

def show_repositories_dialog(page, config):
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
        master_browse, # ft.Column([master_field, master_browse], spacing=4),
        master_web,
        ft.Divider(),
        trip_browse, # ft.Column([trip_field, trip_browse], spacing=4),
        trip_web,
        ft.ElevatedButton("Save", on_click=save_repos)
    ]

    create_dialog("Repositories", controls, page)


def show_about_dialog(page):
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

    about_dialog = ft.AlertDialog(
        title=ft.Text("About Photo Sorter"),
        content=ft.Text(about_text),
        actions=[ft.TextButton("OK", on_click=dismiss)],
        modal=True
    )
    page.dialog = about_dialog
    about_dialog.open = True
    page.update()


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
    
# def call_batch_module(config):
#     import configparser
#     import bin.sv2ReadDirExif
#     #import bin.sv1MakeReposity
    
#     generate_svconfig_ini(config, "")
#     args = None
#     module_config = configparser.ConfigParser()
#     module_config.read("svconfig.ini")
 
#     bin.sv2ReadDirExif.main(args, module_config)
#     return

def call_batch_module(config, update_status_callback, page, logger):
    import configparser
    import bin.sv2ReadDirExif

    # Get your shared logger
    # logger = logging.getLogger("speciesviewer")
    print("All handlers BEFORE:", logger.handlers)

    # 🛑 Remove StreamHandlers and store them
    original_stream_handlers = [
        h for h in logger.handlers if isinstance(h, logging.StreamHandler)
    ]
    for h in original_stream_handlers:
        logger.removeHandler(h)

    print("All handlers AFTER:", logger.handlers)

    try:
        # ✅ Your call runs quietly now (only GUI gets logs if you added it)
        generate_svconfig_ini(config, "")
        args = None
        module_config = configparser.ConfigParser()
        module_config.read("svconfig.ini")
        update_status_callback("Call xxxx.")
        logger.info("   Calling bin.sv2ReadDirExif\n")
        page.update()
        rc = bin.sv2ReadDirExif.main(args, module_config)
        logger.info("   bin.sv2ReadDirExif completed\n") 
        update_status_callback("Restore xxxx.")
    finally:
        # 🔁 Restore original terminal handlers after execution
        for h in original_stream_handlers:
            logger.addHandler(h)


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
    # print("All handlers BEFORE local handler:", logger.handlers)
    attach_gui_handler(logger, "settings_dialog", local_log_box)
    # print("All handlers AFTER local handler:", logger.handlers)    

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
            ft.TextButton("Create Trip Directories", on_click=lambda e: call_batch_module(config, update_status_callback, page, logger))
        ], alignment='start', spacing=20),
        local_log_box
    ], page)
    logger.info("Local box initialized\n")
    #detach_gui_handler(logger, "settings_dialog")

    
