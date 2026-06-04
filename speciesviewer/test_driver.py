from gui.image_viewer import ImagePreviewDialog


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    dlg = ImagePreviewDialog(image_dir="D:/CurrentPictures/2017/Oostzee zeilzomer/2017-08-16")
    dlg.exec()