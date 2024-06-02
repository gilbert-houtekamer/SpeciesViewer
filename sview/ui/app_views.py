import flet as ft
import sview
import sview.model
import sview.ui.app_layout
from .repositories_view import RepositoriesView


class SpecViewApp:

    def __init__(self):
        self.repositories_view: ft.View

    def run(self, config: sview.model.SVConfig = None):
        sview.svconfig = config
        ft.app(target=self.main)

    def main(self, page: ft.Page):
        page.title = "Spieces Viewer"

        def route_change(route):
            page.views.clear()
            page.views.append(
                RepositoriesView("/", self, page)
            )
            page.update()

        def view_pop(view):
            page.views.pop()
            top_view = page.views[-1]
            page.go(top_view.route)

        page.on_route_change = route_change
        page.on_view_pop = view_pop
        page.go(page.route)

        page.padding = 0
        page.bgcolor = ft.colors.BLUE_GREY_200
        page.appbar = ft.AppBar(
            title=ft.Text("Repositories"),
            actions=[
                ft.IconButton(ft.icons.WB_SUNNY_OUTLINED)
            ]
        )
