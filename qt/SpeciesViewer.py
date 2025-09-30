import sys

from PyQt6.QtWidgets import QApplication, QSplashScreen, QToolTip
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtCore import Qt, QTimer

from gui.mainwindow import MainWindow
from utils.logger_support import init_logger
from gui.trip_overview import populate_trip_overview

def start_main():
    # 🖼️ Splash screen setup
    splash_pix = QPixmap("assets/svsplash.jpg")
    splash = QSplashScreen(splash_pix)
    splash.setWindowFlag(Qt.WindowType.FramelessWindowHint)

    font_title = QFont("Arial", 20, QFont.Weight.Bold)
    splash.setFont(font_title)

    # Use HTML to style each line separately
    splash_text = (
        '<div style="text-align:center;">'
        '<span style="font-size:24pt; font-weight:bold;color:white;">Welcome to Species Viewer</span><br>'
        '<span style="font-size:16pt;color:white;">Organize species. Explore collections. Bring biodiversity to life.</span>'
        '</div>'
    )

    splash.showMessage(
        splash_text,
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
    )
    splash.show()

    # 🪄 Launch main app window after splash
    def launch_main_window():
        main_window = MainWindow()
        main_window.logger = init_logger(main_window.green_log_box)           # ✅ Set up logging
        print(f"✅ Logging configured, logger assigned: {main_window.logger}")
        
        populate_trip_overview(main_window)  # ✅ Now logger is ready
        splash.finish(main_window)
        main_window.show()
       
        # main_window.init_panels()  # ✅ Safe moment — QApplication fully live        

    QTimer.singleShot(2000, launch_main_window)  # ⏱️ Delay splash by 2 sec

    # sys.exit(app.exec())  # 🧠 Start the Qt event loop

def main():
    app = QApplication(sys.argv)
    QToolTip.setFont(QFont("SansSerif", 12))
    app.setStyleSheet("""
        QToolTip {
            background-color: white;
            color: #333;
            border: 1px solid #ccc;
            padding: 4px;
        }
    """)    
    ex = start_main()
    # print("QApplication exists in TripCreationDialog?", QApplication.instance()) 
    sys.exit(app.exec())    

if __name__ == "__main__":
    main()
