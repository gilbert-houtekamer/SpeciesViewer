"""Pick deleted master pictures out of the trash and put them back.

ONE job: show everything the master trash can still restore, and restore the
ticked ones. It is a browser over `master_mutate.restorable_batches()` — it owns
no state of its own and never decides what is recoverable.

Invariant: the list is rebuilt from the trash after every restore, never patched.
A batch disappears from it because its files are no longer in the trash, which is
the same test that put it there — so the dialog cannot drift out of step with
what is actually on disk, and cannot offer the same picture twice.

Why a picker rather than a single "undo last": deletes form a stack, and popping
it one dialog at a time tells you nothing about what else is down there. The
thumbnail is the point of the whole screen — a UPN like
`2025 tanzania-nb069275` does not tell you which bird you threw away.
"""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from utils.disk_space import human_bytes

_THUMB = 340        # px, the long edge of the preview pane
_METADATA_EXTS = {".xmp", ".dop"}
_JPG_EXTS = {".jpg", ".jpeg"}


def _preview_source(paths: list[Path]) -> Path | None:
    """Which trashed file to draw. A JPG is already an image; a RAW carries a
    full-size embedded JPEG. XMP/DOP are metadata and would show nothing, so a
    batch of those alone has no preview — which is itself worth seeing."""
    jpgs = [p for p in paths if p.suffix.lower() in _JPG_EXTS]
    if jpgs:
        # Prefer the full-size JPG over its -small thumbnail.
        full = [p for p in jpgs if "-small" not in p.stem.lower()]
        return (full or jpgs)[0]
    raws = [p for p in paths if p.suffix.lower() not in _METADATA_EXTS | _JPG_EXTS]
    return raws[0] if raws else None


