import flet as ft

from .repositories_list import RepositoriesList


class RepositoriesView(ft.View):

    def __init__(self, app_layout, page: ft.Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_layout = app_layout
        self.page = page
        # self.appbar = ft.AppBar(title=ft.Text("Repositories"))
        self.controls.append(RepositoriesList(page))
        self.bgcolor = ft.colors.WHITE
        self.window_bgcolor = ft.colors.WHITE10
        self.expand = True
