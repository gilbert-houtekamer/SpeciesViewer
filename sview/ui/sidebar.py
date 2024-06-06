import flet as ft


class Sidebar(ft.Container):

    def __init__(self, page: ft.Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page = page
        c = [
            ft.Text("Info 1"),
            ft.Text("Info 2"),
            ft.Text("Info 3"),
            ft.Text("Info 4"),
        ]
        self.content = ft.Column(controls=c)
        self.border = ft.border.all(1, ft.colors.BLACK)
        self.border_radius = ft.border_radius.all(10)
        self.bgcolor = ft.colors.RED
        self.padding = 10
        # self.expand = True

    def new_repository(self, e):
        pass
