"""Sidecar Behaviour — what SpeciesViewer writes to a picture's DxO PhotoLab
`.dop` and Lightroom `.xmp`, and what that means for how you run PhotoLab.

This dialog USED to be a 2x2 of switches ({colour&rating, crop} x {.dop, .xmp}).
It no longer is, because the choice turned out to be a false one: PhotoLab
ignores the `.xmp` when its metadata sync is off, Lightroom ignores the `.dop`
always, and neither reads the other's file under any setting — so writing both
cannot conflict, and it is the only way the two stay in step. Switches that no
longer decide anything are worse than none at all, so the dialog now explains the
behaviour instead of pretending to control it.

See utils/sidecar_policy.py for the gate, and engine/sidecarlib.ensure_dop for
the one hard rule: we never create a .dop that PhotoLab did not author.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QGroupBox, QLabel, QDialogButtonBox, QFrame,
)


class SidecarSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sidecar Behaviour")
        self.setMinimumWidth(600)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        intro = QLabel(
            "SpeciesViewer writes colour labels, ratings and crops to <b>both</b> "
            "sidecars, so PhotoLab and Lightroom always see the same thing.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        what = QGroupBox("What is written")
        wl = QVBoxLayout(what)
        for line in (
            "<b>Lightroom .xmp</b> — always. It is the sidecar that always exists, "
            "and it is what the species pipeline and the website read.",
            "<b>PhotoLab .dop</b> — whenever PhotoLab has already written one for "
            "that picture. SpeciesViewer <i>edits</i> a PhotoLab recipe; it never "
            "creates one, because PhotoLab only recognises recipes it authored "
            "itself (an invented .dop is read as a virtual copy).",
            "<b>Keywords and descriptions</b> — always to the .xmp and to JPGs; "
            "the species pipeline and the website need them.",
        ):
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            wl.addWidget(lbl)
        root.addWidget(what)

        advice = QGroupBox("Working with DxO PhotoLab")
        al = QVBoxLayout(advice)
        tip = QLabel(
            "XMP sidecars are always written during culling. If PhotoLab has "
            "<i>Edit ▸ Preferences ▸ Metadata ▸ “Synchronize metadata with XMP "
            "sidecar files”</i> switched <b>on</b>, it watches those files and can "
            "write back over your changes — so it is best not to leave PhotoLab "
            "running on the same pictures while you cull.<br><br>"
            "If you change metadata in both programs, switching that option "
            "<b>off</b> is recommended: PhotoLab then keeps to its own .dop, "
            "SpeciesViewer keeps both files in step, and neither surprises the "
            "other.<br><br>"
            "A picture has no .dop until PhotoLab makes one. To create them for a "
            "whole day, open the folder in PhotoLab, press <b>Ctrl-A</b> and choose "
            "<b>write sidecars</b> — from then on SpeciesViewer can put colour, "
            "rating and crop straight into the recipe.")
        tip.setWordWrap(True)
        al.addWidget(tip)
        root.addWidget(advice)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