class RestoreTrashDialog(QDialog):
    def __init__(self, parent, master_root):
        super().__init__(parent)
        self.setWindowTitle("Restore images")
        self._master_root = master_root
        self._rows: list[dict] = []
        self._thumbs: dict[str, QPixmap | None] = {}   # batch id → pixmap, None = tried and failed
        self.resize(980, 560)

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "Pictures deleted from the master are kept in the trash until it is "
            "emptied. Tick the ones to put back."))

        middle = QHBoxLayout()
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["", "Deleted", "Picture", "Files", "Size"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.currentCellChanged.connect(lambda *_: self._show_thumb())
        self._table.itemChanged.connect(self._on_item_changed)
        middle.addWidget(self._table, 1)

        right = QVBoxLayout()
        self._thumb = QLabel("Select a row")
        self._thumb.setFixedSize(_THUMB, _THUMB)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setStyleSheet("border: 1px solid palette(mid);")
        right.addWidget(self._thumb)
        # Dimensions get their own bold line: the shape is what identifies a
        # picture as a banner strip rather than a photograph of an animal, and a
        # UPN never reveals that.
        self._dims = QLabel("")
        self._dims.setFixedWidth(_THUMB)
        self._dims.setStyleSheet("font-weight: bold;")
        right.addWidget(self._dims)
        self._caption = QLabel("")
        self._caption.setWordWrap(True)
        self._caption.setFixedWidth(_THUMB)
        right.addWidget(self._caption)
        right.addStretch(1)
        middle.addLayout(right)
        outer.addLayout(middle)

        self._summary = QLabel("")
        outer.addWidget(self._summary)

        btns = QHBoxLayout()
        all_btn = QPushButton("Select all")
        all_btn.clicked.connect(lambda: self._set_all(True))
        btns.addWidget(all_btn)
        none_btn = QPushButton("Select none")
        none_btn.clicked.connect(lambda: self._set_all(False))
        btns.addWidget(none_btn)
        btns.addStretch(1)
        self._restore_btn = QPushButton("Restore selected")
        self._restore_btn.clicked.connect(self._restore_selected)
        btns.addWidget(self._restore_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        outer.addLayout(btns)

        self._reload()

    # ── list ──────────────────────────────────────────────────────────────

    def _reload(self):
        """Rebuild from the trash. Always a full rebuild, never a patch — see
        the module note: the trash is the only thing that knows what is left."""
        from db.master_mutate import restorable_batches
        self._rows = restorable_batches(self._master_root)
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Unchecked)
            self._table.setItem(i, 0, tick)
            when = r["when"].strftime("%d %b %Y  %H:%M") if r["when"] else "—"
            self._table.setItem(i, 1, QTableWidgetItem(when))
            self._table.setItem(i, 2, QTableWidgetItem(", ".join(r["upns"]) or r["batch"]))
            files_item = QTableWidgetItem(str(len(r["files"])))
            files_item.setToolTip("\n".join(r["files"]))
            self._table.setItem(i, 3, files_item)
            self._table.setItem(i, 4, QTableWidgetItem(human_bytes(r["bytes"])))
        self._table.blockSignals(False)
        if self._rows:
            self._table.setCurrentCell(0, 2)
        else:
            self._thumb.setText("Nothing in the trash")
            self._caption.setText("")
        self._update_summary()

    def _checked_rows(self) -> list[int]:
        return [i for i in range(self._table.rowCount())
                if self._table.item(i, 0).checkState() == Qt.CheckState.Checked]

    def _set_all(self, on: bool):
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        self._table.blockSignals(True)
        for i in range(self._table.rowCount()):
            self._table.item(i, 0).setCheckState(state)
        self._table.blockSignals(False)
        self._update_summary()

    def _on_item_changed(self, item):
        if item.column() == 0:
            self._update_summary()

    def _update_summary(self):
        picked = self._checked_rows()
        files = sum(len(self._rows[i]["files"]) for i in picked)
        size = sum(self._rows[i]["bytes"] for i in picked)
        total_files = sum(len(r["files"]) for r in self._rows)
        total_size = sum(r["bytes"] for r in self._rows)
        self._summary.setText(
            f"{len(picked)} of {len(self._rows)} selected — {files} file(s), "
            f"{human_bytes(size)}    (trash holds {len(self._rows)} restorable "
            f"picture(s), {total_files} file(s), {human_bytes(total_size)})")
        self._restore_btn.setEnabled(bool(picked))

    # ── thumbnail ─────────────────────────────────────────────────────────

    def _show_thumb(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rows):
            return
        r = self._rows[row]
        self._caption.setText("\n".join(r["files"]))
        src = _preview_source(r["paths"])
        if r["batch"] in self._thumbs:
            self._paint(self._thumbs[r["batch"]], src)
            return
        # Extracting an embedded preview out of a RAW takes a beat; say so
        # rather than letting the pane sit on the previous picture.
        self._thumb.setText("Loading…")
        self._dims.setText("")
        QApplication.processEvents()
        pm = self._load(src)
        self._thumbs[r["batch"]] = pm
        if self._table.currentRow() == row:      # still the selected row
            self._paint(pm, src)

    def _load(self, path: Path | None) -> QPixmap | None:
        if path is None or not path.exists():
            return None
        if path.suffix.lower() in _JPG_EXTS:
            pm = QPixmap(str(path))
            return pm if not pm.isNull() else None
        # Embed-or-decode: this thumbnail is how you decide whether to restore a
        # file, so it has to show the picture even when the RAW embeds no JPEG.
        from gui.preview_extract import preview_jpeg_bytes
        data = preview_jpeg_bytes(path)
        if not data:
            return None
        pm = QPixmap()
        pm.loadFromData(data)
        return pm if not pm.isNull() else None

    def _paint(self, pm: QPixmap | None, src: Path | None):
        if pm is None:
            self._thumb.setText("No preview")
            self._dims.setText("")
            return
        self._thumb.setPixmap(pm.scaled(
            _THUMB, _THUMB, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self._dims.setText(self._describe(pm, src))

    @staticmethod
    def _describe(pm: QPixmap, src: Path | None) -> str:
        """The pixel size, labelled honestly. For a JPG the pixmap IS the file,
        so those are the picture's real dimensions. For a RAW it is the embedded
        preview, which is smaller than the sensor image — calling that "the
        picture size" would understate every RAW in the list, so it says which
        it is measuring."""
        wh = f"{pm.width()} × {pm.height()}"
        if src is not None and src.suffix.lower() in _JPG_EXTS:
            return f"{wh} pixels"
        return f"{wh}  (embedded preview)"

    # ── restore ───────────────────────────────────────────────────────────

    def _restore_selected(self):
        picked = self._checked_rows()
        if not picked:
            return
        rows = [self._rows[i] for i in picked]
        files = sum(len(r["files"]) for r in rows)
        names = ", ".join(", ".join(r["upns"]) for r in rows[:3])
        if len(rows) > 3:
            names += f", … and {len(rows) - 3} more"
        if QMessageBox.question(
                self, "Restore images",
                f"Put back {len(rows)} picture(s) — {files} file(s), "
                f"{human_bytes(sum(r['bytes'] for r in rows))}?\n\n{names}\n\n"
                f"The files move back out of the trash and their rows return to "
                f"the master.") != QMessageBox.StandardButton.Yes:
            return

        from db.master_db import MasterDB
        from db.master_mutate import undo_delete
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        db = MasterDB(self._master_root)
        done, failed = 0, []
        try:
            db.ensure_fresh()
            # Oldest first, so the log and the manifest read chronologically.
            # One DB open for the whole run rather than one per picture.
            for r in reversed(rows):
                try:
                    res = undo_delete(db, self._master_root, r["batch"])
                    done += len(res["restored"])
                except Exception as e:
                    failed.append(f"{', '.join(r['upns']) or r['batch']}: {e}")
        finally:
            db.close()
            QApplication.restoreOverrideCursor()

        self._thumbs.clear()
        self._reload()
        tail = ("\n\nPer-species counts are rebuilt by Count Species — worth "
                "running if this put back more than a single picture.")
        if failed:
            QMessageBox.warning(
                self, "Restore images",
                f"Put back {done} file(s), but {len(failed)} picture(s) failed:\n\n"
                + "\n".join(failed[:5]) + tail)
        else:
            QMessageBox.information(
                self, "Restore images",
                f"Put back {done} file(s)." + tail)
