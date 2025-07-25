import configparser
import logging
import importlib
from pathlib import Path
import flet as ft
from .sidebar import Sidebar
from .appbar import SpecViewAppBar
from .logging_pane import LoggingPane

import sview
import sview.model
from ..model import Repository

logger = logging.getLogger("speciesviewer")


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
        if sview.svconfig.get_current_repository() is None:
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

    def __init__(self, view, page, repo: Repository, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.view = view
        self.page = page
        self.repo = repo

        if self.repo.get_last_project() is not None:
            self.project_path = self.repo.get_last_project()
        else:
            self.project_path = sview.svconfig.path

        self.select_dir_dialog = ft.FilePicker(
            on_result=self.dir_selected,
        )
        self.select_dir_dialog.initial_directory=str(self.project_path)
        self.page.overlay.append(self.select_dir_dialog)
        self.selected_dir = ft.Text(str(self.project_path))
        self.view.path = self.project_path

        self.loggingpane = LoggingPane(self.page, sview.loghandler)

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
                    ft.OutlinedButton(text="ReadDirExif", on_click=lambda e: self.do_module('bin.sv2ReadDirExif')),
                    ft.OutlinedButton(text="WritePictures", on_click=lambda e: self.do_module('bin.sv3WritePictures')),
                    ft.OutlinedButton(text="WriteBlog", on_click=lambda e: self.do_module('bin.sv4WriteBlog')),
                    ft.OutlinedButton(text="BuildWebsite", on_click=lambda e: self.do_module('bin.sv5BuildWebsite')),
                    ft.OutlinedButton(text="Freeze", on_click=lambda e: self.do_module('bin.sv6Freeze')),
                    ft.OutlinedButton(text="WriteCommit", on_click=lambda e: self.do_module('bin.sv7WriteCommit')),
                ]),
                self.loggingpane
            ], expand=True)
        ])
        self.expand = True
        self.bgcolor = ft.colors.BLUE

    def dir_selected(self, e: ft.FilePickerResultEvent):
        if e.path is not None:
            path = Path(e.path)
            self.repo.set_last_project(path)
            self.view.path = path
            self.selected_dir.value = e.path
            self.selected_dir.update()

    def do_module(self, module_name):
        try:
            logger.info(f"starting module {module_name}")
            config = self.prepare_config()
            module = importlib.import_module(module_name)
            main = getattr(module, "main")
            main(None, config)
            logger.info(f"success module {module_name}")
        except Exception as e:
            logger.warning(f"Error executing module {module_name}: {e}")


    def prepare_config(self):
        #
        config = self.repo.config

        # set the configuration from our UI info (we will integrate that later)
        config.set('repository', 'BiotaDatabase', str(self.repo.biota_database_path) + '/')
        config.set('repository', 'BiotaWebRoot',  self.repo.biota_webroot_path + '/')
        config.set('project', 'PictureDirectory', str(self.view.path) + '/')

        return config

