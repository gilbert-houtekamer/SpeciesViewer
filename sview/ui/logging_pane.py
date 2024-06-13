import flet as ft


class LoggingPane(ft.Container):

    def __init__(self, page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page = page
        self.text = ft.ListView(expand=1, spacing=10, padding=20, auto_scroll=True)
        self.content = ft.Column(controls=[
            ft.Text("Logging"),
            self.text
        ])
        self.bgcolor = ft.colors.LIGHT_BLUE
        self.expand = True

    def emit(self, record, msg):
        self.text.controls.append(ft.Text(msg))
        self.page.update()
