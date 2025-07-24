import flet as ft
import os
from utils.photo_sorter import process_source_dir, log_and_print
from utils.config import get_setting, set_setting
from gui.components import create_dialog, make_integer_field, validate_integer, directory_field

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

    def save_settings(e):
        config.set("Global", "splash_enabled", "yes" if splash_field.value else "no")
        config.set("Global", "default_window_width", width_field.value)
        config.set("Global", "default_window_height", height_field.value)
        config.save()  # Save to disk
        splash_field.page.show_snack_bar(ft.SnackBar(ft.Text("✅ Global settings saved")))

    controls = [
        splash_field,
        width_field,
        height_field,
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
        config.save()
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
