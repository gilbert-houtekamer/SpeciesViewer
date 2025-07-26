import flet as ft
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextEdit
from PyQt5.QtCore import QTimer

def make_log_tail_accessor(log_box):
    def get_log_tail():
        return [ctrl.value for ctrl in log_box.controls if hasattr(ctrl, "value")]
    return get_log_tail

# class LogTailWidget(QWidget):
#     def __init__(self, log_source_func, title="Recent log entries:", tail_size=10, update_interval=500, parent=None):
#         super().__init__(parent)

#         self.log_source_func = log_source_func  # Should return list of log lines
#         self.tail_size = tail_size

#         layout = QVBoxLayout()
#         layout.addWidget(QLabel(title))

#         self.textbox = QTextEdit()
#         self.textbox.setReadOnly(True)
#         self.textbox.setStyleSheet("background-color: #f8f8f8; font-family: monospace;")
#         layout.addWidget(self.textbox)

#         self.setLayout(layout)

#         self.timer = QTimer(self)
#         self.timer.timeout.connect(self.update_log_tail)
#         self.timer.start(update_interval)
class LogTailWidget(ft.Column):
    def __init__(self, get_lines_func, tail_size=10):
        super().__init__(scroll=ft.ScrollMode.AUTO)
        self.get_lines_func = get_lines_func
        self.tail_size = tail_size
        self.refresh()

    def refresh(self):
        self.controls.clear()
        latest_lines = self.get_lines_func()[-self.tail_size:]
        for line in latest_lines:
            self.controls.append(ft.Text(line))

    def update_log_tail(self):
        lines = self.log_source_func()
        tail = lines[-self.tail_size:]
        self.textbox.setPlainText("\n".join(tail))
