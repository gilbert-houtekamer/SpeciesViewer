import wx
import logging
from wx_gui.run_batch import call_batch_module
from bin import sv2ReadDirExif  # adjust path if needed

def add_trip_menu(menu_bar, parent_frame, config):
    trip_menu = wx.Menu()

    items = [
        ("Create Trip", show_create_trip_dialog),
        ("Read Directory", show_read_dir_dialog),
        ("Write Summary", show_write_pictures_dialog),
        ("Create Blog", show_write_blog_dialog),
        ("Build Website", show_build_website_dialog),
        ("Freeze Data", show_freeze_dialog),
        ("Commit Website", show_commit_dialog)
    ]

    for label, handler in items:
        item = trip_menu.Append(-1, label)
        parent_frame.Bind(wx.EVT_MENU, lambda evt, h=handler: h(parent_frame, config), item)

    menu_bar.Append(trip_menu, "Trip")

def show_create_trip_dialog(parent, config):
    return

def show_read_dir_dialog(parent, config):
    logger = logging.getLogger("speciesviewer")

    dialog = wx.Dialog(parent, title="Read Picture Directory", size=(250, 250), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
    panel = wx.Panel(dialog)

    vbox = wx.BoxSizer(wx.VERTICAL)

    info_text = wx.StaticText(panel, label="Scan a directory with photo files and extract metadata (Exif tags) for each image.")
    info_text.Wrap(600)
    vbox.Add(info_text, flag=wx.ALL, border=10)

    read_btn = wx.Button(panel, label="Read Directory")
    vbox.Add(read_btn, flag=wx.ALL | wx.ALIGN_LEFT, border=10)

    def on_read(event):
        call_batch_module(
            config,
            parent,
            logger,
            sv2ReadDirExif.main,
            "Reading picture directory...",
            "✅ Directory scan complete. Review messages above."
        )

# call_batch_module(config, callback, page, logger, module_called, before_msg, after_msg):        

    read_btn.Bind(wx.EVT_BUTTON, on_read)

    panel.SetSizer(vbox)
    dialog.Fit()
    dialog.SetMinSize((700, 250))
    dialog.CenterOnParent()    
    dialog.ShowModal()
    dialog.Destroy()

def show_write_pictures_dialog(parent, config):
    return

def show_write_blog_dialog(parent, config):
    return

def show_build_website_dialog(parent, config):
    return

def show_freeze_dialog(parent, config):
    return

def show_commit_dialog(parent, config):
    return