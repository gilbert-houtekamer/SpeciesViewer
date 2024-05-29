import flet as ft
import sview.ui.app as app


def run(page: ft.Page):
    page.title = "GH Species Viewer"
    page.padding = 0
    page.bgcolor = ft.colors.BLUE_GREY_200
    a = app.SpecViewApp(page)
    page.add(a)
    page.update()
