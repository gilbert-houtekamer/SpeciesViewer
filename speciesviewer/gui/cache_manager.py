"""Unified cache management dialog.

Shows all three AppData caches as a pie chart with size labels, and lets the
user clear each one individually or all at once.  The EXIF cache gets an extra
warning because it forces a slow rebuild on the next Read Directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

_COLORS = [
    QColor("#4A9EBA"),  # full-res  — steel blue
    QColor("#6BBD8B"),  # preview   — soft green
    QColor("#E8A03C"),  # exif      — amber
]

_SLICE_NAMES = ["Full-res images", "Preview thumbnails", "EXIF metadata"]


def _fmt(b: int) -> str:
    if b == 0:
        return "empty"
    if b < 1_000_000:
        return f"{b / 1_000:.0f} KB"
    if b < 1_000_000_000:
        return f"{b / 1_000_000:.1f} MB"
    return f"{b / 1_000_000_000:.2f} GB"


class _PieWidget(QWidget):
    """Three-slice pie, starting at 12 o'clock."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[float] = [0.0, 0.0, 0.0]
        self.setFixedSize(160, 160)

    def set_values(self, values: list[float]) -> None:
        self._values = list(values)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -4)
        total = sum(self._values)
        if total == 0:
            p.setPen(QPen(QColor("#AAAAAA"), 1.5))
            p.setBrush(QBrush(QColor("#EEEEEE")))
            p.drawEllipse(rect)
            p.setPen(QPen(QColor("#888888")))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "all empty")
            return
        angle = 90 * 16  # start at 12 o'clock; Qt uses 1/16-degree units
        for i, v in enumerate(self._values):
            span = int(round(v / total * 360 * 16))
            if span == 0:
                continue
            p.setBrush(QBrush(_COLORS[i]))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPie(rect, angle, span)
            angle += span
        p.setPen(QPen(QColor("white"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)


class ManageCachesDialog(QDialog):
    """Single dialog to inspect and selectively clear all three caches.

    Parameters
    ----------
    exif_cache_path : str | None
        Path to the EXIF cache JSON file (exif_cache_path(config) from
        exifcache).  None when no master is configured — the EXIF row will
        show as unavailable and its Clear button will be disabled.
    """

    def __init__(self, parent, exif_cache_path: str | None = None):
        super().__init__(parent)
        self._exif_path = exif_cache_path
        self.setWindowTitle("Manage Caches")
        self.setMinimumWidth(500)
        self._build_ui()
        self._refresh()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ── top: pie + legend ─────────────────────────────────────────────────
        top = QHBoxLayout()
        self._pie = _PieWidget()
        top.addWidget(self._pie, 0, Qt.AlignmentFlag.AlignTop)
        top.addSpacing(20)

        legend = QVBoxLayout()
        legend.setSpacing(8)
        legend.addStretch()
        self._legend_size_labels: list[QLabel] = []
        for i, name in enumerate(_SLICE_NAMES):
            row = QHBoxLayout()
            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f"background:{_COLORS[i].name()}; border-radius: 2px;")
            row.addWidget(swatch)
            row.addSpacing(6)
            row.addWidget(QLabel(name))
            row.addStretch()
            sz = QLabel()
            self._legend_size_labels.append(sz)
            row.addWidget(sz)
            legend.addLayout(row)
        legend.addStretch()
        top.addLayout(legend)
        top.addStretch()
        root.addLayout(top)

        # ── per-cache rows ────────────────────────────────────────────────────
        specs = [
            dict(
                title="Full-res images",
                subtitle="Decoded RAW images for the 100% culling view",
                stat_fn=self._fullres_stats,
                clear_fn=self._clear_fullres,
                warning=None,
            ),
            dict(
                title="Preview thumbnails",
                subtitle="Extracted previews for image grids and the large viewer",
                stat_fn=self._preview_stats,
                clear_fn=self._clear_preview,
                warning=None,
            ),
            dict(
                title="EXIF metadata cache",
                subtitle="Cached EXIF reads — speeds up Read Directory",
                stat_fn=self._exif_stats,
                clear_fn=self._clear_exif,
                warning="Rebuilds on the next Read Directory (one slow scan).",
            ),
        ]

        self._rows: list[dict] = []
        for spec in specs:
            group = QGroupBox(spec["title"])
            gl = QVBoxLayout(group)
            gl.setSpacing(4)

            sub = QLabel(spec["subtitle"])
            sub.setStyleSheet("color: grey; font-size: 11px;")
            gl.addWidget(sub)

            size_row = QHBoxLayout()
            size_lbl = QLabel()
            size_row.addWidget(size_lbl)
            size_row.addStretch()
            btn = QPushButton("Clear")
            btn.setFixedWidth(80)
            size_row.addWidget(btn)
            gl.addLayout(size_row)

            path_lbl = QLabel()
            path_lbl.setStyleSheet("color: grey; font-size: 10px;")
            path_lbl.setWordWrap(True)
            gl.addWidget(path_lbl)

            if spec["warning"]:
                warn = QLabel(f"⚠  {spec['warning']}")
                warn.setStyleSheet("color: #C07000; font-size: 11px;")
                gl.addWidget(warn)

            root.addWidget(group)
            row = dict(size_lbl=size_lbl, path_lbl=path_lbl, btn=btn,
                       stat_fn=spec["stat_fn"], clear_fn=spec["clear_fn"],
                       title=spec["title"])
            self._rows.append(row)
            btn.clicked.connect(lambda _, r=row: self._on_clear(r))

        # ── bottom buttons ────────────────────────────────────────────────────
        btns = QHBoxLayout()
        self._clear_all_btn = QPushButton("Clear All")
        self._clear_all_btn.clicked.connect(self._on_clear_all)
        btns.addWidget(self._clear_all_btn)
        btns.addStretch()
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        root.addLayout(btns)

    # ── stats helpers ─────────────────────────────────────────────────────────

    def _fullres_stats(self) -> tuple[int, int, str]:
        from gui.fullres import fullres_cache_stats, fullres_cache_dir
        count, total = fullres_cache_stats()
        return count, total, str(fullres_cache_dir())

    def _preview_stats(self) -> tuple[int, int, str]:
        from gui.fullres import preview_cache_stats, preview_cache_root
        count, total = preview_cache_stats()
        return count, total, str(preview_cache_root())

    def _exif_stats(self) -> tuple[int, int, str]:
        if not self._exif_path:
            return 0, 0, "(no master configured)"
        from engine import exifcache
        paths = [self._exif_path] + [
            f"{self._exif_path}.bak{i}" for i in range(1, exifcache.CACHE_BACKUPS + 1)
        ]
        count = total = 0
        for p in paths:
            try:
                total += os.path.getsize(p)
                count += 1
            except OSError:
                pass
        return count, total, str(Path(self._exif_path).parent)

    # ── refresh ───────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        sizes: list[float] = []
        for i, row in enumerate(self._rows):
            count, total, path = row["stat_fn"]()
            row["_count"] = count
            row["_total"] = total
            if count:
                row["size_lbl"].setText(f"{_fmt(total)} · {count} file(s)")
            else:
                row["size_lbl"].setText("Empty")
            row["path_lbl"].setText(path)
            row["btn"].setEnabled(count > 0)
            sizes.append(float(total))

        for i, sz in enumerate(sizes):
            total_all = sum(sizes)
            pct = f" ({sz / total_all * 100:.0f}%)" if total_all else ""
            self._legend_size_labels[i].setText(_fmt(int(sz)) + pct)

        self._pie.set_values(sizes)
        self._clear_all_btn.setEnabled(any(s > 0 for s in sizes))

    # ── clear actions ─────────────────────────────────────────────────────────

    def _on_clear(self, row: dict) -> None:
        row["clear_fn"](row)

    def _clear_fullres(self, row: dict) -> None:
        count, total, path = self._fullres_stats()
        if QMessageBox.question(
                self, "Clear full-res cache",
                f"Delete {count} file(s) ({_fmt(total)}) from:\n{path}?"
        ) != QMessageBox.StandardButton.Yes:
            return
        from gui.fullres import clear_fullres_cache
        clear_fullres_cache()
        self._refresh()

    def _clear_preview(self, row: dict) -> None:
        count, total, path = self._preview_stats()
        if QMessageBox.question(
                self, "Clear preview cache",
                f"Delete {count} file(s) ({_fmt(total)}) from:\n{path}?"
        ) != QMessageBox.StandardButton.Yes:
            return
        from gui.fullres import clear_preview_cache
        clear_preview_cache()
        self._refresh()

    def _clear_exif(self, row: dict) -> None:
        if not self._exif_path:
            return
        count, total, path = self._exif_stats()
        if count == 0:
            QMessageBox.information(self, "Clear EXIF cache",
                                    f"No cache files found at:\n{path}")
            return
        from engine import exifcache
        if QMessageBox.question(
                self, "Clear EXIF cache",
                f"Delete {count} file(s) ({_fmt(total)}) from:\n{path}\n\n"
                "⚠  The cache will rebuild on the next Read Directory "
                "(one slow scan); output is unaffected."
        ) != QMessageBox.StandardButton.Yes:
            return
        paths = [self._exif_path] + [
            f"{self._exif_path}.bak{i}" for i in range(1, exifcache.CACHE_BACKUPS + 1)
        ]
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
        self._refresh()

    def _on_clear_all(self) -> None:
        counts_totals = [(r["_count"], r["_total"]) for r in self._rows]
        grand_total = sum(t for _, t in counts_totals)
        lines = "\n".join(
            f"  {_SLICE_NAMES[i]}: {_fmt(t)} ({c} file(s))"
            for i, (c, t) in enumerate(counts_totals) if c > 0
        )
        msg = (f"Delete all cached data ({_fmt(grand_total)} total)?\n\n"
               f"{lines}")
        if self._exif_path and self._rows[2]["_count"] > 0:
            msg += ("\n\n⚠  The EXIF cache will rebuild on the next Read "
                    "Directory (one slow scan).")
        if QMessageBox.question(self, "Clear All Caches", msg) != QMessageBox.StandardButton.Yes:
            return
        from gui.fullres import clear_fullres_cache, clear_preview_cache
        clear_fullres_cache()
        clear_preview_cache()
        if self._exif_path and self._rows[2]["_count"] > 0:
            from engine import exifcache
            paths = [self._exif_path] + [
                f"{self._exif_path}.bak{i}" for i in range(1, exifcache.CACHE_BACKUPS + 1)
            ]
            for p in paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
        self._refresh()
