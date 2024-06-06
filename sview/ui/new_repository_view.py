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

        self.sidebar = Sidebar(page)
        self.appbar = SpecViewAppBar(page, "New Repository")
        self.select_dir_dialog = ft.FilePicker(on_result=self.dir_selected)
        self.selected_dir = ft.Text()
        self.create_button = ft.ElevatedButton(
            "Create",
            on_click=self.new_repository,
            disabled=True
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
                                ),
                                self.selected_dir
                            ]
                        ),
                        ft.ElevatedButton(
                            "Create",
                            on_click=self.new_repository,
                            disabled=True
                        ),
                    ]
                )
            ], expand=True)
        ]

        self.bgcolor = ft.colors.WHITE
        self.window_bgcolor = ft.colors.WHITE10
        self.expand = True

    def name_changed(self, name):
        self.name = name

    def dir_selected(self, e: ft.FilePickerResultEvent):
        self.selected_dir.value = e.path
        self.selected_dir.update()


    def new_repository(self):
        print("create repo")

