from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QLineEdit, QTextEdit, QPushButton, QComboBox,
    QFileDialog, QVBoxLayout, QDateEdit
)
from PyQt6.QtCore import Qt, QDate, QEvent
from PyQt6.QtGui import QPixmap
import os
from pathlib import Path
from PIL import Image
from utils.gui_config import load_gui_cache, add_recent_gui_project
from gui import theme

_ASSETS = str(Path(__file__).resolve().parent.parent / "assets")
# What the website shows for a trip or album without its own picture / banner.
_SITE_DEFAULTS = Path(__file__).resolve().parent.parent / "dist" / "includes web"
DEFAULT_TRIP_PICTURE = str(_SITE_DEFAULTS / "tripimage.jpg")
DEFAULT_TRIP_BANNER = str(_SITE_DEFAULTS / "keerkringvogel.jpg")


def _nearest_existing_dir(path: str) -> str:
    """Walk up from path to the nearest directory that actually exists.

    QFileDialog.getExistingDirectory can't navigate into a non-existent
    starting path; it silently climbs to the nearest ancestor and leaves the
    missing leaf typed in the edit box, so pre-filled-but-not-yet-created
    paths (e.g. an unfiled-inbox folder for a new year) trip the native
    dialog's own "does not exist" complaint on Select Folder.
    """
    p = Path(path.strip()) if path else None
    while p is not None and not p.is_dir():
        parent = p.parent
        p = parent if parent != p else None
    return str(p) if p else ""


def _set_valid_glyph(label: QLabel, valid: bool):
    """Show a coloured ✓/✗ validity glyph. Replaces the old black circle-check /
    circle-x PNGs, which were invisible on the dark theme."""
    label.setText("✓" if valid else "✗")
    label.setStyleSheet(
        f"color: {theme.DONE_FG if valid else '#e0574a'}; font-size: 15px; font-weight: bold;")

class _RevalidateOnActivate:
    """Re-run update_status() whenever the containing window regains focus.

    "Does this directory exist?" is a live filesystem question, but the field
    only ever asked it when the dialog was built and whenever its text changed.
    Open Read SD-cards before the card reader has enumerated the drive — or
    plug the card in while the dialog is already up — and the ✗ from that first
    check stuck there forever, so a perfectly valid card folder looked invalid
    until you retyped the path. Alt-tabbing back from Explorer now re-checks.

    Mix in BEFORE QWidget so showEvent/eventFilter resolve here first. Qt drops
    an event filter automatically when the filter object is destroyed, so the
    filter never outlives the field it belongs to.
    """

    _activation_filter_on = None

    def showEvent(self, event):
        super().showEvent(event)
        win = self.window()
        # Re-install if the field has been re-parented into another window.
        if win is not None and self._activation_filter_on is not win:
            win.installEventFilter(self)
            self._activation_filter_on = win

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.WindowActivate:
            self.update_status()
        return super().eventFilter(obj, event)


def _preview_box(expected_size, max_w=440, max_h=132):
    """Screen size of a preview for an image of expected_size.

    Both shapes have to look right in the same dialog: the 600x450 trip
    picture is bounded by the height, the 2000x270 banner by the width, so
    the box is the expected size scaled down to fit inside both limits.
    """
    w, h = expected_size
    scale = min(max_w / w, max_h / h, 1.0)
    return max(1, round(w * scale)), max(1, round(h * scale))


