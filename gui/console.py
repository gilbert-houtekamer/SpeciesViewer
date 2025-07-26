import flet as ft
import os
import asyncio
import logging
from gui.menu_bar import build_menu
from gui.logger_logic import pause_toggle, GUIHandler, setup_logger, attach_gui_handler, detach_gui_handler
from utils.config import get_setting, set_setting, get_int_setting
from utils.config import config         # assuming config is loaded here
from gui.components import create_dialog, make_integer_field, validate_integer, directory_field
from utils.log_utils import make_log_tail_accessor
from PyQt5.QtWidgets import QApplication
import sys

global_log_box = ft.TextField(
    label="Global batch log",
    multiline=True,
    read_only=True,
    min_lines=40,
    max_lines=40,
    width=1500,
    text_style=ft.TextStyle(size=11, font_family="Courier New")
)

get_global_log_lines = make_log_tail_accessor(global_log_box)

async def auto_refresh(widget, page, interval=0.25):
    while widget.visible:
        widget.refresh()
        await page.update_async()
        await asyncio.sleep(interval)

def launch_main_ui(page):
    # rest of your UI setup...    
    window_width = get_int_setting("Global", "default_window_width", 1200)
    window_height = get_int_setting("Global", "default_window_height", 900)

    page.window_width = window_width
    page.window_height = window_height
    status_bar = ft.Text(value="Ready.", color=ft.colors.ON_SURFACE_VARIANT, size=14, width=400)

    src_folder = get_setting("PhotoSorter", "last_source_folder", fallback="")
    set_setting("Global", "splash_enabled", "yes")

    # maybe later
    # severity_dropdown = ft.Dropdown(
    #     label="Log Level Filter",
    #     width=150,
    #     options=[
    #         ft.dropdown.Option("DEBUG"),
    #         ft.dropdown.Option("INFO"),
    #         ft.dropdown.Option("WARNING"),
    #         ft.dropdown.Option("ERROR")
    #     ],
    #     value="DEBUG",
    #     on_change=lambda e: filter_logs()
    # )

    def add_to_global_log(msg):
        logger.info(msg)
        page.update()

    def clear_log():
        global_log_box.controls.clear()
        global_log_box.update()

    def save_log():
        with open("global_log.txt", "w", encoding="utf-8") as f:
            for entry in global_log_box.controls:
                if isinstance(entry, ft.Text):
                    f.write(entry.value + "\n")
        update_status("✅ Log saved to global_log.txt.")

    def toggle_log():
        if pause_toggle == False:
            pause_toggle = True
        else:
            pause_toggle = False            

    def update_status(message):
        status_bar.value = message
        page.update()

    # def filter_logs():
    #     selected_level = severity_dropdown.value

    #     # Map levels to numeric values for easy comparison
    #     level_map = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
    #     selected_levelno = level_map.get(selected_level, 10)

    #     global_log_box.controls = [
    #         entry for entry in all_log_entries
    #         if entry.levelno >= selected_levelno
    #     ]
    #     global_log_box.update()        

    main_content = ft.Column([
        build_menu(page, update_status, add_to_global_log, config),
        ft.Divider(),
        ft.Text("Welcome to Species Viewer", size=24),
        global_log_box,
        ft.Row([
            pause_toggle,
            ft.TextButton("🗑️ Clear Log", on_click=lambda e: clear_log()),
            ft.TextButton("💾 Save Log", on_click=lambda e: save_log()),
        ], alignment='start', spacing=20),              
        # You can insert splash or other placeholder sections here
    ], expand=True)

    page.add(
        ft.Column([
            main_content,     # fills available space
            status_bar        # anchored at bottom
        ], expand=True)
    )

    # Add to page first
    page.controls.append(global_log_box)
    page.update()

    # Setup logger with GUI handler now that control is attached
    logger = setup_logger(global_log_box)
    logger.info("Species Viewer loading...")
    

def splash_screen(page):
# Enlarged window size for rich visuals
    page.window_width = 1600
    page.window_height = 1200    

    splash_image = ft.Image(
        src="assets/svsplash.jpg",
        width=1200,
        height=900,
        opacity=0.0,
        animate_opacity=500  # fade in
    )

    splash_text = ft.Text(
        "Welcome to Species Viewer",
        size=28,
        weight=ft.FontWeight.BOLD,
        color=ft.colors.ON_SURFACE_VARIANT
    )

    splash_subtext = ft.Text(
        "Organize species. Explore collections. Bring biodiversity to life.",
        size=18,
        italic=True,
        color=ft.colors.ON_SURFACE_VARIANT
    )

    splash_column = ft.Column(
        controls=[
            splash_image,
            splash_text,
            splash_subtext
        ],
        alignment="center",
        horizontal_alignment="center",
        spacing=20
    )

    splash_container = ft.Container(
        content=splash_column,
        alignment=ft.alignment.center,
        expand=True,
        bgcolor=ft.colors.SURFACE_VARIANT
    )

    page.add(splash_container)
    page.update()

    async def proceed():
        splash_image.opacity = 1.0
        page.update()
        await asyncio.sleep(1.5)  # splash delay
        page.controls.clear()
        page.update()
        launch_main_ui(page)

    page.run_task(proceed)


def main(page: ft.Page):
    page.title = "Species Viewer"
    page.window_width = 1600
    page.window_height = 1200
    splash_screen(page)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    ft.app(target=main)


