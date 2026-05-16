import flet as ft
from gui.dialogs import show_my_dialog, show_about_dialog, show_photo_sorter_overlay
from gui.dialogs import show_global_settings_dialog, show_repositories_dialog

from gui.dialogs import show_create_trip_dialog, show_read_dir_dialog, show_write_pictures_dialog, show_write_blog_dialog
from gui.dialogs import show_build_website_dialog, show_freeze_dialog, show_commit_dialog



def build_menu(page, dialog_manager, update_status_callback, add_to_global_log, config):
    return ft.Row([
        ft.PopupMenuButton(
            icon=ft.icons.MENU,
            items=[ft.PopupMenuItem(text="Exit", on_click=lambda _: page.window_close())],
            tooltip="File"
        ),
        ft.PopupMenuButton(
            icon=ft.icons.PARK,
            items=[
                ft.PopupMenuItem(text="Create Trip", on_click=lambda e: show_create_trip_dialog(page, config, update_status_callback)),
                ft.PopupMenuItem(text="Read Directory", on_click=lambda e: show_read_dir_dialog(page, config, update_status_callback)),
                ft.PopupMenuItem(text="Write Summary", on_click=lambda e: show_write_pictures_dialog(page, config, update_status_callback)),
                ft.PopupMenuItem(text="Create Blog", on_click=lambda e: show_write_blog_dialog(page, config, update_status_callback)),
                ft.PopupMenuItem(text="Build Website", on_click=lambda e: show_build_website_dialog(page, config, update_status_callback)),
                ft.PopupMenuItem(text="Freeze Data", on_click=lambda e: show_freeze_dialog(page, config, update_status_callback)),
                ft.PopupMenuItem(text="Commit Website", on_click=lambda e: show_commit_dialog(page, config, update_status_callback))
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
                ft.PopupMenuItem(text="Global Settings", on_click=lambda _: show_global_settings_dialog(page, config, dialog_manager)),
                ft.PopupMenuItem(text="Repositories", on_click=lambda _: show_repositories_dialog(page, config, dialog_manager))
            ],
            tooltip="Settings"
        ),        
        ft.PopupMenuButton(
            icon=ft.icons.HELP_OUTLINE,
            items=[
                ft.PopupMenuItem(text="Test", on_click=lambda _: show_my_dialog(page, config, dialog_manager)),
                ft.PopupMenuItem(text="About", on_click=lambda _: show_about_dialog(page, config, dialog_manager))],
            tooltip="Help"
        ),
    ])
