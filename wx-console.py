import wx
import logging
import configparser
from wx_gui.main_window import MyFrame
from wx_dialogs.dialogs import AboutDialog, GlobalSettingsDialog, RepoSettingsDialog
from wx_dialogs.trip_dialogs import add_trip_menu
from wx_utils.config import config

class LogHandler(logging.Handler):
    def __init__(self, log_widget):
        super().__init__()
        self.log_widget = log_widget

    def emit(self, record):
        msg = self.format(record)
        wx.CallAfter(self.log_widget.AppendText, msg + "\n")

    def _append_log(self, msg):
        self.log_widget.AppendText(msg + "\n")
        self.log_widget.ShowPosition(self.log_widget.GetLastPosition())        

class MyFrame(wx.Frame):
    def __init__(self, parent, title, config):
        super().__init__(parent, title=title, size=(1500, 1200))
        self.config = config
        self._create_menu()

    def _create_menu(self):
        menu_bar = wx.MenuBar()

        # Help Menu
        help_menu = wx.Menu()
        about_item = help_menu.Append(wx.ID_ABOUT, "About\tF1", "Show About Dialog")

        # Settings Menu
        settings_menu = wx.Menu()
        global_item = settings_menu.Append(wx.ID_ANY, "Global Settings", "Set username and app-wide options")
        repo_item = settings_menu.Append(wx.ID_ANY, "Repository Settings", "Configure repo paths")

        menu_bar.Append(settings_menu, "&Settings")
        menu_bar.Append(help_menu, "&Help")
        self.SetMenuBar(menu_bar)

        # Bind events
        self.Bind(wx.EVT_MENU, self.on_about, about_item)
        self.Bind(wx.EVT_MENU, self.on_global, global_item)
        self.Bind(wx.EVT_MENU, self.on_repo, repo_item)

    def on_about(self, event):
            dlg = AboutDialog(self)
            dlg.ShowModal()
            dlg.Destroy()

    def on_global(self, event):
        dlg = GlobalSettingsDialog(self, self.config)
        dlg.ShowModal()
        dlg.Destroy()

    def on_repo(self, event):
        dlg = RepoSettingsDialog(self, self.config)
        dlg.ShowModal()
        dlg.Destroy()

class LeftSidebarPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)

        label = wx.StaticText(self, label="🔹 Left Sidebar")
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(label, 0, wx.ALL, 8)
        self.SetSizer(sizer)

class MiddleWorkspacePanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)

        label = wx.StaticText(self, label="🧭 Middle Workspace")
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(label, 0, wx.ALL, 8)
        self.SetSizer(sizer)

class RightToolsPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)

        label = wx.StaticText(self, label="🛠 Right Tools Panel")
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(label, 0, wx.ALL, 8)
        self.SetSizer(sizer)

class LogPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)

        self.log_output = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.HSCROLL | wx.VSCROLL
        )
        font = wx.Font(10, wx.FONTFAMILY_MODERN, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self.log_output.SetFont(font)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.log_output, 1, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(sizer) 

    def write(self, msg):
        self.log_output.AppendText(msg)

class SuppressExifToolOutput(logging.Filter):
    def filter(self, record):
        return not record.getMessage().startswith("ExifToolHelper.execute: OUT stdout =")        
class ConsoleFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="StudioView Console", size=(1100, 700))

        # 🔧 Read config
        self.config = configparser.ConfigParser()
        self.config.read("sv_config.ini", encoding="utf-8")

        self.id_global = wx.NewIdRef()
        self.id_repo = wx.NewIdRef()

        self.init_menu()  # 🍽️ Menu bar setup

        # 🧱 Main layout container
        self.panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.HORIZONTAL)

        # 🌆 Outer splitter: top row + log panel
        main_split = wx.SplitterWindow(self.panel)
        main_split.SetMinimumPaneSize(100)

        # 🧩 Top splitter: left | center_right
        top_split = wx.SplitterWindow(main_split)
        top_split.SetMinimumPaneSize(100)

        # 🧩 Center + right splitter
        center_right_split = wx.SplitterWindow(top_split)
        center_right_split.SetMinimumPaneSize(150)

        # 🎨 Center panel
        center_wrapper = wx.Panel(center_right_split, style=wx.BORDER_SIMPLE)
        center_inner = MiddleWorkspacePanel(center_wrapper)
        center_sizer = wx.BoxSizer(wx.VERTICAL)
        center_sizer.Add(center_inner, 1, wx.EXPAND | wx.ALL, 2)
        center_wrapper.SetSizer(center_sizer)
        center_wrapper.SetBackgroundColour("#FFFFFF")

        # 🎨 Right panel
        right_wrapper = wx.Panel(center_right_split, style=wx.BORDER_SIMPLE)
        right_inner = RightToolsPanel(right_wrapper)
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer.Add(right_inner, 1, wx.EXPAND | wx.ALL, 2)
        right_wrapper.SetSizer(right_sizer)
        right_wrapper.SetBackgroundColour("#F0F0F0")

        # ⚙️ Split center and right
        center_right_split.SplitVertically(center_wrapper, right_wrapper)
        center_right_split.SetSashPosition(600)

        # 🧊 Left panel
        left_wrapper = wx.Panel(top_split, style=wx.BORDER_SIMPLE)
        left_inner = LeftSidebarPanel(left_wrapper)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        left_sizer.Add(left_inner, 1, wx.EXPAND | wx.ALL, 2)
        left_wrapper.SetSizer(left_sizer)
        left_wrapper.SetBackgroundColour("#E0E0E0")

        # ⚙️ Split left and center_right
        top_split.SplitVertically(left_wrapper, center_right_split)
        top_split.SetSashPosition(150)

        # 🪵 Log panel (bottom)
        log_wrapper = wx.Panel(main_split, style=wx.BORDER_SIMPLE)
        self.log_panel = LogPanel(log_wrapper)
        log_sizer = wx.BoxSizer(wx.VERTICAL)
        log_sizer.Add(self.log_panel, 1, wx.EXPAND | wx.ALL, 2)
        log_wrapper.SetSizer(log_sizer)
        self.log_panel.SetBackgroundColour("#DCDCDC")

        # ⚙️ Split top and log
        main_split.SplitHorizontally(top_split, log_wrapper)
        main_split.SetSashPosition(400)

        # 🏗️ Finalize layout
        layout.Add(main_split, 1, wx.EXPAND)
        self.panel.SetSizer(layout)
        # ✅ Final layout
        self.Centre()
        self.Show
  
        logger = self.init_logger()
        logger.info("Console layout and logger initialized.")

      

    def init_menu(self):
        menubar = wx.MenuBar()

        # --- File Menu ---
        file_menu = wx.Menu()
        file_menu.Append(wx.ID_OPEN, "Open...\tCtrl+O")
        file_menu.Append(wx.ID_SAVE, "Save\tCtrl+S")
        file_menu.AppendSeparator()
        file_menu.Append(wx.ID_EXIT, "Quit\tCtrl+Q")
        menubar.Append(file_menu, "&File")

        # --- Edit Menu (if you had one) ---
        edit_menu = wx.Menu()
        edit_menu.Append(wx.ID_COPY, "Copy\tCtrl+C")
        edit_menu.Append(wx.ID_PASTE, "Paste\tCtrl+V")
        menubar.Append(edit_menu, "&Edit")

        add_trip_menu(menubar, self, self.config)
        
        # settings_menu = wx.Menu()
        # self.id_global = wx.NewIdRef().Id
        # self.id_repo = wx.NewIdRef().Id        
        settings_menu = wx.Menu()
        settings_menu.Append(self.id_global, "Global Settings", "Set username and app-wide options")
        self.Bind(wx.EVT_MENU, self.on_global, id=self.id_global)
        settings_menu.Append(self.id_repo, "Repository Settings", "Configure repo paths")
        self.Bind(wx.EVT_MENU, self.on_repo,id= self.id_repo)
        menubar.Append(settings_menu, "&Settings")

        # --- Help Menu ---
        help_menu = wx.Menu()
        help_menu.Append(wx.ID_HELP, "Help Contents\tF1")
        help_menu.Append(wx.ID_ABOUT, "About StudioView")
        menubar.Append(help_menu, "&Help")

        self.SetMenuBar(menubar)

        # Bind menu events (preserving your old logic)
        self.Bind(wx.EVT_MENU, self.on_open, id=wx.ID_OPEN)
        self.Bind(wx.EVT_MENU, self.on_save, id=wx.ID_SAVE)
        self.Bind(wx.EVT_MENU, lambda evt: self.Close(), id=wx.ID_EXIT)
        self.Bind(wx.EVT_MENU, self.on_copy, id=wx.ID_COPY)
        self.Bind(wx.EVT_MENU, self.on_paste, id=wx.ID_PASTE)
        self.Bind(wx.EVT_MENU, self.on_help, id=wx.ID_HELP)
        self.Bind(wx.EVT_MENU, lambda evt: self.on_about(), id=wx.ID_ABOUT)

    def init_logger(self):
        logger = logging.getLogger("speciesviewer")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        logger.handlers.clear()

        # File output
        fh = logging.FileHandler("studio.log", mode="w", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))
        logger.addHandler(fh)

        # Console (stdout)
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(sh)

        # GUI output via custom handler
        gui_handler = LogHandler(self.log_panel.log_output)
        logger.addHandler(gui_handler)

        logger.addFilter(SuppressExifToolOutput())

        logger.info("Logging configured")
        return logger  # in case you need it later


        # logger = logging.getLogger("speciesviewer")
        # #logger.propagate = False
        # logger.addFilter(SuppressExifToolOutput())       
        # # Assuming self.log_panel has a wx.TextCtrl called self.log_output
        # handler = LogHandler(self.log_panel.log_output)
        # logging.getLogger().addHandler(handler)    
    # def init_logger(self):
    #     logger = logging.getLogger("speciesviewer")
    #     #logger.handlers.clear()        
    #     logger.setLevel(logging.DEBUG)

    #     fh = logging.FileHandler("SpeciesViewerLog.txt", mode="w", encoding="utf-8")
    #     fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))

    #     sh = logging.StreamHandler()
    #     sh.setFormatter(logging.Formatter("%(message)s"))

    #     # gh = logging.StreamHandler(self.log_panel)
    #     # gh.setFormatter(logging.Formatter("%(message)s"))

    #     logger.handlers.clear()  # Prevent duplicate logs during dev restarts
    #     logger.addHandler(fh)
    #     logger.addHandler(sh)
    #     # logger.addHandler(gh)

    #     gui_handler = LogHandler(self.log_panel.log_output)
    #     logger.addHandler(gui_handler)
    #     # handler = LogHandler(self.log_panel.log_output)
    #     # logging.getLogger().addHandler(handler)             

        
    #     logger.addFilter(SuppressExifToolOutput())

    #     logger.info("Logging configured")
    #     return logger 

    def _create_menu(self):
        menu_bar = wx.MenuBar()

        # Help Menu
        help_menu = wx.Menu()
        about_item = help_menu.Append(wx.ID_ABOUT, "About\tF1", "Show About Dialog")

        # Settings Menu
        settings_menu = wx.Menu()
        global_item = settings_menu.Append(wx.ID_ANY, "Global Settings", "Set username and app-wide options")
        repo_item = settings_menu.Append(wx.ID_ANY, "Repository Settings", "Configure repo paths")

        menu_bar.Append(settings_menu, "&Settings")
        menu_bar.Append(help_menu, "&Help")
        self.SetMenuBar(menu_bar)

        # Bind events
        self.Bind(wx.EVT_MENU, self.on_about, about_item)
        self.Bind(wx.EVT_MENU, self.on_global, global_item)
        self.Bind(wx.EVT_MENU, self.on_repo, repo_item)

    def on_about(self, event):
        dlg = AboutDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def on_global(self, event):
        dlg = GlobalSettingsDialog(self, self.config)
        dlg.ShowModal()
        dlg.Destroy()

    def on_repo(self, event):
        dlg = RepoSettingsDialog(self, self.config)
        dlg.ShowModal()
        dlg.Destroy()        

    def on_open(self, event):
        print(" on_open(self, event)")

    def on_save(self, event):
        print(" on_save(self, event)")

    def on_copy(self, event):
        print(" on_copy(self, event)")

    def on_paste(self, event):
        print(" on_paste(self, event)")

    def on_help(self, event):
        print(" on_help(self, event)")                
              

if __name__ == "__main__":
    app = wx.App(False)
    frame = ConsoleFrame()
    frame.Show()
    logging.info("Application started.")
    app.MainLoop()        
