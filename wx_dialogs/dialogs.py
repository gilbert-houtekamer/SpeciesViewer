import wx
import sys
import os
import logging
from wx import DirDialog
from wx_utils.config import get_global_settings, save_global_settings, get_repo_settings, save_repo_settings

class AboutDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="About", size=(300, 200))
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        label = wx.StaticText(panel, label="Species Viewer v1.0\nBuilt with wxPython.")
        vbox.Add(label, flag=wx.ALL | wx.CENTER, border=15)

        close_btn = wx.Button(panel, label="Close")
        close_btn.Bind(wx.EVT_BUTTON, self.on_close)
        vbox.Add(close_btn, flag=wx.ALL | wx.CENTER, border=10)

        panel.SetSizer(vbox)

    def on_save(self, event):
        lines = [line.strip() for line in self.repo_paths.GetValue().splitlines()]
        valid = [line for line in lines if line and os.path.isdir(line)]
        if not valid:
            wx.MessageBox("At least one valid repo path required.", "Validation Error", wx.ICON_ERROR)
            return
        save_repo_settings(valid)
        self.EndModal(wx.ID_OK)

    def on_close(self, event):
        self.EndModal(wx.ID_OK)        

class BaseSettingsDialog(wx.Dialog):
    def __init__(self, parent, title, size=(400, 300)):
        super().__init__(parent, title=title, size=size)
        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel.SetSizer(self.sizer)

    def add_labeled_field(self, label, value="", is_password=False):
        self.sizer.Add(wx.StaticText(self.panel, label=label), 0, wx.LEFT | wx.TOP, 5)
        style = wx.TE_PASSWORD if is_password else 0
        field = wx.TextCtrl(self.panel, value=value, style=style)
        self.sizer.Add(field, 0, wx.EXPAND | wx.ALL, 5)
        return field

    def add_checkbox(self, label, checked=False):
        checkbox = wx.CheckBox(self.panel, label=label)
        checkbox.SetValue(checked)
        self.sizer.Add(checkbox, 0, wx.ALL | wx.EXPAND, 5)
        return checkbox

    def add_button(self, label, handler):
        btn = wx.Button(self.panel, label=label)
        btn.Bind(wx.EVT_BUTTON, handler)
        self.sizer.Add(btn, 0, wx.ALL | wx.CENTER, 10)
        return btn

    def show_success(self, message="Settings saved"):
        wx.MessageBox(f"✅ {message}", "Success", wx.OK | wx.ICON_INFORMATION)
        self.Destroy()


class GlobalSettingsDialog(BaseSettingsDialog):
    def __init__(self, parent, config):
        super().__init__(parent, title="Global Settings", size=(400, 300))

        self.config = config
        splash_enabled = config.get("Global", "splash_enabled", fallback="yes") == "yes"
        self.splash_switch = self.add_checkbox("Enable Splash", checked=splash_enabled)

        self.width_field = self.add_labeled_field("Window width", config.get("Global", "default_window_width", fallback="1600"))
        self.height_field = self.add_labeled_field("Window height", config.get("Global", "default_window_height", fallback="1200"))
        self.webpassword_field = self.add_labeled_field("Website password", config.get("websettings", "sftppwd", fallback="? still needed"))

        self.add_button("Save", self.save_settings)

    def save_settings(self, event):
        self.config.set("Global", "splash_enabled", "yes" if self.splash_switch.GetValue() else "no")
        self.config.set("Global", "default_window_width", self.width_field.GetValue())
        self.config.set("Global", "default_window_height", self.height_field.GetValue())
        self.config.set("websettings", "sftppwd", self.webpassword_field.GetValue())
        with open("sv_config.ini", "w", encoding="utf-8") as f:
            self.config.write(f)
        self.show_success("Logging configured")
        logger = logging.getLogger(("speciesviewer"))
        logger.info("Logging configured")



class RepoSettingsDialog(BaseSettingsDialog):
    def __init__(self, parent, config):
        super().__init__(parent, title="Repositories", size=(600, 450))
        self.config = config

        # [master] Section
        self.master_field = self.add_labeled_field("[master] Database", config.get("master", "masterbiotadatabase", fallback=""))
        browse_master_btn = wx.Button(self.panel, label="Browse [master] Database")
        browse_master_btn.Bind(wx.EVT_BUTTON, self.browse_master)
        self.sizer.Add(browse_master_btn, 0, wx.ALL | wx.ALIGN_RIGHT, 5)

        self.master_web = self.add_labeled_field("[master] Web root", config.get("master", "masterbiotawebroot", fallback=""))

        self.sizer.Add(wx.StaticLine(self.panel), 0, wx.EXPAND | wx.ALL, 5)

        # [trip] Section
        self.trip_field = self.add_labeled_field("[trip] Database", config.get("trip", "tripbiotadatabase", fallback=""))
        browse_trip_btn = wx.Button(self.panel, label="Browse [trip] Database")
        browse_trip_btn.Bind(wx.EVT_BUTTON, self.browse_trip)
        self.sizer.Add(browse_trip_btn, 0, wx.ALL | wx.ALIGN_RIGHT, 5)

        self.trip_web = self.add_labeled_field("[trip] Web root", config.get("trip", "tripbiotawebroot", fallback=""))

        self.add_button("Save", self.save_settings)

    def browse_master(self, event):
        with DirDialog(self, "Select [master] Database Directory") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.master_field.SetValue(dlg.GetPath())

    def browse_trip(self, event):
        with DirDialog(self, "Select [trip] Database Directory") as dlg:
            if dlg.ShowModal() == wx.ID_OK:
                self.trip_field.SetValue(dlg.GetPath())

    def save_settings(self, event):
        self.config.set("master", "masterbiotadatabase", self.master_field.GetValue())
        self.config.set("master", "masterbiotawebroot", self.master_web.GetValue())
        self.config.set("trip", "tripbiotadatabase", self.trip_field.GetValue())
        self.config.set("trip", "tripbiotawebroot", self.trip_web.GetValue())
        with open("sv_config.ini", "w", encoding="utf-8") as f:
            self.config.write(f)
        self.show_success("Repository settings saved")