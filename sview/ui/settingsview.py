import flet as ft

class SettingsView(ft.View):

    def __init__(self, page: ft.Page):
        self.page = page
        self.r = "/settings"
        self.c = [
            ft.AppBar(title=ft.Text("Settings"), bgcolor=ft.colors.SURFACE_VARIANT),
            ft.Text("Settings!", style="bodyMedium"),
            ft.ElevatedButton(
                "Go to mail settings", on_click=self.open_mail_settings
            ),
        ]
        super().__init__(self.r, self.c)

    def open_mail_settings(self, e):
        self.page.go("/settings/mail")

