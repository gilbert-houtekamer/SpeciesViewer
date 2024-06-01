import flet as ft


class Sidebar(ft.Container):

    def __init__(self, app_layout, page: ft.Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_layout = app_layout
        self.page = page
        c = [
            ft.ElevatedButton("Sidebar1", on_click=self.new_repository),
            ft.ElevatedButton("Sidebar2", on_click=self.new_repository),
            ft.ElevatedButton("Sidebar3", on_click=self.new_repository),
            ft.ElevatedButton("Sidebar4", on_click=self.new_repository),
        ]
        self.content = ft.Column(controls=c)
        self.border = ft.border.all(1, ft.colors.BLACK)
        self.border_radius = ft.border_radius.all(10)
        self.bgcolor = ft.colors.RED
        self.padding = 10
        # self.expand = True

    def new_repository(self, e):
        pass
