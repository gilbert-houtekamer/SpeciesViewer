import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplashScreen
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtCore import Qt, QTimer

from qt.gui.layout_colorwidget import Color
from gui.menu import create_menubar
from gui.mainwindow import MainWindow
from utils.logger_support import init_logger
from gui.setup_logpanel import attach_log_panel  # ✅ Safe: won't be called until after QApplication

def start_main():
    app = QApplication(sys.argv)  # ✅ Must come first

    # 🖼️ Splash screen setup
    splash_pix = QPixmap("assets/svsplash.jpg")
    splash = QSplashScreen(splash_pix)
    splash.setWindowFlag(Qt.WindowType.FramelessWindowHint)

    font_title = QFont("Arial", 20, QFont.Weight.Bold)
    splash.setFont(font_title)
    splash.showMessage(
        "Welcome to Species Viewer\nOrganize species. Explore collections. Bring biodiversity to life.",
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        Qt.GlobalColor.white
    )

    splash.show()

    # 🪄 Launch main app window after splash
    def launch_main_window():
        main_window = MainWindow()
        #attach_log_panel(main_window)      # ✅ Safe to attach after QApplication
        init_logger(main_window.green_log_box)           # ✅ Set up logging
        splash.finish(main_window)
        main_window.show()

    QTimer.singleShot(2000, launch_main_window)  # ⏱️ Delay splash by 2 sec

    sys.exit(app.exec())  # 🧠 Start the Qt event loop

if __name__ == "__main__":
    start_main()
