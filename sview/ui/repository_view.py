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

        self.sidebar = Sidebar(self, page)
        self.appbar = SpecViewAppBar(app, page)
        self.controls = [
            ft.Row(controls=[
                self.sidebar,
            ], expand=True)
        ]

        self.bgcolor = ft.colors.WHITE
        self.window_bgcolor = ft.colors.WHITE10
        self.expand = True

