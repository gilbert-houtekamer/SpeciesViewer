import flet as ft


class RepositoriesView(ft.View):

    def __init__(self, page: ft.Page):
        self.page = page
        self.r = "/repositories"
        self.c = [
            ft.AppBar(title=ft.Text("Repositories")),
            ft.ElevatedButton("New", on_click=self.new_repository),
        ]
        super().__init__(self.r, self.c)

    def new_repository(self, e):
        self.page.go("/repositories/new")
