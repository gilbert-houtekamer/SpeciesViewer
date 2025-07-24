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

def directory_field(label: str, page: ft.Page, config_section: str = None, config_key: str = None):
    import os, tkinter as tk
    from tkinter import filedialog
    from utils.config import get_setting, set_setting
    import flet as ft

    initial_path = get_setting(config_section, config_key, "") if config_section and config_key else ""
    field = ft.TextField(label=label, value=initial_path, width=720)

    status_icon = ft.Icon(
        name=ft.icons.CHECK_CIRCLE,
        color=ft.colors.GREEN,
        tooltip="Directory exists"
    )

    def update_status(path):
        if os.path.isdir(path):
            status_icon.name = ft.icons.CHECK_CIRCLE
            status_icon.color = ft.colors.GREEN
            status_icon.tooltip = "Directory exists"
        else:
            status_icon.name = ft.icons.ERROR
            status_icon.color = ft.colors.RED
            status_icon.tooltip = "Directory not found, create directory or check for typos"

        page.update()

    def browse(e):
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()

        folder = filedialog.askdirectory(
            initialdir=field.value if os.path.isdir(field.value) else os.getcwd(),
            title=f"Choose folder for {label}"
        )
        root.destroy()

        if folder:
            chosen = os.path.abspath(folder)
            field.value = chosen
            update_status(chosen)
            if config_section and config_key:
                set_setting(config_section, config_key, chosen)
            page.show_snack_bar(ft.SnackBar(ft.Text(f"📁 {label}: {chosen}")))
            page.update()

    browse_btn = ft.IconButton(
        icon=ft.icons.FOLDER,
        tooltip="Browse",
        on_click=browse
    )

    field.on_change = lambda e: update_status(field.value)
    update_status(initial_path)

    # 👌 Sleek row layout
    row = ft.Row([field, browse_btn, status_icon], spacing=10, alignment=ft.MainAxisAlignment.START)
    return field, row

