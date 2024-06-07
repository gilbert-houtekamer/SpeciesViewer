import flet as ft


class SpecViewAppBar(ft.AppBar):

    def __init__(self, page, title, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page = page
        appbar_items = [
            ft.PopupMenuItem(text="Repositories", on_click=self.goto_repositories),
            ft.PopupMenuItem(),  # divider
            ft.PopupMenuItem(text="Settings")
        ]

        self.leading_width = 100,
        self.title = ft.Text(title, size=32, text_align=ft.TextAlign.START)
        self.center_title = False
        self.toolbar_height = 75
        self.bgcolor = ft.colors.LIGHT_BLUE_ACCENT_700
        self.actions = [
            ft.ElevatedButton(
                "Repositories",
                on_click=self.goto_repositories,
                bgcolor=ft.colors.BLUE,
                color=ft.colors.WHITE
            ),
            ft.Container(
                content=ft.PopupMenuButton(
                    items=appbar_items
                ),
                margin=ft.margin.only(left=50, right=25)
            )
        ]
        self.leading = ft.Icon(ft.icons.GRID_GOLDENRATIO_ROUNDED)

    def goto_repositories(self, e):
        self.page.go("/repositories")
