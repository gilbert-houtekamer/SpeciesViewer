import flet as ft
from .sidebar import Sidebar
from .repositories_list import RepositoriesList


class RepositoriesView(ft.View):

    def __init__(self, app_layout, page: ft.Page, *args, **kwargs):
        self.app_layout = app_layout
        self.page = page
        self.r = "/repositories"
        super().__init__(self.r, *args, **kwargs)
        self.appbar = ft.AppBar(title=ft.Text("Repositories"))
        self.controls.append(
            ft.Column(controls=[
                ft.Container(content=
                             ft.Row(spacing=0, controls=[
                                 Sidebar(self, page),
                                 RepositoriesList(page)
                             ], expand=True))
            ]))
        self.bgcolor = ft.colors.WHITE
        self.window_bgcolor = ft.colors.WHITE10
        self.expand = True
