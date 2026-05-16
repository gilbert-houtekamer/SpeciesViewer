import flet as ft
import os
import logging
#from plyer import filechooser
import tkinter as tk
from tkinter import filedialog
from utils.config import get_setting, set_setting  # if these are your helpers
from logger_logic import attach_gui_handler, detach_gui_handler

class DialogManager:
    def __init__(self, page):
        self.page = page
        self.active_dialog = None
        self.active_id = None

    def show_dialog(self, title, content_controls, dialog_id=None, on_close=None):
        def dismiss(e):
            # Dismiss overlay
            self.page.overlay.remove(self.dialog)
            self.page.dialog = None  # Important: clear dialog reference

            # Remove handler
            logger = logging.getLogger("speciesviewer")
            detach_gui_handler(logger, "speciesviewer")

            # Optional: log for peace of mind
            logger.info("Dialog closed. Cleanups complete.")

            # Force UI to refresh
            self.dialog.open = False
            self.page.update()

        def handle_esc(e: ft.KeyboardEvent):
            if e.key == "Escape":
                dismiss(e)

        self.page.on_keyboard_event = handle_esc

        close_icon = ft.Tooltip(
            message="Close",
            content=ft.GestureDetector(
                content=ft.Icon(name=ft.icons.CLOSE),
                on_tap=lambda e: dismiss(e)
            )
        )

        title_bar = ft.Container(
            bgcolor=ft.colors.AMBER,
            padding=10,
            content=ft.Row([
                ft.Text(title, size=22, weight="bold", color=ft.colors.ON_PRIMARY),
                close_icon
            ], alignment="spaceBetween")
        )

        content_body = ft.Container(
            content=ft.Column(content_controls, spacing=14),
            padding=20
        )

        card = ft.Card(
            content=ft.Column([
                title_bar,
                content_body
            ], spacing=0)
        )

        self.dialog = ft.Container(
            content=card,
            bgcolor=ft.colors.with_opacity("#000000", 0.8),
            alignment=ft.alignment.center,
            padding=30,
            expand=True
        )

        # Clean previous dialog if any
        if self.active_dialog and self.active_dialog in self.page.overlay:
            self.page.overlay.remove(self.active_dialog)

        self.page.dialog = self.dialog
        self.page.overlay.append(self.dialog)
        self.page.update()

        self.active_dialog = self.dialog
        self.active_id = dialog_id

        # Attach multiline TextField for logging (optional GUI handler)
        if dialog_id:
            for control in content_controls:
                if isinstance(control, ft.TextField) and control.multiline:
                    attach_gui_handler("speciesviewer", dialog_id, control)
                    print(f"[DialogManager] Attached GUI handler for {dialog_id}")
                    break


# def create_dialog(title: str, content_controls: list, page: ft.Page, on_close=None):
#     def dismiss(e):
#         print("Dismiss called")        
#         if dialog_container in page.overlay:
#             print("Removing dialog container")            
#             page.overlay.remove(dialog_container)
#         else:
#             print("Dialog container not found in page.overlay ({title})")            
#         page.on_keyboard_event = None
#         page.update()
#         if on_close:
#             print("Running on_close")            
#             on_close(e)

#     # ESC key closes dialog
#     def handle_keypress(e: ft.KeyboardEvent):
#         if e.key == "Escape":
#             dismiss(e)

#     page.on_keyboard_event = handle_keypress

#     title_bar = ft.Container(
#         bgcolor="#808000",
#         padding=10,
#         content=ft.Row([
#             ft.Text(title, size=22, weight="bold", color=ft.colors.ON_PRIMARY),
#             ft.IconButton(icon=ft.icons.CLOSE, tooltip="Close", on_click=dismiss)
#         ], alignment="spaceBetween")
#     )

#     content_body = ft.Container(
#         content=ft.Column(content_controls, spacing=14),
#         padding=20
#     )

#     card = ft.Card(content=ft.Column([title_bar, content_body], spacing=0))

#     print(f"id(dialog_container) {id(dialog_container)}")
#     dialog = dialog_container = ft.Container(
#         content=card,
#         bgcolor=ft.colors.with_opacity("#dbe5cb", 0.95),  # Light olive background
#         alignment=ft.alignment.center,
#         padding=30,
#         expand=True
#     )
#     print(f"id(dialog_container) {id(dialog_container)} and {dialog_container in page.overlay} (dialog_container in page.overlay)")    

#     page.dialog = dialog
#     dialog.open = True

#     page.overlay.append(dialog_container)
#     print(f"len(page.overlay): {len(page.overlay)}")
#     page.update()

