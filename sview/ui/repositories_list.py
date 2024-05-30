import flet as ft
import sview


class RepositoriesList(ft.Column):

    def __init__(self, page: ft.Page, *args, **kwargs):
        self.page = page
        super().__init__(*args, **kwargs)
        
        print(sview.svconfig)
        self.controls.append(ft.ElevatedButton("RList"
                                               "", on_click=self.new_repository))
        self.controls.append(ft.ElevatedButton("right"
                                               "", on_click=self.new_repository))

    def new_repository(self, e):
        self.page.go("/repositories/new")
