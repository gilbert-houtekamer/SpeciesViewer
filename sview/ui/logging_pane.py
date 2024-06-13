import flet as ft


class LoggingPane(ft.Container):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.content = ft.Row(controls=[
            ft.Text("logs", color=ft.colors.BLACK)
        ])

        self.bgcolor = ft.colors.GREEN
        self.expand = True
