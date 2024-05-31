import flet as ft


class SidebarView(ft.Container):

    def __init__(self, page: ft.Page, *args, **kwargs):
        self.page = page
        super().__init__(*args, **kwargs)
        self.bgcolor=ft.colors.PINK
        self.content = ft.Column( controls = [
            ft.ElevatedButton("Sidebar", on_click=self.new_repository)
        ])

    def new_repository(self, e):
        self.page.go("/repositories/new")
