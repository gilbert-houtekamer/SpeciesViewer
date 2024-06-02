import flet as ft
from .sidebar import Sidebar
from .repositories_list import RepositoriesList


class RepositoriesView(ft.View):

    def __init__(self, route, app, page: ft.Page, *args, **kwargs):
        super().__init__(route, *args, **kwargs)
        self.route = route
        self.app = app
        self.page = page
        self.sidebar = Sidebar(self, page)
        self.rlist = RepositoriesList(self, page)
        self.bgcolor = ft.colors.WHITE
        self.window_bgcolor = ft.colors.WHITE10
        self.controls = [
            ft.Row(controls=[
                self.sidebar,
                self.rlist
            ], expand=True)
        ]
        self.expand = True

        appbar_items = [
            ft.PopupMenuItem(text="Login"),
            ft.PopupMenuItem(),  # divider
            ft.PopupMenuItem(text="Settings")
        ]

        self.appbar = ft.AppBar(
            leading=ft.Icon(ft.icons.GRID_GOLDENRATIO_ROUNDED),
            leading_width=100,
            title=ft.Text("Repositories",size=32, text_align=ft.TextAlign.START),
            center_title=False,
            toolbar_height=75,
            bgcolor=ft.colors.LIGHT_BLUE_ACCENT_700,
            actions=[
                ft.Container(
                    content=ft.PopupMenuButton(
                        items=appbar_items
                    ),
                    margin=ft.margin.only(left=50, right=25)
                )
            ],
        )

