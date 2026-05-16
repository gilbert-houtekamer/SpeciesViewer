import flet as ft
import sview
import sview.model
import sview.ui.app_layout
from .app_layout import AppLayout


class SpecViewApp:

    def __init__(self):
        self.repositories_view: ft.View

    def run(self, config: sview.model.SVConfig = None):
        sview.svconfig = config
        ft.app(target=self.main)

    def main(self, page: ft.Page):
        page.title = "Spieces Viewer"
        page.padding = 0
        page.bgcolor = ft.colors.BLUE_GREY_200
        page.appbar = ft.AppBar(
            title=ft.Text("Repositories"),
            actions=[
                ft.IconButton(ft.icons.WB_SUNNY_OUTLINED)
            ]
        )
        page.add(AppLayout(self, page))
        page.update()
