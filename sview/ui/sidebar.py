import flet as ft


class Sidebar(ft.UserControl):

    def __init__(self, app_layout, page):
        super().__init__()
        self.view = None
        self.app_layout = app_layout
        self.page = page
        self.top_nav_items = [
            ft.NavigationRailDestination(
                label_content=ft.Text("Boards"),
                label="Boards",
                icon=ft.icons.BOOK_OUTLINED,
                selected_icon=ft.icons.BOOK_OUTLINED
            ),
            ft.NavigationRailDestination(
                label_content=ft.Text("Members"),
                label="Members",
                icon=ft.icons.PERSON,
                selected_icon=ft.icons.PERSON
            ),

        ]
        self.top_nav_rail = ft.NavigationRail(
            selected_index=None,
            label_type=ft.NavigationRailLabelType.ALL,
            on_change=self.top_nav_change,
            destinations=self.top_nav_items,
            bgcolor=ft.colors.BLUE_GREY,
            extended=True,
            expand=True
        )

    def build(self):
        self.view = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("Workspace"),
                ]),
                # divider
                ft.Container(
                    bgcolor=ft.colors.BLACK26,
                    border_radius=ft.border_radius.all(30),
                    height=1,
                    alignment=ft.alignment.center_right,
                    width=220
                ),
                self.top_nav_rail,
                # divider
                ft.Container(
                    bgcolor=ft.colors.BLACK26,
                    border_radius=ft.border_radius.all(30),
                    height=1,
                    alignment=ft.alignment.center_right,
                    width=220
                ),
            ], tight=True),
            padding=ft.padding.all(15),
            margin=ft.margin.all(0),
            width=250,
            bgcolor=ft.colors.BLUE_GREY,
        )
        return self.view

    def top_nav_change(self, e):
        self.top_nav_rail.selected_index = e.control.selected_index
        self.update()
