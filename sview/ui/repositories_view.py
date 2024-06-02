import flet as ft
from .sidebar import Sidebar
from .repositories_list import RepositoriesList


class RepositoriesView(ft.View):

    def __init__(self, route, app, page: ft.Page, *args, **kwargs):
        super().__init__(route, *args, **kwargs)
        self.route = route
        self.app = app
        self.page = page
        self.sidebar = Sidebar(self, page)
        self.rlist = RepositoriesList(self, page)
        self.bgcolor = ft.colors.WHITE
        self.window_bgcolor = ft.colors.WHITE10
        self.controls = [
            ft.Row(controls=[
                self.sidebar,
                self.rlist
            ], expand=True)
        ]
        self.expand = True
