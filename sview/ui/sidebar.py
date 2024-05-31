import flet as ft


class Sidebar(ft.Container):

    def __init__(self, page: ft.Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page = page
        self.content = ft.Column(controls=[
            ft.ElevatedButton("Sidebar", on_click=self.new_repository)
        ])
        self.border = ft.border.all(1, ft.colors.BLACK)
        self.border_radius = ft.border_radius.all(10)
        self.bgcolor = ft.colors.GREEN_200
        self.padding = 10
        self.expand = True
        self.expand_loose = True

    def new_repository(self, e):
        self.page.go("/repositories/new")
