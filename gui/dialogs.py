import flet as ft
import os
from utils.photo_sorter import process_source_dir, log_and_print
from utils.config import get_setting, set_setting

def create_dialog(title: str, content_controls: list, page: ft.Page, dismiss_callback=None):
    def dismiss(e):
        page.overlay.remove(dialog_container)
        page.on_keyboard_event = None
        page.update()
        if dismiss_callback:
            dismiss_callback()

    # ESC key closes dialog
    def handle_keypress(e: ft.KeyboardEvent):
        if e.key == "Escape":
            dismiss(e)

    page.on_keyboard_event = handle_keypress

    title_bar = ft.Container(
        bgcolor="#808000",
        padding=10,
        content=ft.Row([
            ft.Text(title, size=22, weight="bold", color=ft.colors.ON_PRIMARY),
            ft.IconButton(icon=ft.icons.CLOSE, tooltip="Close", on_click=dismiss)
        ], alignment="spaceBetween")
    )

    content_body = ft.Container(
        content=ft.Column(content_controls, spacing=14),
        padding=20
    )

    card = ft.Card(content=ft.Column([title_bar, content_body], spacing=0))

    dialog_container = ft.Container(
        content=card,
        bgcolor=ft.colors.with_opacity("#dbe5cb", 0.95),  # Light olive background
        alignment=ft.alignment.center,
        padding=30,
        expand=True
    )

    page.overlay.append(dialog_container)
    page.update()


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
    def dismiss(e):
        page.overlay.remove(overlay)
        page.on_keyboard_event = None  # clean up listener        
        page.update()

    def handle_keypress(e: ft.KeyboardEvent):
        if e.key == "Escape":
            dismiss(e)


    close_icon = ft.IconButton(
        icon=ft.icons.CLOSE,
        tooltip="Close",
        on_click=dismiss,
        icon_color=ft.colors.ON_PRIMARY
    )    
    FIELD_WIDTH = 900

    source_field = ft.TextField(label="Source folder", width=FIELD_WIDTH)
    dest_field = ft.TextField(label="Destination folder", width=FIELD_WIDTH)
    log_box = ft.TextField(value="", multiline=True, read_only=True, width=FIELD_WIDTH, height=400)
    title_bar = ft.Container(
        bgcolor="#808000",  # Olive
        padding=10,
        content=ft.Row([
            ft.Text("Photo Sorter", size=22, weight="bold", color=ft.colors.ON_PRIMARY),
            close_icon
        ], alignment="spaceBetween")
    )

    # Load saved defaults from config
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
            if ev.files and len(ev.files) > 0:
                path = os.path.dirname(ev.files[0].path)
                source_field.value = path
                page.update()
        picker_source.on_result = result
        picker_source.pick_files(file_type=ft.FilePickerFileType.ANY, dialog_title="Pick any file in source folder")

    def browse_dest(e):
        def result(ev):
            if ev.files and len(ev.files) > 0:
                path = os.path.dirname(ev.files[0].path)
                dest_field.value = path
                page.update()
        picker_dest.on_result = result
        picker_dest.pick_files(file_type=ft.FilePickerFileType.ANY, dialog_title="Pick any file in destination folder")

    def run_sorter(e):
        src = source_field.value.strip()
        dst = dest_field.value.strip()
        if not os.path.isdir(src) or not os.path.isdir(dst):
            log_box.value += "\n⚠ Invalid folder path."
            add_to_global_log("\n⚠ Invalid folder path.")
            update_status_callback("Error: Invalid folders.")
        else:
            log_box.value += f"\n🔧 Running Photo Sorter for {os.path.basename(src)}..."
            add_to_global_log(f"\n🔧 Running Photo Sorter for {os.path.basename(src)}...")
            page.update()
            try:
                process_source_dir(src, dst, "Oostzee zeilzomer", "Baltic Sea sailing summer", log_callback=add_log_entry)
                log_box.value += "\n✅ Done!"
                add_to_global_log("\n✅ Done!")
                update_status_callback("Photo Sorter completed.")

                # Save folder paths to config
                set_setting("PhotoSorter", "last_source_folder", src)
                set_setting("PhotoSorter", "last_dest_folder", dst)
            except Exception as err:
                log_box.value += f"\n❌ Error: {err}"
                add_to_global_log(f"\n❌ Error: {err}")                           
                update_status_callback("Error in Photo Sorter.")
        page.update()

    field_group = ft.Container(
        content=ft.Column([
            source_field,
            ft.TextButton("📂 Browse source", on_click=browse_source),
            dest_field,
            ft.TextButton("📂 Browse destination", on_click=browse_dest),
            ft.ElevatedButton("Run Photo Sorter", on_click=run_sorter),
            log_box
        ], spacing=14),
        padding=20
    )        

    dialog_background_color = ft.colors.with_opacity("#808000", 0.4)

    overlay = ft.Container(
        content=ft.Card(content=ft.Column([
            title_bar,
            field_group,
            ft.Row([ft.TextButton("Close", on_click=dismiss)], alignment="end")
        ], spacing=0)),
        bgcolor=dialog_background_color,
        alignment=ft.alignment.center,
        padding=30,
        expand=True
    )    

    page.overlay.append(overlay)
    page.on_keyboard_event = handle_keypress    
    page.update()    

