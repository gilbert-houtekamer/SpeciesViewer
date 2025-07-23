import flet as ft
import os
import asyncio
from gui.menu_bar import build_menu
from utils.config import get_setting, set_setting, get_int_setting

global_log_box = ft.TextField(
    value="Welcome to Species Viewer.\n",
    multiline=True,
    read_only=True,
    height=300,
    expand=True
)

def launch_main_ui(page):
    window_width = get_int_setting("Global", "default_window_width", 1200)
    window_height = get_int_setting("Global", "default_window_height", 900)

    page.window_width = window_width
    page.window_height = window_height
    status_bar = ft.Text(value="Ready.", color=ft.colors.ON_SURFACE_VARIANT, size=14, width=400)

    src_folder = get_setting("PhotoSorter", "last_source_folder", fallback="")
    set_setting("Global", "splash_enabled", "yes")


    def add_to_global_log(msg):
        global_log_box.value += msg + "\n"
        page.update()

    def clear_log():
        global_log_box.value = ""
        page.update()

    def save_log():
        with open("global_log.txt", "w", encoding="utf-8") as f:
            f.write(global_log_box.value)
        update_status(f"✅ Log saved to global_log.txt.")    

    def update_status(message):
        status_bar.value = message
        page.update()

    main_content = ft.Column([
        build_menu(page, update_status, add_to_global_log),
        ft.Divider(),
        ft.Text("Welcome to Species Viewer", size=24),
        global_log_box, 
        ft.Row([
            ft.TextButton("🗑️ Clear Log", on_click=lambda e: clear_log()),
            ft.TextButton("💾 Save Log", on_click=lambda e: save_log())
        ], alignment='start', spacing=20),              
        ft.Divider()
        # You can insert splash or other placeholder sections here
    ], expand=True)

    page.add(
        ft.Column([
            main_content,     # fills available space
            status_bar        # anchored at bottom
        ], expand=True)
    )

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
        await asyncio.sleep(2.5)  # splash delay
        page.controls.clear()
        page.update()
        launch_main_ui(page)

    page.run_task(proceed)

def main(page: ft.Page):
    page.title = "Species Viewer"
    page.window_width = 1600
    page.window_height = 1200
    splash_screen(page)

ft.app(target=main)

