import flet as ft
import os
#from plyer import filechooser
import tkinter as tk
from tkinter import filedialog
from utils.config import get_setting, set_setting  # if these are your helpers

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

# def folder_field(label, initial_value, page):
#     field = ft.TextField(label=label, value=initial_value)
#     picker = ft.FilePicker()
#     page.overlay.append(picker)

#     def browse(e):
#         def result(ev):
#             if ev.files and len(ev.files) > 0:
#                 path = os.path.dirname(ev.files[0].path)
#                 field.value = path
#                 page.update()
#         picker.on_result = result
#         #picker.pick_files(dialog_title=f"Pick any file in {label}")
#         picker.get_directory_path(dialog_title="Choose folder")

#     def test(e):
#         path = field.value.strip()
#         exists = os.path.isdir(path)
#         page.show_snack_bar(ft.SnackBar(ft.Text("✅ Folder exists" if exists else "❌ Folder not found")))

#     # Compact layout: field + icon buttons to right
#     field_row = ft.Row([
#         ft.Container(content=field, width=600),
#         ft.Column([
#             ft.IconButton(icon=ft.icons.FOLDER, tooltip="Browse", on_click=browse),
#             ft.IconButton(icon=ft.icons.SCIENCE, tooltip="Test", on_click=test)
#         ])
#     ])
#     return field, field_row

def make_integer_field(label, value, min_val, max_val):
    return ft.TextField(
        label=label,
        value=str(value),
        keyboard_type=ft.KeyboardType.NUMBER,
        on_change=lambda e: validate_integer(e, min_val, max_val),
        error_text=None
    )

def validate_integer(e, min_val, max_val):
    try:
        val = int(e.control.value)
        if val < min_val or val > max_val:
            e.control.error_text = f"Must be between {min_val} and {max_val}"
        else:
            e.control.error_text = None
    except:
        e.control.error_text = "Must be an integer"
    e.page.update()


def pick_directory(label: str, initial_path: str, page: ft.Page):
    field = ft.TextField(label=label, value=initial_path, width=900)

    def browse(e):
        root = tk.Tk()
        root.withdraw()  # hide base window
        folder = filedialog.askdirectory(
            initialdir=initial_path if os.path.isdir(initial_path) else os.getcwd(),
            title=f"Choose folder for {label}"
        )
        root.destroy()  # cleanup tkinter window
        if folder:
            field.value = os.path.abspath(folder)
            page.show_snack_bar(ft.SnackBar(ft.Text(f"📁 Selected: {field.value}")))
            page.update()

    browse_button = ft.TextButton("📂 Browse", on_click=browse)

    return field, browse_button


    # def browse(e):
    #     paths = filechooser.choose_dir(title=f"Select folder for {label}")
    #     if paths and len(paths) > 0:
    #         chosen_path = paths[0]
    #         page.show_snack_bar(ft.SnackBar(ft.Text(f"📁 Selected: {chosen_path}")))
    #         if on_path_selected:
    #             on_path_selected(chosen_path)

    # return ft.TextButton("📂 Pick folder", on_click=browse, tooltip=f"Pick folder for {label}")


# def directory_field(label: str, initial_path: str, page: ft.Page):
#     field = ft.TextField(label=label, value=initial_path, width=900)
#     picker = ft.FilePicker()

#     # Callback: called when user picks a directory
#     def on_result(e: ft.FilePickerResultEvent):
#         if e.path:
#             field.value = os.path.abspath(e.path)
#             page.update()

#     picker.on_result = on_result
#     page.overlay.append(picker)

#     browse_btn = ft.TextButton("📂 Browse", on_click=lambda e: picker.get_directory_path(dialog_title=f"Pick folder for {label}"))

#     return field, browse_btn

# def directory_field(label: str, initial_path: str, page: ft.Page):
#     field = ft.TextField(label=label, value=initial_path, width=900)

#     def browse(e):
#         root = tk.Tk()
#         root.withdraw()  # hide root window

#         # Force focus and move to top
#         root.attributes('-topmost', True)
#         root.update()

#         folder = filedialog.askdirectory(
#             initialdir=initial_path if os.path.isdir(initial_path) else os.getcwd(),
#             title=f"Choose folder for {label}"
#         )
#         root.destroy()

#         if folder:
#             field.value = os.path.abspath(folder)
#             page.show_snack_bar(ft.SnackBar(ft.Text(f"📁 Selected: {field.value}")))
#             page.update()

#     browse_button = ft.TextButton("📂 Browse", on_click=browse)

#     return field, browse_button

def directory_field(label: str, initial_path: str = "", page: ft.Page = None, config_section: str = None, config_key: str = None):
    # Use config fallback if no path provided
    if initial_path == "" and config_section and config_key:
        initial_path = get_setting(config_section, config_key, "")

    field = ft.TextField(label=label, value=initial_path, width=900)

    def browse(e):
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        root.update()

        folder = filedialog.askdirectory(
            initialdir=field.value if os.path.isdir(field.value) else os.getcwd(),
            title=f"Choose folder for {label}"
        )
        root.destroy()

        if folder:
            chosen = os.path.abspath(folder)
            field.value = chosen
            if config_section and config_key:
                set_setting(config_section, config_key, chosen)
            if page:
                page.show_snack_bar(ft.SnackBar(ft.Text(f"📁 Selected: {chosen}")))
                page.update()

    browse_btn = ft.TextButton("📂 Browse", on_click=browse)

    return field, browse_btn