class ImageFileFieldQt(QWidget):
    def __init__(self, label, expected_size, root_field, icon_path=_ASSETS,
                 default_file=None):
        super().__init__()
        self.expected_size = expected_size
        # The picture the website shows when this field is left empty. Empty
        # is then a valid choice, not an error.
        self.default_file = default_file
        self.root_field = root_field  # QLineEdit or similar widget with .text()
        self.icon_path = icon_path

        # UI Elements
        self.label_widget = QLabel(label)
        self.field = QLineEdit()
        self.browse_btn = QPushButton("Browse")
        self.status_icon = QLabel()
        self.hint_label = QLabel("")

        # A thumbnail of whatever the path currently points at. It lives in
        # this widget (so validate() can keep it in step with the ✓/✗) but is
        # NOT in this widget's row — the dialog parks it beside the
        # description text, where there is room for it. See image_field_row().
        self.preview = QLabel()
        self.preview.setFixedSize(*_preview_box(expected_size))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_src = None      # path last loaded, to skip re-decoding
        self._clear_preview()

        # Initial UI Setup
        _set_valid_glyph(self.status_icon, False)
        self.status_icon.setToolTip("")
        self.hint_label.setStyleSheet("color: red; font-size: 9pt;")
        self.hint_label.setVisible(False)

        layout = QHBoxLayout(self)
        layout.addWidget(self.label_widget)
        layout.addWidget(self.field)
        layout.addWidget(self.browse_btn)
        layout.addWidget(self.status_icon)
        layout.addWidget(self.hint_label)

        # Connections
        self.field.textChanged.connect(self.clear_hint)
        self.field.textChanged.connect(self.validate)
        self.browse_btn.clicked.connect(self.pick_file)

        # Initial validation
        self.validate()

    def get_icon(self, status: str) -> QPixmap:
        icon_file = "circle-check.png" if status == "valid" else "circle-x.png"
        pixmap = QPixmap(os.path.join(self.icon_path, icon_file))
        if pixmap.isNull():
            print(f"❌ Failed to load icon: {icon_file}")
        return pixmap

    def validate(self) -> bool:
        relative_path = self.field.text().strip()
        root_path = self.root_field.get_text()

        if not relative_path and self.default_file:
            self.status_icon.setText("")
            self.status_icon.setToolTip("")
            self.show_hint("Empty — the default picture is used.", error=False)
            self._show_preview(self.default_file)
            return True

        if not relative_path or not root_path:
            return self._invalidate("Missing path input.")

        full_path = os.path.join(root_path, relative_path)

        if not os.path.isfile(full_path) or not full_path.lower().endswith(".jpg"):
            return self._invalidate(f"'{os.path.basename(full_path)}' is not a valid .JPG file")

        try:
            with Image.open(full_path) as img:
                if img.size != self.expected_size:
                    return self._invalidate(
                        f"Size mismatch: expected {self.expected_size}, got {img.size}"
                    )
        except Exception as err:
            return self._invalidate(f"Error reading image: {err}")

        # Valid image
        _set_valid_glyph(self.status_icon, True)
        self.status_icon.setToolTip("")
        self.clear_hint()
        self._show_preview(full_path)
        return True

    def _invalidate(self, message: str) -> bool:
        _set_valid_glyph(self.status_icon, False)
        self.status_icon.setToolTip(message)
        self.show_hint(message)
        self._clear_preview()
        return False

    def _show_preview(self, full_path: str):
        """Paint the thumbnail. validate() runs on every keystroke, so the
        decode is skipped when the path has not actually moved."""
        if self._preview_src == full_path and self.preview.pixmap() is not None:
            return
        pixmap = QPixmap(full_path)
        if pixmap.isNull():
            self._clear_preview()
            return
        self.preview.setText("")
        self.preview.setPixmap(pixmap.scaled(
            self.preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self.preview.setStyleSheet(f"border: 1px solid {theme.LINE};")
        self.preview.setToolTip(full_path)
        self._preview_src = full_path

    def _clear_preview(self):
        """An empty frame rather than a hidden one: the reserved space tells
        you the shape the picture is supposed to have."""
        self._preview_src = None
        self.preview.clear()
        self.preview.setText("no preview")
        self.preview.setToolTip("")
        self.preview.setStyleSheet(
            f"border: 1px dashed {theme.LINE}; color: {theme.INK_FAINT}; font-size: 9pt;")

    def pick_file(self):
        root_path = self.root_field.get_text()
        if not os.path.isdir(root_path):
            self.status_icon.setToolTip("❌ Select valid Trip root first")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Select JPG Image", root_path, "JPEG files (*.jpg *.jpeg)"
        )
        if path:
            self.field.setText(path)

    def get_filename(self):
        return os.path.basename(self.field.text().strip())                

    def show_hint(self, message: str, error: bool = True):
        colour = "red" if error else theme.INK_FAINT
        self.hint_label.setStyleSheet(f"color: {colour}; font-size: 9pt;")
        self.hint_label.setText(message)
        self.hint_label.setVisible(True)

    def clear_hint(self):
        self.hint_label.setVisible(False)


def image_field_row(info_label, image_field) -> QWidget:
    """Description text on the left, that field's live preview on the right.

    The description paragraph leaves half a dialog of empty space next to it;
    the preview goes there so the Images tab does not grow a row taller.
    """
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(info_label, 1)
    layout.addWidget(image_field.preview, 0, Qt.AlignmentFlag.AlignTop)
    return row


class DirectoryFieldQt(_RevalidateOnActivate, QWidget):
    def __init__(self, label, initial_path="", icon_path=_ASSETS):
        super().__init__()
        self.icon_path = icon_path

        self.label_widget = QLabel(label)  # ✅ Create label first        

        self.field = QLineEdit()
        self.field.setText(initial_path)
        self.browse_btn = QPushButton("Browse")
        self.status_icon = QLabel()
        _set_valid_glyph(self.status_icon, False)

        layout = QHBoxLayout(self)
        layout.addWidget(self.label_widget)
        layout.addWidget(self.field)
        layout.addWidget(self.browse_btn)
        layout.addWidget(self.status_icon)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: red; font-size: 9pt;")
        self.hint_label.setVisible(False)
        layout.addWidget(self.hint_label)      

        self.field.textChanged.connect(self.clear_hint)        

        self.field.textChanged.connect(self.update_status)
        self.browse_btn.clicked.connect(lambda: self.browse(label))
        self.update_status()

    def textChanged(self, callback):
        self.field.textChanged.connect(callback)

    def get_icon(self, valid):
        icon_file = "circle-check.png" if valid else "circle-x.png"
        return QPixmap(os.path.join(self.icon_path, icon_file)) # .scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio)

    def update_status(self):
        # strip() to match get_path(): without it a pasted path with a trailing
        # space showed a permanent ✗ even though the Read itself ran fine.
        path = self.field.text().strip()
        valid = os.path.isdir(path)
        _set_valid_glyph(self.status_icon, valid)
        tip = "Directory exists" if valid else "Directory not found or invalid"
        self.status_icon.setToolTip(tip)
        if valid:
            self.clear_hint()
        else:
            self.show_hint(tip)

    def browse(self, label):
        start = _nearest_existing_dir(self.field.text())
        path = QFileDialog.getExistingDirectory(self, f"Choose folder for {label}", start)
        if path:
            self.field.setText(path)

    def get_path(self):
        return self.field.text().strip()            

    def show_hint(self, message):
        self.hint_label.setText(message)
        self.hint_label.setVisible(True)

    def clear_hint(self):
        self.hint_label.setVisible(False)


class RecentDirectoryFieldQt(_RevalidateOnActivate, QWidget):
    def __init__(self, label, initial_path="", section="recent_projects", icon_path=_ASSETS):
        super().__init__()
        self.icon_path = icon_path
        self.section = section

        self.label_widget = QLabel(label)
        self.combo = QComboBox()
        self.browse_btn = QPushButton("Browse")
        self.status_icon = QLabel()
        _set_valid_glyph(self.status_icon, False)

        self.combo.setMinimumWidth(500)
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.setStyleSheet("QPushButton { padding: 4px 0px; }")

        # Load recent paths from cache
        recent = load_gui_cache().get(section, [])
        self.combo.addItems(recent)
        if initial_path:
            self.set_text(initial_path)

        layout = QHBoxLayout(self)
        layout.addWidget(self.label_widget)
        layout.addWidget(self.combo)
        layout.addWidget(self.browse_btn)
        layout.addWidget(self.status_icon)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: red; font-size: 9pt;")
        self.hint_label.setVisible(False)
        layout.addWidget(self.hint_label)

        self.combo.currentTextChanged.connect(self.clear_hint)
        self.combo.currentTextChanged.connect(self.update_status)
        self.browse_btn.clicked.connect(lambda: self.browse(label))
        self.update_status()

    def textChanged(self, callback):
        self.combo.currentTextChanged.connect(callback)

    def get_icon(self, valid):
        icon_file = "circle-check.png" if valid else "circle-x.png"
        return QPixmap(os.path.join(self.icon_path, icon_file))

    def update_status(self):
        path = self.get_text()
        valid = os.path.isdir(path)
        _set_valid_glyph(self.status_icon, valid)
        tip = "Directory exists" if valid else "Directory not found or invalid"
        self.status_icon.setToolTip(tip)
        if valid:
            self.clear_hint()
        else:
            self.show_hint(tip)

    def browse(self, label):
        path = QFileDialog.getExistingDirectory(self, f"Choose folder for {label}", self.get_text())
        if path:
            self.set_text(path)
            add_recent_gui_project(path)

    def get_text(self):
        return self.combo.currentText().strip()

    def set_text(self, value):
        if value and value not in [self.combo.itemText(i) for i in range(self.combo.count())]:
            self.combo.insertItem(0, value)
        self.combo.setCurrentText(value)

    def show_hint(self, message):
        self.hint_label.setText(message)
        self.hint_label.setVisible(True)

    def clear_hint(self):
        self.hint_label.setVisible(False)


class LabeledTextFieldQt(QWidget):
    def __init__(self, label, value="", multiline=False, inline=False):
        super().__init__()
        self.multiline = multiline

        self.label = QLabel(label, self)

        if multiline:
            self.field = QTextEdit(self)
            # Plain-text only: text is stored in .ini files, so a browser
            # paste carrying HTML styling would both look wrong and break
            # INI parsing. setAcceptRichText(False) strips formatting on paste.
            self.field.setAcceptRichText(False)
            self.field.setPlainText(value)
        else:
            self.field = QLineEdit(self)
            self.field.setText(value)

        if inline and not multiline:
            layout = QHBoxLayout(self)
            layout.addWidget(self.label)
            layout.addWidget(self.field)
        else:
            layout = QVBoxLayout(self)
            layout.addWidget(self.label)
            layout.addWidget(self.field)

    def get_text(self):
        try:
            return self.field.toPlainText().strip() if self.multiline else self.field.text().strip()
        except RuntimeError:
            print("⚠️ Field widget was deleted (get_text).")
            return ""

    def set_text(self, value):
        if self.multiline:
            self.field.setPlainText(value)
        else:
            self.field.setText(value)

class DateRangeFieldQt(QWidget):
    """Start/End date pair.

    `lead_days` / `span_days` shape a NEW range: a trip is normally planned ahead
    (lead_days from today) and lasts span_days. With `follow_end=True` the end
    date keeps chasing the start — picking a new start re-sets the end to
    start + span_days, so a trip that moves in the calendar stays the same
    length. An album is a range over the PAST that the user dials in by hand, so
    it leaves both off and keeps today/today+7.
    """

    def __init__(self, from_date=None, to_date=None, *,
                 lead_days=0, span_days=7, follow_end=False):
        super().__init__()
        layout = QHBoxLayout(self)

        self.from_date = QDateEdit()
        self.to_date = QDateEdit()

        self.from_date.setCalendarPopup(True)
        self.to_date.setCalendarPopup(True)
        self.from_date.setDisplayFormat("yyyy-MM-dd")
        self.to_date.setDisplayFormat("yyyy-MM-dd")

        layout.addWidget(QLabel("Start Date"))
        layout.addWidget(self.from_date)
        layout.addSpacing(20)
        layout.addWidget(QLabel("End Date"))
        layout.addWidget(self.to_date)

        self._span_days = span_days

        # Set initial values
        start = from_date or QDate.currentDate().addDays(lead_days)
        self.from_date.setDate(start)
        self.to_date.setDate(to_date or start.addDays(span_days))

        if follow_end:
            self.from_date.dateChanged.connect(self._on_start_changed)

    def _on_start_changed(self, start):
        """Keep the end date span_days after the start. Signals are blocked so
        loading an existing trip's saved range (set_dates) never rewrites it."""
        self.to_date.setDate(start.addDays(self._span_days))

    def get_dates(self):
        return self.from_date.date().toString("yyyy-MM-dd"), self.to_date.date().toString("yyyy-MM-dd")

    def set_dates(self, from_str, to_str):
        blocked = self.from_date.blockSignals(True)
        try:
            self.from_date.setDate(QDate.fromString(from_str, "yyyy-MM-dd"))
        finally:
            self.from_date.blockSignals(blocked)
        self.to_date.setDate(QDate.fromString(to_str, "yyyy-MM-dd"))