def create_dialog(title: str, content_controls: list, page: ft.Page, on_close=None):
    # Create the dialog content
    dialog_card = ft.Card(
        content=ft.Column([
            ft.Container(
                content=ft.Row([
                    ft.Text(title, size=20, weight="bold"),
                    ft.IconButton(
                        icon=ft.icons.CLOSE,
                        tooltip="Close",
                        on_click=lambda e: close_dialog()
                    )
                ], alignment="spaceBetween"),
                bgcolor=ft.colors.AMBER,
                padding=10
            ),
            ft.Container(
                content=ft.Column(content_controls, spacing=10),
                padding=20
            ),
        ])
    )

    # Dialog container
    dialog = ft.Container(
        content=dialog_card,
        bgcolor=ft.colors.with_opacity(0.95, ft.colors.BLACK),
        alignment=ft.alignment.center,
        padding=30,
        expand=True,
        visible=True,
    )

    # Add to page if not already
    if dialog not in page.overlay:
        page.overlay.append(dialog)

    page.dialog = dialog
    page.update()

    def close_dialog():
        print("Overlay before:", len(page.overlay))            
        dialog.visible = False
        page.update()  # Make sure visibility change gets rendered first
        print("Overlay after 1:", len(page.overlay))
        if dialog in page.overlay:
            page.overlay.remove(dialog)
        print("Overlay after 2:", len(page.overlay))
        page.dialog = None
        page.on_keyboard_event = None
        if on_close:
            on_close()
        print("Overlay after 3:", len(page.overlay))            
       
# def create_dialog(title: str, content_controls: list, page: ft.Page, on_close=None, dialog_id=None):
#     # Create the dialog
#     dialog = ft.AlertDialog(
#         modal=True,
#         title=ft.Text(title),
#         content=ft.Column(content_controls),
#         actions=[
#             ft.TextButton("Close", on_click=close_dialog)
#         ],
#     )

#     # Define the close handler inside
#     def close_dialog(e):
#         print("Trying to close dialog")
#         dialog.open = False
#         page.update()
#         if on_close:
#             on_close()

#     page.dialog = dialog
#     dialog.open = True
#     page.update()    
    
#     # def dismiss(e=None):
#     #     print(f"[Dismiss] Called for dialog: {title}")
#     #     if dialog in page.overlay:
#     #         page.overlay.remove(dialog)
#     #         print("[Dismiss] Dialog removed from overlay.")
#     #     else:
#     #         print("[Dismiss] Dialog not found in overlay.")

#     #     # Detach GUI handler if an ID is provided
#     #     if dialog_id:
#     #         print(f"[Dismiss] Detaching GUI handler for {dialog_id}")
#     #         detach_gui_handler(dialog_id)

#     #     page.on_keyboard_event = None
#     #     page.update()

#     #     if on_close:
#     #         print("[Dismiss] Running on_close callback.")
#     #         on_close(e)

#     # def handle_keypress(e: ft.KeyboardEvent):
#     #     if e.key == "Escape":
#     #         dismiss(e)

#     # page.on_keyboard_event = handle_keypress

#     # title_bar = ft.Container(
#     #     bgcolor="#808000",
#     #     padding=10,
#     #     content=ft.Row([
#     #         ft.Text(title, size=22, weight="bold", color=ft.colors.ON_PRIMARY),
#     #         ft.IconButton(icon=ft.icons.CLOSE, tooltip="Close", on_click=dismiss)
#     #     ], alignment="spaceBetween")
#     # )

#     # content_body = ft.Container(
#     #     content=ft.Column(content_controls, spacing=14),
#     #     padding=20
#     # )

#     # card = ft.Card(content=ft.Column([title_bar, content_body], spacing=0))

#     # dialog = ft.Container(
#     #     content=card,
#     #     bgcolor=ft.colors.with_opacity("#dbe5cb", 0.95),
#     #     alignment=ft.alignment.center,
#     #     padding=30,
#     #     expand=True
#     # )

#     # # Remove previous dialog if any
#     # if page.dialog and page.dialog in page.overlay:
#     #     print("[Dialog] Removing previous dialog from overlay.")
#     #     page.overlay.remove(page.dialog)

#     # page.dialog = dialog
#     # page.overlay.append(dialog)
#     # page.update()

#     # # Attach GUI handler if an ID is provided
#     # if dialog_id:
#     #     print(f"[Dialog] Attaching GUI handler for {dialog_id}")
#     #     attach_gui_handler(dialog_id)


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