def show_photo_sorter_dialog(page, update_status_callback):
    FIELD_WIDTH = 1000

    source_field = ft.TextField(label="Source folder", width=FIELD_WIDTH)
    dest_field = ft.TextField(label="Destination folder", width=FIELD_WIDTH)

    source_field.value = get_setting("PhotoSorter", "last_source_folder", "")
    dest_field.value = get_setting("PhotoSorter", "last_dest_folder", "")
    
    log_box = ft.TextField(value="", multiline=True, read_only=True, width=FIELD_WIDTH, height=400)

    picker_source = ft.FilePicker()
    picker_dest = ft.FilePicker()
    page.overlay.extend([picker_source, picker_dest])

    def close_dialog(e):
        page.dialog.open = False
        page.update()

    def browse_source(e):
        def result(ev):
            if ev.files and len(ev.files) > 0:
                source_field.value = os.path.dirname(ev.files[0].path)
                page.update()
        picker_source.on_result = result
        picker_source.pick_files(file_type=ft.FilePickerFileType.ANY, dialog_title="Pick any file in source folder")

    def browse_dest(e):
        def result(ev):
            if ev.files and len(ev.files) > 0:
                dest_field.value = os.path.dirname(ev.files[0].path)
                page.update()
        picker_dest.on_result = result
        picker_dest.pick_files(file_type=ft.FilePickerFileType.ANY, dialog_title="Pick any file in destination folder")

    def run_sorter(e):
        src = source_field.value.strip()
        dst = dest_field.value.strip()
        if not os.path.isdir(src) or not os.path.isdir(dst):
            log_box.value += "\n⚠ Invalid folder path."
            #add_to_global_log("\n⚠ Invalid folder path.")
            update_status_callback("Error: Invalid folders.")
        else:
            log_box.value += f"\n🔧 Running Photo Sorter for {os.path.basename(src)}..."  
            page.update()
            try:
                process_source_dir(src, dst, "Oostzee zeilzomer", "Baltic Sea sailing summer")
                log_box.value += "\n✅ Done!"
                #add_to_global_log("\n✅ Done!")
                update_status_callback("Photo Sorter completed.")
            except Exception as err:
                log_box.value += f"\n❌ Error: {err}"
                update_status_callback("Error in Photo Sorter.")
        page.update()

    content = ft.Column([
        source_field,
        ft.TextButton("📂 Browse source folder", on_click=browse_source),
        dest_field,
        ft.TextButton("📂 Browse destination folder", on_click=browse_dest),
        ft.ElevatedButton("Run Photo Sorter", on_click=run_sorter),
        log_box
    ], tight=True)

    dialog = ft.AlertDialog(
        title=ft.Text("Photo Sorter"),
        content=content,
        actions=[ft.TextButton("Close", on_click=close_dialog)],
        modal=True, 
        width=FIELD_WIDTH+100
    )

    page.dialog = dialog
    dialog.open = True
    page.update()

