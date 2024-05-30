import flet as ft
from .sidebar_view import SidebarView
from .repositories_list import RepositoriesList


class RepositoriesView(ft.View):

    def __init__(self, page: ft.Page):
        self.page = page
        self.r = "/repositories"
        super().__init__(self.r)        
        self.appbar = ft.AppBar(title=ft.Text("Repositories"))
        self.controls.append(ft.Row(spacing=0, controls=[
            SidebarView(page),
            ft.Column(spacing=0, controls=[
                ft.Row(spacing=0, controls=[
                    ft.ElevatedButton("other", on_click=self.new_repository),
                    ft.ElevatedButton("new", on_click=self.new_repository)
                ]),
                RepositoriesList(page),
            ]),
        ]))

    def new_repository(self, e):
        self.page.go("/repositories/new")
