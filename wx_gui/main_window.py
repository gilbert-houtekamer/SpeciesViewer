import wx
from wx_dialogs.dialogs import AboutDialog

class MyFrame(wx.Frame):
    def __init__(self, parent, title):
        super().__init__(parent, title=title, size=(500, 600))
        self._create_menu()

    def _create_menu(self):
        menu_bar = wx.MenuBar()

        help_menu = wx.Menu()
        about_item = help_menu.Append(wx.ID_ABOUT, "About\tF1", "Show About Dialog")
        menu_bar.Append(help_menu, "&Help")

        self.SetMenuBar(menu_bar)
        self.Bind(wx.EVT_MENU, self.on_about, about_item)

    def on_about(self, event):
        dlg = AboutDialog(self)
        dlg.ShowModal()
        dlg.Destroy()
