import flet as ft
from gui.dialogs import show_about_dialog, show_photo_sorter_dialog, show_photo_sorter_overlay

def build_menu(page, update_status_callback, add_to_global_log):
    return ft.Row([
        ft.PopupMenuButton(
            icon=ft.icons.MENU,
            items=[ft.PopupMenuItem(text="Exit", on_click=lambda _: page.window_close())],
            tooltip="File"
        ),
        ft.PopupMenuButton(
            icon=ft.icons.BUILD,
            items=[
                ft.PopupMenuItem(text="Photo Sorter", on_click=lambda _: show_photo_sorter_overlay(page, update_status_callback, add_to_global_log))
            ],
            tooltip="Utility"
        ),
        ft.PopupMenuButton(
            icon=ft.icons.HELP_OUTLINE,
            items=[ft.PopupMenuItem(text="About", on_click=lambda _: show_about_dialog(page))],
            tooltip="Help"
        ),
    ])
