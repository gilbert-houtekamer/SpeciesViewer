"""Shared result dialog for the "Create JPG (embedded + crop)" action.

Shows what was written — a thumbnail for a single file, and the path as a
reveal-in-Explorer link — instead of a bare "Created 1 JPG." message box. Used
by the culling grid (image_viewer) and the large viewer (large_image_view)."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt

from gui import theme


def _open_link(href: str) -> None:
    from utils.open_path import reveal_path, open_path
    p = Path(href)
    if p.is_file():
        reveal_path(href)      # select the file in Explorer / Finder
    else:
        open_path(href)        # open the folder


def show_create_jpg_result(parent, created, skipped=0, no_raw=0, failed=0) -> None:
    """Small modal summarising a Create-JPG run. `created` is the list of written
    JPG paths; the rest are counts."""
    created = [str(p) for p in created]
    dlg = QDialog(parent)
    dlg.setWindowTitle("Create JPG")
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(16, 14, 16, 12)
    lay.setSpacing(10)

    # A thumbnail confirms the crop landed, for the common single-file case.
    if len(created) == 1:
        pm = QPixmap(created[0])
        if not pm.isNull():
            thumb = QLabel()
            thumb.setPixmap(pm.scaled(
                260, 195, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(thumb)

    link = theme.READY_FG          # theme-aware accent — readable on light AND dark
    muted = theme.INK_SOFT
    lines: list[str] = []
    if created:
        if len(created) == 1:
            p = Path(created[0])
            lines.append(f'✓ Created <a href="{p}" style="color:{link}">{p.name}</a>')
            lines.append(f"<span style='color:{muted}'>{p.parent}</span>")
        else:
            folder = Path(created[0]).parent
            lines.append(f"✓ Created <b>{len(created)}</b> JPGs in")
            lines.append(f'<a href="{folder}" style="color:{link}">{folder}</a>')
    if skipped:
        lines.append(f"Skipped {skipped} (already had a JPG)")
    if no_raw:
        lines.append(f"{no_raw} without a RAW")
    if failed:
        lines.append(f"{failed} failed (no embedded preview?)")
    if not lines:
        lines.append("Nothing to do.")

    lbl = QLabel("<br>".join(lines))
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setOpenExternalLinks(False)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    lbl.linkActivated.connect(_open_link)
    lay.addWidget(lbl)

    row = QHBoxLayout()
    row.addStretch(1)
    ok = QPushButton("OK")
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    row.addWidget(ok)
    lay.addLayout(row)
    dlg.exec()
