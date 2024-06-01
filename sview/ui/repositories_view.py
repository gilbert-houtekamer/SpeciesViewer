import flet as ft
from .sidebar import Sidebar
from .repositories_list import RepositoriesList


class RepositoriesView(ft.View):

    def __init__(self, page: ft.Page, *args, **kwargs):
        self.page = page
        self.r = "/repositories"
        super().__init__(self.r, *args, **kwargs)
        self.appbar = ft.AppBar(title=ft.Text("Repositories"))
        self.controls.append(
            ft.Column(controls=[
                ft.Container(content=
                             ft.Row(spacing=0, controls=[
                                 Sidebar(page),
                                 RepositoriesList(page)
                             ], expand=True))
            ]))
        self.width = 200
        self.height = 200
        self.padding = 10
        self.spacing = 10
        self.bgcolor = ft.colors.WHITE
        self.window_bgcolor = ft.colors.WHITE10
        self.expand = True
        page.on_resize = self.on_resize

    def new_repository(self, e):
        self.page.go("/repositories/new")

    def on_resize(self, e: ft.ControlEvent):
        pass
