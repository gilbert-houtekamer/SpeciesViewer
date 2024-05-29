import flet as ft
import configparser
from .mainview import MainView
from .settingsview import SettingsView
from .mailsettingsview import  MailSettingsView


class FrontPage:

    def __init__(self):
        self.page = None
        self.mainview = None
        self.settingsview = None
        self.mailsettingsview = None

    # function run(self, configparser.ConfigParser config = None):
    def run(self, config: configparser.ConfigParser = None):
        ft.app(target=self.main)

    def main(self, page: ft.Page):
        # initialise
        self.page = page
        self.mainview = MainView(page)
        self.settingsview = SettingsView(page)
        self.mailsettingsview = MailSettingsView(page)

        page.title = "Spieces Viewer"
        page.on_route_change = self.route_change
        page.on_view_pop = self.view_pop
        page.go(page.route)


    def route_change(self, e):
        self.page.views.clear()
        self.page.views.append(self.mainview)
        if self.page.route == "/settings" or self.page.route == "/settings/mail":
            self.page.views.append(self.settingsview)
        if self.page.route == "/settings/mail":
            self.page.views.append(self.mailsettingsview)
        self.page.update()


    def view_pop(self, e):
        self.page.views.pop()
        top_view = self.page.views[-1]
        self.page.go(top_view.route)



