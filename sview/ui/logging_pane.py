import flet as ft
from sview.util import LoggingHandler

class LoggingPane(ft.Container):

    def __init__(self, page, loghandler: LoggingHandler, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page = page
        self.loghandler = loghandler
        self.text = ft.ListView(expand=1, spacing=0, padding=0, auto_scroll=True)
        self.content = ft.Column(controls=[
            ft.Text("Logging"),
            self.text
        ])
        self.bgcolor = ft.colors.LIGHT_BLUE
        self.expand = True
        self.loghandler.add_listener(self)

    def emit(self, record, msg):
        self.text.controls.append(ft.Text(msg))
        self.page.update()

    def will_unmount(self):
        self.loghandler.remove_listener(self)
        super().will_unmount()
