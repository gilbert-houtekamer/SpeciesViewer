import flet as ft

class MainView(ft.View):

    def __init__(self, page: ft.Page):
        self.page = page
        self.r = "/"
        self.c = [
            ft.AppBar(title=ft.Text("Flet app")),
            ft.ElevatedButton("Go to settings", on_click=self.open_settings),
        ]
        super().__init__(self.r, self.c)

    def open_settings(self, e):
        self.page.go("/settings")
