"""Unified cache management dialog.

Shows the three AppData caches as a pie chart with size labels, and lets the
user clear each individually or all at once.  The EXIF slice is split into two
independently-clearable rows — day/trip (Read Directory) and master scan — so
you can forget all trips without discarding the expensive master cache (or vice
versa).  Each carries its own rebuild warning; clearing takes a cache's whole
.bak ring, or load_cache would just recover from a backup.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
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
    """Single dialog to inspect, relocate, and selectively clear the three
    per-machine caches (full-res, preview, EXIF). Each cache resolves its
    location live through utils.cache_paths, so paths track relocations made
    here without the dialog needing to be told where anything lives.
    """

    def __init__(self, parent):
        super().__init__(parent)
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
        # `pie_index` maps a row to one of the three pie slices; the two EXIF
        # rows share slice 2 so the pie/legend stay 3-way while the day/trip and
        # master caches clear independently. `edit` gates the relocate button —
        # both EXIF files live in ONE folder, so only the first EXIF row offers
        # it (moving that folder moves both).
        specs = [
            dict(
                kind="fullres", pie_index=0,
                title="Full-res images",
                subtitle="Decoded RAW images for the 100% culling view",
                stat_fn=self._fullres_stats,
                clear_fn=self._clear_fullres,
                warning=None,
            ),
            dict(
                kind="preview", pie_index=1,
                title="Preview thumbnails",
                subtitle="Extracted previews for image grids and the large viewer",
                stat_fn=self._preview_stats,
                clear_fn=self._clear_preview,
                warning=None,
                extra=("Remove low-res", self._purge_small_previews,
                       self._small_preview_stats),
            ),
            dict(
                kind="exif", pie_index=2,
                title="EXIF cache — day/trip",
                subtitle="Cached EXIF reads for day/trip Read Directory (all trips)",
                stat_fn=self._exif_daytrip_stats,
                clear_fn=self._clear_exif_daytrip,
                warning="Rebuilds on the next Read Directory (one slow scan).",
                edit_stat_fn=self._exif_stats,   # relocate moves the whole folder
            ),
            dict(
                kind="exif", pie_index=2,
                title="EXIF cache — master scan",
                subtitle="Cached EXIF reads for the master-wide scan",
                stat_fn=self._exif_master_stats,
                clear_fn=self._clear_exif_master,
                warning="Rebuilds on the next Rescan master EXIF cache "
                        "(a few minutes).",
                edit=False,
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
            extra_btn = None
            if spec.get("extra"):
                label, handler, _stat = spec["extra"]
                extra_btn = QPushButton(label)
                extra_btn.setFixedWidth(120)
                extra_btn.setToolTip(
                    "Delete only the low-res (< ~50 KB) thumbnails, keeping the "
                    "full-size previews — they re-extract at full resolution.")
                extra_btn.clicked.connect(lambda _, h=handler: h())
                size_row.addWidget(extra_btn)
            edit_btn = None
            if spec.get("edit", True):
                edit_btn = QPushButton("Edit…")
                edit_btn.setFixedWidth(70)
                edit_btn.setToolTip(
                    "Move this cache to another folder (e.g. a faster or larger "
                    "disk). The day/trip and master EXIF caches share one folder, "
                    "so moving it moves both.")
                size_row.addWidget(edit_btn)
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
            row = dict(kind=spec["kind"], pie_index=spec["pie_index"],
                       size_lbl=size_lbl, path_lbl=path_lbl,
                       btn=btn, edit_btn=edit_btn,
                       stat_fn=spec["stat_fn"], clear_fn=spec["clear_fn"],
                       edit_stat_fn=spec.get("edit_stat_fn") or spec["stat_fn"],
                       title=spec["title"], extra_btn=extra_btn,
                       extra_stat_fn=spec["extra"][2] if spec.get("extra") else None)
            self._rows.append(row)
            btn.clicked.connect(lambda _, r=row: self._on_clear(r))
            if edit_btn is not None:
                edit_btn.clicked.connect(lambda _, r=row: self._on_edit(r))

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

    def _small_preview_stats(self) -> tuple[int, int]:
        from gui.fullres import small_preview_stats
        return small_preview_stats()

    def _exif_file(self) -> str:
        """Live path to exif_cache.json (recomputed so it tracks relocations)."""
        from engine import exifcache
        return str(exifcache.exif_cache_path(None))

    def _exif_files_for(self, base: str) -> list[str]:
        """A cache file plus its whole .bak ring. Clearing must take all of them
        or load_cache just recovers from a backup (the .bak fall-through)."""
        from engine import exifcache
        return [base] + [f"{base}.bak{i}"
                         for i in range(1, exifcache.CACHE_BACKUPS + 1)]

    def _exif_daytrip_files(self) -> list[str]:
        from engine import exifcache
        return self._exif_files_for(str(exifcache.exif_cache_path(None)))

    def _exif_master_files(self) -> list[str]:
        from engine import exifcache
        return self._exif_files_for(str(exifcache.master_cache_path(None)))

    def _exif_all_files(self) -> list[str]:
        """Both EXIF caches (day/trip + master) and their .bak rings — for
        Clear All. Both live in the same EXIF cache dir."""
        return self._exif_daytrip_files() + self._exif_master_files()

    def _files_stats(self, files: list[str]) -> tuple[int, int, str]:
        count = total = 0
        for p in files:
            try:
                total += os.path.getsize(p)
                count += 1
            except OSError:
                pass
        return count, total, str(Path(self._exif_file()).parent)

    def _exif_daytrip_stats(self) -> tuple[int, int, str]:
        return self._files_stats(self._exif_daytrip_files())

    def _exif_master_stats(self) -> tuple[int, int, str]:
        return self._files_stats(self._exif_master_files())

    def _exif_stats(self) -> tuple[int, int, str]:
        """Combined — used for the relocate prompt (moving the folder moves both)."""
        return self._files_stats(self._exif_all_files())

    # ── refresh ───────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        pie_sizes = [0.0] * len(_SLICE_NAMES)
        for row in self._rows:
            count, total, path = row["stat_fn"]()
            row["_count"] = count
            row["_total"] = total
            if count:
                row["size_lbl"].setText(f"{_fmt(total)} · {count} file(s)")
            else:
                row["size_lbl"].setText("Empty")
            from utils.cache_paths import display_path
            row["path_lbl"].setText(display_path(path))
            row["btn"].setEnabled(count > 0)
            if row.get("extra_btn") is not None and row.get("extra_stat_fn"):
                small_count, small_total = row["extra_stat_fn"]()
                row["extra_btn"].setEnabled(small_count > 0)
                row["extra_btn"].setText(
                    f"Remove low-res ({small_count})" if small_count
                    else "Remove low-res")
            pie_sizes[row["pie_index"]] += float(total)

        total_all = sum(pie_sizes)
        for i, sz in enumerate(pie_sizes):
            pct = f" ({sz / total_all * 100:.0f}%)" if total_all else ""
            self._legend_size_labels[i].setText(_fmt(int(sz)) + pct)

        self._pie.set_values(pie_sizes)
        self._clear_all_btn.setEnabled(any(r["_total"] > 0 for r in self._rows))

    # ── clear actions ─────────────────────────────────────────────────────────

    def _on_clear(self, row: dict) -> None:
        row["clear_fn"](row)

    # ── relocate ───────────────────────────────────────────────────────────────

    def _on_edit(self, row: dict) -> None:
        """Move a cache to a user-chosen folder and persist the override."""
        from utils.cache_paths import (
            cache_root, default_cache_root, display_path, relocate_cache,
            is_overridden,
        )
        kind = row["kind"]
        current = cache_root(kind)
        chosen = QFileDialog.getExistingDirectory(
            self, f"Choose a folder for the {row['title']} cache", str(current))
        if not chosen:
            return
        chosen_p = Path(chosen)
        if chosen_p.resolve() == current.resolve():
            # Picking the same folder is a no-op, but offer a reset if overridden.
            if is_overridden(kind) and QMessageBox.question(
                    self, "Reset to default",
                    "This folder is the current location. Reset this cache back to "
                    f"the default location?\n\n{display_path(default_cache_root(kind))}"
            ) == QMessageBox.StandardButton.Yes:
                relocate_cache(kind, default_cache_root(kind))
                self._refresh()
            return

        count, total, _ = row["edit_stat_fn"]()
        move = False
        if count > 0:
            ans = QMessageBox.question(
                self, "Move cache",
                f"Move the {row['title']} cache to:\n{chosen_p}\n\n"
                f"Move the existing {count} file(s) ({_fmt(total)}) there now? "
                f"(Choose No to leave them and start fresh — they become orphaned "
                f"in the old folder.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                return
            move = ans == QMessageBox.StandardButton.Yes

        try:
            relocate_cache(kind, chosen_p, move_existing=move)
        except OSError as e:
            QMessageBox.warning(self, "Move cache", f"Could not move the cache:\n{e}")
            return
        self._refresh()

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

    def _purge_small_previews(self) -> None:
        count, total = self._small_preview_stats()
        if count == 0:
            QMessageBox.information(self, "Remove low-res previews",
                                    "No low-res preview thumbnails to remove.")
            return
        if QMessageBox.question(
                self, "Remove low-res previews",
                f"Delete {count} low-res thumbnail(s) ({_fmt(total)}), keeping the "
                f"full-size previews?\n\nThey re-extract at full resolution the "
                f"next time you view those pictures."
        ) != QMessageBox.StandardButton.Yes:
            return
        from gui.fullres import purge_small_previews
        purge_small_previews()
        self._refresh()

    def _clear_exif_daytrip(self, row: dict) -> None:
        self._clear_exif_files(
            self._exif_daytrip_files(), self._exif_daytrip_stats(),
            "Clear day/trip EXIF cache",
            "The day/trip cache rebuilds on the next Read Directory (one slow "
            "scan). The master cache is untouched. Output is unaffected.")

    def _clear_exif_master(self, row: dict) -> None:
        self._clear_exif_files(
            self._exif_master_files(), self._exif_master_stats(),
            "Clear master EXIF cache",
            "The master cache rebuilds on the next Rescan master EXIF cache "
            "(a few minutes). The day/trip cache is untouched. Output is "
            "unaffected.")

    def _clear_exif_files(self, files: list[str], stats, title: str,
                          note: str) -> None:
        count, total, path = stats
        if count == 0:
            QMessageBox.information(self, title,
                                    f"No cache files found at:\n{path}")
            return
        if QMessageBox.question(
                self, title,
                f"Delete {count} file(s) ({_fmt(total)}) from:\n{path}\n\n⚠  {note}"
        ) != QMessageBox.StandardButton.Yes:
            return
        for p in files:
            try:
                os.remove(p)
            except OSError:
                pass
        self._refresh()

    def _on_clear_all(self) -> None:
        slice_counts = [0] * len(_SLICE_NAMES)
        slice_totals = [0] * len(_SLICE_NAMES)
        for r in self._rows:
            slice_counts[r["pie_index"]] += r["_count"]
            slice_totals[r["pie_index"]] += r["_total"]
        grand_total = sum(slice_totals)
        lines = "\n".join(
            f"  {_SLICE_NAMES[i]}: {_fmt(slice_totals[i])} ({slice_counts[i]} file(s))"
            for i in range(len(_SLICE_NAMES)) if slice_counts[i] > 0
        )
        msg = f"Delete all cached data ({_fmt(grand_total)} total)?\n\n{lines}"
        exif_present = slice_counts[2] > 0
        if exif_present:
            msg += ("\n\n⚠  The EXIF caches rebuild on the next Read Directory "
                    "and Rescan master EXIF cache (slow scans).")
        if QMessageBox.question(self, "Clear All Caches", msg) != QMessageBox.StandardButton.Yes:
            return
        from gui.fullres import clear_fullres_cache, clear_preview_cache
        clear_fullres_cache()
        clear_preview_cache()
        if exif_present:
            for p in self._exif_all_files():
                try:
                    os.remove(p)
                except OSError:
                    pass
        self._refresh()
