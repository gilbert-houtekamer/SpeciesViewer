import flet as ft
from .sidebar import Sidebar
from .repositories_list import RepositoriesList


class RepositoriesView(ft.View):

    def __init__(self, page: ft.Page, *args, **kwargs):
        self.page = page
        self.r = "/repositories"
        super().__init__(self.r, *args, **kwargs)
        self.appbar = ft.AppBar(title=ft.Text("Repositories"))
        self.controls.append(ft.Row(spacing=0, controls=[
            Sidebar(page),
            RepositoriesList(page),
            # ft.Column(spacing=0, controls=[
            #     ft.Row(spacing=0, controls=[
            #         ft.ElevatedButton("other", on_click=self.new_repository),
            #         ft.ElevatedButton("new", on_click=self.new_repository)
            #     ]),
            #     RepositoriesList(page),
            # ]),
        ]))
        self.padding=10
        self.spacing=10
        self.bgcolor=ft.colors.WHITE

    def new_repository(self, e):
        self.page.go("/repositories/new")
