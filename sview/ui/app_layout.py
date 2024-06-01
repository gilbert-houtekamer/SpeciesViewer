import flet as ft

from .repositories_view import RepositoriesView
from .sidebar import Sidebar


class AppLayout(ft.Row):

    def __init__(self, app, page: ft.Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = app
        self.page = page
        self.sidebar = Sidebar(self, page)
        self.active_view = RepositoriesView(self, page)
        self.controls = [
            self.sidebar,
            self.active_view
        ]
