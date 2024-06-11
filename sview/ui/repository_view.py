import flet as ft
from .sidebar import Sidebar
from .appbar import SpecViewAppBar

import sview
import sview.model


class RepositoryView(ft.View):

    def __init__(self, route, app, page: ft.Page, *args, **kwargs):
        super().__init__(route, *args, **kwargs)
        self.route = route
        self.app = app
        self.page = page

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

        self.content = ft.Row( [
            ft.Container(
                content=ft.Text(self.repo.name, color=ft.colors.BLACK),
            )])
