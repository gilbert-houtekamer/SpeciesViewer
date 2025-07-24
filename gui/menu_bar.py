import flet as ft
from gui.dialogs import show_about_dialog, show_photo_sorter_overlay
from gui.dialogs import show_create_trip_dialog
from gui.dialogs import show_global_settings_dialog, show_repositories_dialog


def build_menu(page, update_status_callback, add_to_global_log, config):
    return ft.Row([
        ft.PopupMenuButton(
            icon=ft.icons.MENU,
            items=[ft.PopupMenuItem(text="Exit", on_click=lambda _: page.window_close())],
            tooltip="File"
        ),
        ft.PopupMenuButton(
            icon=ft.icons.BUILD,
            items=[
                ft.PopupMenuItem(text="Create Trip", on_click=lambda e: show_create_trip_dialog(page, config))
            ],
            tooltip="Trip"
        ),
        ft.PopupMenuButton(
            icon=ft.icons.BUILD,
            items=[
                ft.PopupMenuItem(text="Photo Sorter", on_click=lambda _: show_photo_sorter_overlay(page, update_status_callback, add_to_global_log))
            ],
            tooltip="Utility"
        ),
        ft.PopupMenuButton(
            icon=ft.icons.SETTINGS,
            items=[
                ft.PopupMenuItem(text="Global Settings", on_click=lambda _: show_global_settings_dialog(page, config)),
                ft.PopupMenuItem(text="Repositories", on_click=lambda _: show_repositories_dialog(page, config))
            ],
            tooltip="Settings"
        ),        
        ft.PopupMenuButton(
            icon=ft.icons.HELP_OUTLINE,
            items=[ft.PopupMenuItem(text="About", on_click=lambda _: show_about_dialog(page))],
            tooltip="Help"
        ),
    ])
