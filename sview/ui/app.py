import flet as ft
import sview
import sview.model
from .repositories_view import RepositoriesView


class SpecViewApp:

    def __init__(self):
        # testing
        self.page: ft.Page
        self.repositories_view: ft.View

    def run(self, config: sview.model.SVConfig = None):
        sview.svconfig = config
        ft.app(target=self.main, view=ft.FLET_APP)

    def main(self, page: ft.Page):
        # initialise
        self.page = page
        self.repositories_view = RepositoriesView(page)

        page.title = "Spieces Viewer"
        page.on_route_change = self.route_change
        page.on_view_pop = self.view_pop
        page.go(page.route)

    def route_change(self, e):
        self.page.views.clear()
        self.page.views.append(self.repositories_view)
        # if self.page.route == "/repositories":
        #     self.page.views.append(self.repositories_view)
        # self.page.views.append(self.mainview)
        # if self.page.route == "/settings" or self.page.route == "/settings/mail":
        #     self.page.views.append(self.settingsview)
        # if self.page.route == "/settings/mail":
        #     self.page.views.append(self.mailsettingsview)
        self.page.update()

    def view_pop(self, e):
        self.page.views.pop()
        top_view = self.page.views[-1]
        self.page.go(top_view.route)
