import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplashScreen
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtCore import Qt, QTimer

from qt.gui.layout_colorwidget import Color
from gui.mainwindow import MainWindow
from utils.logger_support import init_logger

def start_main():
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
        main_window.logger = init_logger(main_window.green_log_box)           # ✅ Set up logging
        print(f"✅ Logging configured, logger assigned: {main_window.logger}")
        splash.finish(main_window)
        main_window.show()
       
        # main_window.init_panels()  # ✅ Safe moment — QApplication fully live        

    QTimer.singleShot(2000, launch_main_window)  # ⏱️ Delay splash by 2 sec

    # sys.exit(app.exec())  # 🧠 Start the Qt event loop

def main():
    app = QApplication(sys.argv)
    ex = start_main()
    # print("QApplication exists in TripCreationDialog?", QApplication.instance()) 
    sys.exit(app.exec())    

if __name__ == "__main__":
    main()
