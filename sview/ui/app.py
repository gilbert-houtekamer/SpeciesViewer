import flet as ft
import sview
import sview.model
from .repositories_view import RepositoriesView
from .repository_view import RepositoryView
from .new_repository_view import NewRepositoryView


class SpecViewApp:

    def __init__(self):
        self.repositories_view: ft.View
        self.app_items = None
        self.page = None

    def run(self, config: sview.model.SVConfig = None):
        sview.svconfig = config
        ft.app(target=self.main)

    def main(self, page: ft.Page):
        self.page = page
        page.title = "Spieces Viewer"

        def route_change(route):
            page.views.clear()
            page.views.append(
                RepositoryView("/", self, page)
            )
            if page.route.startswith("/repositories"):
                page.views.append(
                    RepositoriesView("/repositories", self, page)
                )
            if page.route.startswith("/repositories/new"):
                page.views.append(
                    NewRepositoryView("/repositories/new", self, page)
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
        page.update()
