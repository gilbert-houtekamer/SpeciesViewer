"""Trip trash — look at the pictures deleted on trip days, put some back, or
empty a day's trash for good.

ONE job: show <pictures root>/.trash/<day>/<picture>/ as a grid, one day at a
time, and offer exactly two actions:

  - Restore selected: moves each picture's files back into its day folder
    (engine.trip_trash.restore_picture — all or nothing, never overwrites) and
    marks the day's Read & Summarize as to be re-run. Only for a day that is
    not published: a published day is locked until Reset Trip.
  - Discard this day: PERMANENTLY deletes every picture of the day shown, after
    a confirmation that names the count and the size.

Selecting: left click as usual (Ctrl/Shift extend); RIGHT click adds a picture to
the selection or takes it out — no key to hold, and any number of separate
pictures. Thumbnails are read only for the cards on screen, as you scroll, and a
restore takes its cards off the grid without reloading anything.

Deliberately not the Image Viewer: that grid's colour bar, delete buttons and
species keys mean nothing for a deleted picture. Double-click (or z) opens Quick
View read-only for a big look.

The pictures come from _TrashCollection, not the culling grid's collection: that
one reads every .xmp through exiftool (42 s for a 200-picture day), and a trashed
picture needs nothing from its sidecar — only an image.
"""
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QSize, QThread, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem, QMessageBox,
                             QProgressDialog, QPushButton, QVBoxLayout,
                             QAbstractItemView)

from engine import trip_trash
from gui.window_title import window_title
from gui.widgets import wrapped

logger = logging.getLogger("speciesviewer")

_THUMB_W = 220


class _TrashItem:
    """One trashed picture, as much of a picture item as the grid and Quick View
    ask for: an image. Everything else reads as empty (Quick View uses getattr
    defaults, and is opened read-only)."""
    color = ""
    exposure = 0.0
    species = ""

    def __init__(self, key, source, loader):
        self.key = key
        self._source = source
        self._loader = loader

    def _produce(self):
        from PyQt6.QtGui import QImage
        from utils.raw_extensions import RAW, SELF_DESCRIBING
        src = self._source
        suffix = src.suffix.lower()
        if suffix in RAW:
            from gui.preview_extract import preview_jpeg_bytes
            data = preview_jpeg_bytes(src)
            return QImage.fromData(data) if data else None
        img = QImage(str(src))
        if img.isNull() and suffix in SELF_DESCRIBING:        # HEIC: Qt can't read it
            import io
            from PIL import Image
            import pillow_heif
            pillow_heif.register_heif_opener()
            buf = io.BytesIO()
            Image.open(src).convert("RGB").save(buf, "JPEG", quality=90)
            img = QImage.fromData(buf.getvalue())
        return img if not img.isNull() else None

    def request_thumbnail(self, target_w, on_ready):
        self._loader.request(self._produce, on_ready, target_w)

    def request_full_pixmap(self, on_ready):
        self._loader.request(self._produce, on_ready)


class _TrashCollection:
    """The pictures of one trashed day, keyed by their folder name."""

    def __init__(self, pictures, loader):
        from utils.raw_extensions import RAW, SELF_DESCRIBING
        self._items, self._raw = {}, {}
        for folder, files in pictures:
            by_kind = ([f for f in files if f.suffix.lower() in RAW]
                       or [f for f in files if f.suffix.lower() in SELF_DESCRIBING]
                       or [f for f in files if f.suffix.lower() in (".jpg", ".jpeg", ".png")])
            if not by_kind:
                continue                      # sidecars only: nothing to show
            key = folder.name
            self._items[key] = _TrashItem(key, by_kind[0], loader)
            if by_kind[0].suffix.lower() in RAW:
                self._raw[key] = by_kind[0]

    def all_keys(self):
        return list(self._items)

    def item_for_key(self, key):
        return self._items[key]

    def raw_path(self, key):
        # None for a HEIC/JPG: Quick View then shows the item's own image.
        return self._raw.get(key)


# Size counts still running. A QThread destroyed mid-run takes the app down, and
# closing the window must not wait minutes for a 200 GB walk — so a running count
# keeps itself alive here and lets go when it finishes.
_RUNNING: set = set()


def _human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


class _SizeWorker(QObject):
    """Counts the whole trash off the GUI thread — 200 GB is minutes of stat()."""
    # object, not int: Qt carries an int signal argument as 32 bits, and a
    # 245 GB trash overflowed to "990 MB".
    done = pyqtSignal(object, object)

    def __init__(self, pictures_root):
        super().__init__()
        self._root = pictures_root

    def run(self):
        try:
            n, size = trip_trash.trash_size(self._root)
        except OSError as e:
            logger.warning(f"Trip trash: could not measure {self._root}: {e}")
            n, size = -1, 0
        self.done.emit(n, size)


