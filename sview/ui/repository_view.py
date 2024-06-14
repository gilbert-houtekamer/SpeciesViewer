import logging
from pathlib import Path
import flet as ft
from .sidebar import Sidebar
from .appbar import SpecViewAppBar
from .logging_pane import LoggingPane

import sview
import sview.model

import sv2ReadDirExif

logger = logging.getLogger(__name__)


class RepositoryView(ft.View):

    def __init__(self, route, app, page: ft.Page, *args, **kwargs):
        super().__init__(route, *args, **kwargs)
        self.route = route
        self.app = app
        self.page = page
        self.path = None

        self.sidebar = Sidebar(page)
        c = [
            self.sidebar
        ]
        if sview.svconfig.get_current_repository() == None:
            title = ""
        else:
            if not sview.svconfig.get_current_repository().validate():
                sview.svconfig.set_current_repository(None)
                title = ""
            else:
                c.append(RepositoryPane(self, page, sview.svconfig.get_current_repository()))
                title = sview.svconfig.get_current_repository().name
        self.appbar = SpecViewAppBar(page, f"Repository: {title}")
        self.controls = [
            ft.Row(controls=c, expand=True)
        ]
        self.bgcolor = ft.colors.LIGHT_BLUE
        self.window_bgcolor = ft.colors.LIGHT_BLUE
        self.expand = True


class RepositoryPane(ft.Container):

    def __init__(self, view, page, repo, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.view = view
        self.page = page
        self.repo = repo

        self.select_dir_dialog = ft.FilePicker(
            on_result=self.dir_selected
        )
        self.page.overlay.append(self.select_dir_dialog)
        self.selected_dir = ft.Text()
        self.loggingpane = LoggingPane(self.page)
        sview.loghandler.clear_listeners()
        sview.loghandler.add_listener(self.loggingpane)

        self.content = ft.Column(controls=[
            ft.Row(
                controls=[
                    ft.ElevatedButton(
                        "Project",
                        icon=ft.icons.FILE_OPEN,
                        on_click=lambda _: self.select_dir_dialog.get_directory_path(),
                        bgcolor=ft.colors.BLUE,
                        color=ft.colors.WHITE,
                    ),
                    self.selected_dir
                ]
            ),
            ft.Row(controls=[
                ft.Column(controls=[
                    ft.OutlinedButton(text="ReadDirExif", on_click=self.do_ReadDirExif),
                    ft.OutlinedButton(text="WritePictures"),
                    ft.OutlinedButton(text="WriteBlog"),
                    ft.OutlinedButton(text="BuildWebsite"),
                    ft.OutlinedButton(text="Freeze"),
                    ft.OutlinedButton(text="WriteCommit"),
                ]),
                self.loggingpane
            ], expand=True)
        ])
        self.expand = True
        self.bgcolor = ft.colors.BLUE

    def dir_selected(self, e: ft.FilePickerResultEvent):
        if e.path is not None:
            self.selected_dir.value = e.path
            self.selected_dir.update()
            self.path = Path(e.path)


    def do_ReadDirExif(self, e):
        sv2ReadDirExif.test()
