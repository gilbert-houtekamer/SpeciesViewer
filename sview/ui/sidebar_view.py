import flet as ft


class SidebarView(ft.Column):

    def __init__(self, page: ft.Page, *args, **kwargs):
        self.page = page
        super().__init__(*args, **kwargs)
        self.controls.append(ft.ElevatedButton("Sidebar", on_click=self.new_repository))

    def new_repository(self, e):
        self.page.go("/repositories/new")
