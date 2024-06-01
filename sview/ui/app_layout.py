import flet as ft

from .repositories_list import RepositoriesList
from .sidebar import Sidebar


class AppLayout(ft.Row):

    def __init__(self, app, page: ft.Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = app
        self.page = page
        self.sidebar = Sidebar(self, page)
        self.rlist = RepositoriesList(page)
        self.controls = [
            self.sidebar,
            self.rlist
        ]
        self.expand = True
