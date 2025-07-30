from widgets.log_panel import LogPanel

def attach_log_panel(main_window):
    main_window.log_panel = LogPanel()
    main_window._layout1.addWidget(main_window.log_panel)