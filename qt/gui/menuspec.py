from gui.menu import MenuSpec  # assuming class is defined elsewhere
from gui.trip_process import show_read_dir_dialog
from settings.globalsettings import GlobalSettingsDialog
from settings.reposettings import RepoSettingsDialog
from dialogs.setting import AboutDialog

def build_menu_spec(main_window):
    config = main_window.config

    return {
        "File": {
            "Quit": MenuSpec(
                callback=main_window.close,
                shortcut="Ctrl+Q"
            ),
        },
        "Trip": {
            "Read Directory": MenuSpec(
                callback=lambda: show_read_dir_dialog(main_window, config).exec(),
                title="Read Directory",
                shortcut="Ctrl+D",
                # enabled=lambda: config.repo_available,
            ),
        },
        "Settings": {
            "Global Settings": MenuSpec(
                callback=lambda: GlobalSettingsDialog(main_window, config).exec(),
                shortcut="Ctrl+G",
            ),
            "Repository Settings": MenuSpec(
                callback=lambda: RepoSettingsDialog(main_window, config).exec(),
            ),
        },
        "Help": {
            "About": MenuSpec(
                callback=lambda: AboutDialog(main_window).exec(),
            ),
        }
    }