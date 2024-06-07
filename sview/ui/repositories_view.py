import flet as ft
from .sidebar import Sidebar
from .appbar import SpecViewAppBar

import sview
import sview.model


class RepositoriesView(ft.View):

    def __init__(self, route, app, page: ft.Page, *args, **kwargs):
        super().__init__(route, *args, **kwargs)
        self.route = route
        self.app = app
        self.page = page

        self.sidebar = Sidebar(page)
        self.appbar = SpecViewAppBar(page, "Repositories" )
        self.rlist = RepositoriesList(self, page)
        self.controls = [
            ft.FloatingActionButton(icon=ft.icons.ADD, on_click=self.add_clicked),
            ft.Row(controls=[
                self.sidebar,
                self.rlist
            ], expand=True)
        ]

        self.bgcolor = ft.colors.LIGHT_BLUE
        self.window_bgcolor = ft.colors.LIGHT_BLUE
        self.expand = True

    def add_clicked(self, button):
        self.page.go("/repositories/new")


class RepositoriesList(ft.Container):

    def __init__(self, view, page: ft.Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.view = view
        self.page = page
        c = []

        for repo_path in sview.svconfig.repo_paths:
            c.append(RepositoryPane(page, sview.svconfig.repositories[repo_path]))
        # self.controls.append(ft.Column(controls=c))
        self.content = ft.Column(controls=c)
        self.border = ft.border.all(1, ft.colors.BLACK)
        self.border_radius = ft.border_radius.all(10)
        self.padding = 10
        self.bgcolor = ft.colors.LIGHT_BLUE
        self.window_bgcolor = ft.colors.LIGHT_BLUE
        self.expand = True


class RepositoryPane(ft.Container):

    def __init__(self, page, repo: sview.model.Repository, *args, **kwargs):
        self.page = page
        self.repo = repo
        super().__init__(*args, **kwargs)
        self.content = ft.Row([
            ft.Container(
                content=ft.Text(repo.name, color=ft.colors.BLACK),
                on_click=lambda e: self.goto_repository(repo)
            )
        ])
        self.border = ft.border.all(1, ft.colors.BLACK38)
        self.border_radius = ft.border_radius.all(5)
        self.bgcolor = ft.colors.WHITE60
        self.padding = ft.padding.all(10)
        self.expand = True
        self.expand_loose = True

    def goto_repository(self, repo_name):
        sview.svconfig.set_current_repository(repo_name)
        self.page.go("/")