class _ToggleGrid(QListWidget):
    """The picture grid. Left click selects as usual (Ctrl/Shift extend it);
    RIGHT click adds a picture to the selection or takes it out again — no key
    to hold, and as many separate pictures as you like (a restore is typically
    3 out of 100, which a single [ ] range can't express)."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            item = self.itemAt(event.position().toPoint())
            if item is not None:
                item.setSelected(not item.isSelected())
            event.accept()
            return
        super().mousePressEvent(event)


class TripTrashDialog(QDialog):
    def __init__(self, parent, trip_root, pictures_root, trip_name: str = ""):
        super().__init__(parent)
        self._main_window = parent
        self._trip_root = Path(trip_root)
        self._pictures_root = Path(pictures_root)
        self._coll = None
        self._keys: list = []
        self._folder_of: dict = {}           # key -> picture folder in the trash
        self._items: dict = {}               # key -> grid item
        self._requested: set = set()         # keys whose thumbnail is on its way
        self._thumbs: dict = {}              # key -> pixmap, kept for the session
        self._generation = 0                 # bumps on every day load: late thumbnails are dropped
        self._size = None                    # (pictures, bytes) once measured
        self._leftovers = 0                  # sidecar-only folders on the day shown
        from gui.image_loader import AsyncImageLoader
        self._loader = AsyncImageLoader(self)

        self.setWindowTitle(window_title("Trash", trip_name))
        self.setWindowFlags(Qt.WindowType.Window
                            | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowSystemMenuHint
                            | Qt.WindowType.WindowMinMaxButtonsHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.resize(1200, 800)

        lay = QVBoxLayout(self)
        lay.addWidget(wrapped(
            "Pictures deleted on this trip's days. Right-click a picture to add it "
            "to the selection (right-click again to take it out), then Restore puts "
            "them back in their day; that day then needs Read & Summarize again. "
            "A published day is locked until Reset Trip. Double-click a picture "
            "for a big look."))

        top = QHBoxLayout()
        top.addWidget(QLabel("Day:"))
        self._day_combo = QComboBox()
        self._day_combo.setMinimumWidth(320)
        self._day_combo.currentIndexChanged.connect(self._load_day)
        top.addWidget(self._day_combo)
        top.addStretch(1)
        self._size_lbl = QLabel("Measuring the trash…")
        top.addWidget(self._size_lbl)
        lay.addLayout(top)

        self._grid = _ToggleGrid()
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setIconSize(QSize(_THUMB_W, _THUMB_W * 3 // 4))
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setUniformItemSizes(True)
        self._grid.setSpacing(6)
        self._grid.setWordWrap(True)
        self._grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._grid.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # A selected picture must be unmistakable on a big screen.
        self._grid.setStyleSheet(
            "QListWidget::item:selected { border: 5px solid #2f7de1; }")
        self._grid.itemSelectionChanged.connect(self._update_buttons)
        self._grid.itemDoubleClicked.connect(lambda it: self._quick_view(it))
        # Thumbnails are read only for what is on screen (and one screen ahead),
        # as you scroll — an 800-picture day must not read 800 RAWs up front.
        # Wait until the scrolling stops, then load what is on screen THEN.
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(150)
        self._scroll_timer.timeout.connect(self._request_visible)
        self._grid.verticalScrollBar().valueChanged.connect(
            lambda _v: self._scroll_timer.start())
        lay.addWidget(self._grid, 1)
        QShortcut(QKeySequence("z"), self._grid,
                  activated=lambda: self._quick_view(self._grid.currentItem()))

        self._status = QLabel("")
        lay.addWidget(self._status)

        btns = QHBoxLayout()
        self._restore_btn = QPushButton("Restore selected")
        self._restore_btn.clicked.connect(self._restore_selected)
        self._discard_btn = QPushButton("Discard this day…")
        self._discard_btn.setToolTip("Permanently delete every picture of this day in the trash.")
        self._discard_btn.clicked.connect(self._discard_day)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btns.addWidget(self._restore_btn)
        btns.addStretch(1)
        btns.addWidget(self._discard_btn)
        btns.addWidget(close_btn)
        lay.addLayout(btns)

        self._fill_days()
        self._measure()

    # ── days ─────────────────────────────────────────────────────────────────
    def _day_locked(self, day: str) -> bool:
        from gui.day_freeze import day_is_frozen
        return day_is_frozen(self._trip_root, day)[0]

    def _day_label(self, day: str, n: int) -> str:
        lock = " — published, locked" if self._day_locked(day) else ""
        return f"{day}  ({n} picture{'s' if n != 1 else ''}){lock}"

    def _fill_days(self, keep: str = ""):
        self._day_combo.blockSignals(True)
        self._day_combo.clear()
        for day in trip_trash.trash_days(self._pictures_root):
            n = trip_trash.trash_day_count(self._pictures_root, day)
            self._day_combo.addItem(self._day_label(day, n), day)
        self._day_combo.blockSignals(False)
        idx = self._day_combo.findData(keep) if keep else -1
        self._day_combo.setCurrentIndex(idx if idx >= 0 else 0)
        if self._day_combo.count() == 0:
            self._grid.clear()
            self._status.setText("The trash is empty.")
        self._load_day()

    def _current_day(self) -> str:
        return self._day_combo.currentData() or ""

    def _load_day(self):
        self._generation += 1
        self._loader.cancel_all()
        self._grid.clear()
        self._keys, self._folder_of, self._items, self._coll = [], {}, {}, None
        self._requested = set()
        self._leftovers = 0
        day = self._current_day()
        if not day:
            self._update_buttons()
            return
        pictures = trip_trash.trash_pictures(self._pictures_root, day)
        self._coll = _TrashCollection(pictures, self._loader)
        files_of = {p.name: (p, fs) for p, fs in pictures}
        for key in self._coll.all_keys():
            folder, files = files_of[key]
            self._keys.append(key)
            self._folder_of[key] = folder
            kinds = " · ".join(sorted(f.suffix.lstrip(".").upper() for f in files))
            it = QListWidgetItem(f"{folder.name}\n{kinds}")
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setSizeHint(QSize(_THUMB_W + 16, _THUMB_W * 3 // 4 + 52))
            if key in self._thumbs:
                it.setIcon(QIcon(self._thumbs[key]))
            self._items[key] = it
            self._grid.addItem(it)
        # Folders holding only a sidecar (.xmp) have no picture to show or
        # restore — their RAW is gone. Count them apart, so the day's number
        # is the pictures you see; Discard this day removes them too.
        self._leftovers = len(pictures) - len(self._keys)
        self._day_combo.setItemText(self._day_combo.currentIndex(),
                                    self._day_label(day, len(self._keys)))
        self._show_day_status()
        self._update_buttons()
        QTimer.singleShot(0, self._request_visible)

    def _show_day_status(self):
        day = self._current_day()
        locked = self._day_locked(day)
        left = self._leftovers
        self._status.setText(
            f"{len(self._keys)} picture(s) deleted on {day}."
            + (f" Also {left} leftover sidecar(s) (.xmp) whose picture is gone — "
               f"nothing to restore." if left else "")
            + (" This day is published: restore is locked until Reset Trip." if locked else ""))

    def _request_visible(self, *_):
        """Ask for the thumbnails of the cards on screen plus one screen ahead."""
        if self._coll is None:
            return
        # Drop whatever is still queued for cards no longer on screen, so the
        # cards you are looking at come first.
        self._loader.cancel_all()
        self._requested = set()
        view = self._grid.viewport().rect()
        ahead = view.adjusted(0, -view.height(), 0, view.height())
        gen = self._generation
        for key, it in self._items.items():
            if key in self._requested or key in self._thumbs:
                continue
            if not self._grid.visualItemRect(it).intersects(ahead):
                continue
            self._requested.add(key)
            self._coll.item_for_key(key).request_thumbnail(
                _THUMB_W, lambda px, k=key, g=gen: self._on_thumb(k, g, px))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._request_visible)

    def _on_thumb(self, key, gen, px):
        if px is None or px.isNull():
            return
        self._thumbs[key] = px
        if gen != self._generation:
            return                       # a later day load superseded this request
        it = self._items.get(key)
        if it is not None:
            it.setIcon(QIcon(px))

    # ── actions ──────────────────────────────────────────────────────────────
    def _selected_keys(self) -> list:
        return [it.data(Qt.ItemDataRole.UserRole) for it in self._grid.selectedItems()]

    def _update_buttons(self):
        n = len(self._selected_keys())
        day = self._current_day()
        locked = bool(day) and self._day_locked(day)
        self._restore_btn.setText(f"Restore selected ({n})" if n else "Restore selected")
        self._restore_btn.setEnabled(n > 0 and not locked)
        self._restore_btn.setToolTip(
            "This day is published: Reset Trip to unlock it." if locked else
            "Move the selected pictures back into their day folder.")
        self._discard_btn.setEnabled(bool(day))

    def _quick_view(self, item):
        if item is None or self._coll is None:
            return
        from gui.quick_viewer import QuickViewer
        key = item.data(Qt.ItemDataRole.UserRole)
        dlg = QuickViewer(self, self._coll, self._keys, self._keys.index(key), read_only=True)
        dlg.show()

    def _drop_cards(self, keys):
        """Take restored pictures off the grid — no reload, nothing re-read."""
        for key in keys:
            it = self._items.pop(key, None)
            if it is not None:
                self._grid.takeItem(self._grid.row(it))
            if key in self._keys:
                self._keys.remove(key)
            self._folder_of.pop(key, None)
            self._thumbs.pop(key, None)
        if not self._keys:
            self._fill_days()            # the day is empty now: it leaves the list
            return
        self._day_combo.setItemText(self._day_combo.currentIndex(),
                                    self._day_label(self._current_day(), len(self._keys)))
        self._show_day_status()
        self._update_buttons()
        QTimer.singleShot(0, self._request_visible)

    def _restore_selected(self):
        day = self._current_day()
        keys = self._selected_keys()
        if not keys or not day or self._day_locked(day):
            return
        day_folder = self._pictures_root / day
        done, clashes = [], []
        for key in keys:
            folder = self._folder_of.get(key)
            if folder is None or not folder.is_dir():
                continue
            try:
                moved, conflicts = trip_trash.restore_picture(folder, day_folder)
            except OSError as e:
                clashes.append(f"{folder.name}: {e}")
                continue
            if conflicts:
                clashes.append(f"{folder.name}: already in the day — {', '.join(conflicts)}")
            elif moved:
                done.append(key)
        if done:
            self._mark_day_for_reread(day)
        logger.info(f"Trip trash: restored {len(done)} picture(s) to {day_folder}.")
        msg = f"{len(done)} picture(s) restored to {day}."
        if done:
            msg += "\n\nRun Read & Summarize for this day to bring them back into the trip."
        if clashes:
            msg += ("\n\nNot restored (a file with the same name is already in the day "
                    "folder; nothing was overwritten):\n" + "\n".join(clashes[:20]))
            if len(clashes) > 20:
                msg += f"\n… and {len(clashes) - 20} more."
        QMessageBox.information(self, "Restore", msg)
        self._drop_cards(done)
        self._measure()

    def _mark_day_for_reread(self, day: str):
        """The restored files are on disk but not in the day's data yet: reset
        Read & Summarize (and everything after it) so the overview says so."""
        from engine.statemachine import StateMachine
        sm = StateMachine(str(self._trip_root), day, readState=True)
        if not sm.Taskstate:
            return                       # the day was never read: nothing to reset
        sm.updateState("ReadDirExif", False,
                       "Pictures restored from the trash — run Read & Summarize again.")
        refresh = getattr(self._main_window, "refresh_trip_row", None)
        if refresh:
            try:
                refresh(sm, day)
            except Exception as e:
                logger.warning(f"Trip trash: could not refresh the overview row for {day}: {e}")

    def _discard_day(self):
        day = self._current_day()
        n = len(self._keys) + self._leftovers
        if not day or n == 0:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            size = trip_trash.trash_day_size(self._pictures_root, day)
        finally:
            QApplication.restoreOverrideCursor()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Discard this day")
        box.setText(f"Permanently delete everything of {day} in the trash — {n:,} "
                    f"picture folder(s), {_human_size(size)}?")
        box.setInformativeText("This cannot be undone. The pictures do not go to the "
                               "Windows recycle bin.")
        go = box.addButton(f"Delete {n:,} pictures", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not go:
            return
        prog = QProgressDialog("Deleting…", "Stop", 0, n, self)
        prog.setWindowTitle("Discard this day")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)

        def tick(done, total):
            prog.setMaximum(total)
            prog.setValue(done)
            QApplication.processEvents()
            return not prog.wasCanceled()

        self._loader.cancel_all()
        self._grid.clear()                  # release thumbnails/handles first
        self._items, self._coll = {}, None
        try:
            removed = trip_trash.discard_day(self._pictures_root, day, progress_cb=tick)
        except OSError as e:
            prog.close()
            QMessageBox.warning(self, "Discard this day", f"Stopped on an error:\n{e}")
            removed = None
        prog.close()
        if removed is not None:
            QMessageBox.information(self, "Discard this day",
                                    f"{removed:,} picture(s) of {day} permanently deleted.")
        self._fill_days(keep=day)
        self._measure()

    # ── size ─────────────────────────────────────────────────────────────────
    def _measure(self):
        self._size_lbl.setText("Measuring the trash…")
        thread = QThread()
        worker = _SizeWorker(self._pictures_root)
        worker.moveToThread(thread)
        pair = (thread, worker)
        _RUNNING.add(pair)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_size)
        worker.done.connect(thread.quit)
        thread.finished.connect(lambda p=pair: _RUNNING.discard(p))
        thread.start()

    def _on_size(self, n: int, size: int):
        if n < 0:
            self._size_lbl.setText("Could not measure the trash.")
            return
        self._size = (n, size)
        self._size_lbl.setText(f"Whole trash: {n:,} pictures · {_human_size(size)}")
