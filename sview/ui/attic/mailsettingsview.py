import flet as ft

class MailSettingsView(ft.View):

    def __init__(self, page: ft.Page):
        self.page = page
        self.r = "/settings/mail"
        self.c = [
            ft.AppBar(
                title=ft.Text("Mail Settings"), bgcolor=ft.colors.SURFACE_VARIANT
            ),
            ft.Text("Mail settings!"),
        ]
        super().__init__(self.r, self.c)
