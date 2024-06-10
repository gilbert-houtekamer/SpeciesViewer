import os
from pathlib import Path
import flet as ft
from .sidebar import Sidebar
from .appbar import SpecViewAppBar

import sview
import sview.model


class NewRepositoryView(ft.View):

    def __init__(self, route, app, page: ft.Page, *args, **kwargs):
        super().__init__(route, *args, **kwargs)
        self.route = route
        self.app = app
        self.page = page
        self.name = ""
        self.path = None

        self.sidebar = Sidebar(page)
        self.appbar = SpecViewAppBar(page, "New Repository")
        self.select_dir_dialog = ft.FilePicker(on_result=self.dir_selected)
        self.selected_dir = ft.Text()
        self.create_button = ft.ElevatedButton(
            "Create",
            on_click=self.create_repository,
            bgcolor=ft.colors.BLUE,
            color=ft.colors.WHITE,
            disabled=True,
        )
        self.open_button = ft.ElevatedButton(
            "Open",
            on_click=self.open_repository,
            bgcolor=ft.colors.BLUE,
            color=ft.colors.WHITE,
            disabled=True,
        )
        self.cancel_button = ft.ElevatedButton(
            "Cancel",
            on_click=self.cancel,
            bgcolor=ft.colors.BLUE,
            color=ft.colors.WHITE,
        )



        page.overlay.append(self.select_dir_dialog)
        self.controls = [
            ft.Row(controls=[
                self.sidebar,
                ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.TextField(
                                    label="Name",
                                    hint_text="Please enter name here",
                                    on_change=self.name_changed
                                ),
                                ft.ElevatedButton(
                                    "Repo",
                                    icon=ft.icons.FILE_OPEN,
                                    on_click=lambda _: self.select_dir_dialog.get_directory_path(),
                                    bgcolor=ft.colors.BLUE,
                                    color=ft.colors.WHITE,
                                ),
                                self.selected_dir
                            ]
                        ),
                        ft.Row( controls=[
                            self.cancel_button,
                            self.open_button,
                            self.create_button,
                        ])
                    ]
                )
            ], expand=True)
        ]

        self.bgcolor = ft.colors.LIGHT_BLUE
        self.window_bgcolor = ft.colors.LIGHT_BLUE
        self.expand = True

    def name_changed(self, name):
        self.name = name.data
        if self.new_repo_valid():
            self.create_button.disabled = False
        else:
            self.create_button.disabled = True
        self.create_button.update()


    def dir_selected(self, e: ft.FilePickerResultEvent):
        self.selected_dir.value = e.path
        self.selected_dir.update()
        self.path = Path(e.path)
        if self.new_repo_valid():
            self.create_button.disabled = False
        else:
            self.create_button.disabled = True
        self.create_button.update()

    def new_repo_valid(self):
        if self.name is None or self.name == "":
            return False
        if sview.svconfig.has_repository(self.name):
            return False
        with os.scandir(self.path) as it:
            if any(it):
                return False
        return True

    def create_repository(self, e):
        repository = sview.model.Repository(self.path, self.name)
        repository.populate()
        sview.svconfig.add_repository(repository)
        sview.svconfig.write_config()
        self.page.go("/repositories")

    def open_repository(self):
        print("open repo")

    def cancel(self, e):
        self.page.go("/repositories")

