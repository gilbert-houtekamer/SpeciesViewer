"""Master Species Viewer — browse the master collection one species at a time.

A standalone counterpart to the Trip Species Viewer: it needs no active trip.
Pick a species from the master's own ~2000-species list (a searchable field, not
a 2000-row dropdown) and its complete master picture set appears as cards sorted
by Rate. Editing follows the existing master rules — stars/colour edits and
Blue/Purple colour-delete are live only when `master.readonly = no` on this
machine; otherwise it's a pure viewer.

Reuses the card grid + master read path wholesale: PictureCard, the
RatingEditorCollection provider in full-master mode (set_master(full=True)), the
MasterDB SQLite read cache, DeletePreviewDialog, and LargeImageView. The only
genuinely new code is the top bar (searchable species field) and wiring.
"""

import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget, QScrollArea,
    QGridLayout, QLabel, QLineEdit, QCompleter, QComboBox, QSpinBox, QPushButton,
    QStyle, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer

from engine.filepicture import FilePicture
from engine.filecsv import Files
from engine.picturecsv import Pictures
from gui.window_state import restore_window, save_window
from gui.image_loader import AsyncImageLoader
from gui.rating_editor import PictureCard, DeletePreviewDialog
from utils.config import _config, set_setting

logger = logging.getLogger("speciesviewer")


class MasterSpeciesViewerDialog(QDialog):
    """Scrollable grid of master picture cards for one selected species."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Master Species Viewer")
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self._geo_key = "MasterSpeciesViewer"
        _st = restore_window(self, self._geo_key, default_size=(1000, 720))
        self.columns = max(1, min(8, _st["cols"])) if _st["cols"] else 3

        self._loader = AsyncImageLoader(self)
        self._collection = None
        self._master_db = None
        self._master_root = None
        self._master_read_only = True
        self._species_lang = "NL"
        self._jpg_only = True
        self._cards = []
        self._cards_by_key = {}
        self._last_clicked_key = None
        self._current_sci = None       # casefolded scientific name in view
        self._current_display = None   # display name shown in the current language
        self._species_rows = []        # [{sci, nl, en, n}] from the master DB
        self._name_to_sci = {}         # casefolded any-language name -> sci
        self._sci_to_row = {}          # casefolded sci -> picker row

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 4)
        main_layout.setSpacing(4)

        # ── Top bar ────────────────────────────────────────────────────────
        top = QHBoxLayout()
        top.addWidget(QLabel("Species:"))
        self._species_field = QLineEdit()
        self._species_field.setMinimumWidth(240)
        self._species_field.setPlaceholderText("Type a Dutch, English or scientific name…")
        self._species_field.returnPressed.connect(self._on_species_entered)
        top.addWidget(self._species_field)

        self._lang_combo = QComboBox()
        self._lang_combo.addItems(["NL", "EN", "Sci"])
        self._lang_combo.setFixedWidth(55)
        self._lang_combo.setToolTip("Species name language")
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self._lang_combo)
        top.addStretch()

        self._jpg_btn = QPushButton("JPG only")
        self._jpg_btn.setCheckable(True)
        self._jpg_btn.setChecked(True)
        self._jpg_btn.setToolTip("Show only pictures that have a JPG file")
        self._jpg_btn.setStyleSheet("""
            QPushButton { border: 1px solid #aaa; border-radius: 3px; padding: 2px 6px; }
            QPushButton:checked { background-color: #c8e6c9; border-color: #4caf50; }
        """)
        self._jpg_btn.toggled.connect(self._on_jpg_only_toggled)
        top.addWidget(self._jpg_btn)
        top.addSpacing(8)

        from gui.image_viewer import ColorFilterBar
        self._color_filter_bar = ColorFilterBar(
            on_filter_changed=lambda _c: self._rebuild_cards())
        top.addWidget(self._color_filter_bar)
        top.addSpacing(8)

        top.addWidget(QLabel("Columns:"))
        self._col_spinner = QSpinBox()
        self._col_spinner.setRange(1, 8)
        self._col_spinner.setValue(self.columns)
        self._col_spinner.valueChanged.connect(self._on_columns_changed)
        top.addWidget(self._col_spinner)
        top.addSpacing(8)

        self._del_blue_btn = QPushButton("✖ Blue")
        self._del_blue_btn.setFixedSize(66, 24)
        self._del_blue_btn.setToolTip("Delete all Blue-tagged master pictures (move to master trash)")
        self._del_blue_btn.setStyleSheet("""
            QPushButton { border: 1px solid #1E90FF; border-radius: 3px;
                          padding: 2px 4px; background: transparent; color: #1E90FF;
                          font-weight: bold; }
            QPushButton:hover { background: rgba(30,144,255,0.1); }
        """)
        self._del_blue_btn.clicked.connect(lambda: self._batch_delete_color("Blue"))
        top.addWidget(self._del_blue_btn)

        self._del_purple_btn = QPushButton("✖ Purple")
        self._del_purple_btn.setFixedSize(76, 24)
        self._del_purple_btn.setToolTip("Delete all Purple-tagged master pictures (move to master trash)")
        self._del_purple_btn.setStyleSheet("""
            QPushButton { border: 1px solid #9370DB; border-radius: 3px;
                          padding: 2px 4px; background: transparent; color: #9370DB;
                          font-weight: bold; }
            QPushButton:hover { background: rgba(147,112,219,0.1); }
        """)
        self._del_purple_btn.clicked.connect(lambda: self._batch_delete_color("Purple"))
        top.addWidget(self._del_purple_btn)

        main_layout.addLayout(top)

        self._status = QLabel("Initializing…")
        self._status.setStyleSheet("color: #555; font-size: 10px;")
        main_layout.addWidget(self._status)

        # ── Scroll grid (same structure as the Trip viewer) ─────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_content = QWidget()
        _scroll_vbox = QVBoxLayout(self._scroll_content)
        _scroll_vbox.setContentsMargins(0, 0, 0, 0)
        _scroll_vbox.setSpacing(0)
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(6)
        _scroll_vbox.addWidget(self._grid_widget)
        _scroll_vbox.addStretch(1)
        self._scroll.setWidget(self._scroll_content)
        main_layout.addWidget(self._scroll)

        QTimer.singleShot(0, self._init_master)

    def closeEvent(self, event):
        save_window(self, self._geo_key, cols=self.columns)
        super().closeEvent(event)

    # ── Initialisation ─────────────────────────────────────────────────────
    def _init_master(self):
        master_root = _config.get("master", "masterbiotadatabase", fallback="").strip()
        if not master_root or not Path(master_root).exists():
            self._status.setText("No master database is configured — set one in Repository Settings.")
            self._set_editing_enabled(False)
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from db.master_db import MasterDB
            db = MasterDB(master_root)
            self._status.setText("Opening master database…")
            QApplication.processEvents()
            db.ensure_fresh(progress_cb=lambda msg: self._status.setText(msg))
        except Exception as e:
            QApplication.restoreOverrideCursor()
            logger.error(f"Master Species Viewer: master load failed: {e}", exc_info=True)
            self._status.setText("Master database load failed — see the log.")
            self._set_editing_enabled(False)
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._master_db = db
        self._master_root = master_root
        self._master_read_only = _config.get(
            "master", "readonly", fallback="yes").strip().lower() != "no"
        self._set_editing_enabled(not self._master_read_only)

        # Empty fpr shell so the reused provider/card code has the attributes it
        # expects; the master pictures come entirely from the DB (full mode).
        fpr = FilePicture(None)
        fpr.fi = Files(None)
        fpr.upn = Pictures(None)

        from gui.rating_provider import RatingEditorCollection
        self._collection = RatingEditorCollection(
            fpr, day_objects=None, upn_day_map={}, jpg_only=self._jpg_only,
            species_lang=self._species_lang, loader=self._loader,
            view_read_only=False)
        # full=True → show the species' COMPLETE master set (no trip cards);
        # read_only follows the machine's master policy (editable when off).
        self._collection.set_master(
            db, master_root, include=True,
            read_only=self._master_read_only, full=True)

        self._load_species_list()

    def _set_editing_enabled(self, enabled: bool):
        self._del_blue_btn.setVisible(enabled)
        self._del_purple_btn.setVisible(enabled)

    def _load_species_list(self):
        self._species_rows = self._master_db.species_picker_rows()
        self._name_to_sci = {}
        self._sci_to_row = {}
        candidates = []
        for row in self._species_rows:
            sci = (row.get("sci") or "").strip()
            if not sci:
                continue
            self._sci_to_row[sci.casefold()] = row
            for name in (sci, row.get("nl", ""), row.get("en", "")):
                name = (name or "").strip()
                if name:
                    self._name_to_sci.setdefault(name.casefold(), sci)
                    candidates.append(name)
        completer = QCompleter(sorted(set(candidates)), self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.activated.connect(lambda _t: QTimer.singleShot(0, self._on_species_entered))
        self._species_field.setCompleter(completer)
        ro = " (read-only)" if self._master_read_only else ""
        self._status.setText(
            f"{len(self._species_rows)} master species{ro} — type a name to view its pictures.")
        self._species_field.setFocus()

    # ── Species selection ──────────────────────────────────────────────────
    def _display_name_for(self, sci: str) -> str:
        row = self._sci_to_row.get((sci or "").casefold(), {})
        if self._species_lang == "EN":
            return (row.get("en") or row.get("nl") or sci or "").strip()
        if self._species_lang == "Sci":
            return (sci or row.get("nl") or "").strip()
        return (row.get("nl") or row.get("en") or sci or "").strip()

    def _on_species_entered(self):
        if self._collection is None:
            return
        text = self._species_field.text().strip()
        if not text:
            return
        sci = self._name_to_sci.get(text.casefold())
        if not sci:
            self._status.setText(f"“{text}” isn’t a master species — pick one from the list.")
            return
        self._current_sci = sci.casefold()
        self._current_display = self._display_name_for(sci)
        # Keep the field showing the resolved name in the current language.
        self._species_field.setText(self._current_display)
        self._rebuild_cards()

    def _on_lang_changed(self, _idx: int):
        self._species_lang = self._lang_combo.currentText()
        if self._current_sci:
            self._current_display = self._display_name_for(self._current_sci)
            self._species_field.setText(self._current_display)
            self._rebuild_cards()

    def _on_jpg_only_toggled(self, checked: bool):
        self._jpg_only = checked
        self._rebuild_cards()

    def _on_columns_changed(self, value: int):
        self.columns = value
        self._rebuild_cards()

    # ── Grid management (mirrors RatingEditorDialog) ───────────────────────
    def _grid_placement(self, total: int):
        ncols = self.columns
        spacing = self._grid.spacing()
        sb = self._scroll.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        frame = 2 * self._scroll.frameWidth()
        avail = self._scroll.width() - sb - frame - spacing
        if avail < 100:
            avail = 960
        card_w = max(120, (avail - spacing * (ncols - 1)) // ncols)
        return [(idx // ncols, idx % ncols, 1, card_w) for idx in range(total)]

    def _clear_grid(self):
        if self._collection is not None:
            self._collection.cancel_pending_loads()
        self._cards_by_key.clear()
        for c in range(8):
            self._grid.setColumnStretch(c, 0)
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._cards.clear()

    def _rebuild_cards(self):
        if self._collection is None:
            return
        self._clear_grid()
        self._last_clicked_key = None
        if not self._current_sci:
            return
        self._collection.set_species_lang(self._species_lang)
        self._collection.set_jpg_only(self._jpg_only)
        self._collection.set_color_filter(self._color_filter_bar.selected_colors)
        self._collection.rebuild(self._current_display, forced_sci=self._current_sci)
        n = len(self._collection)
        placement = self._grid_placement(n)
        for c in range(self.columns):
            self._grid.setColumnStretch(c, 1)
        for idx in range(n):
            item = self._collection.item(idx)
            r, col, span, card_w = placement[idx]
            card = PictureCard(item, card_w, show_kind_tag=False)
            card.card_changed.connect(self._on_card_changed)
            card.card_clicked.connect(self._on_card_cursor)
            card.card_double_clicked.connect(self._on_card_clicked)
            card.card_right_clicked.connect(self._on_card_context_menu)
            self._grid.addWidget(card, r, col, 1, span)
            self._cards.append(card)
            self._cards_by_key[item.key] = card
        self._status.setText(
            f"{n} master picture{'s' if n != 1 else ''} for {self._current_display}")
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))

    # ── Card interactions (ported from RatingEditorDialog) ─────────────────
    def _on_card_cursor(self, upn_key: str):
        prev = self._cards_by_key.get(self._last_clicked_key) if self._last_clicked_key else None
        if prev:
            prev.set_cursor_focus(False)
        self._last_clicked_key = upn_key
        card = self._cards_by_key.get(upn_key)
        if card:
            card.set_cursor_focus(True)

    def _on_card_clicked(self, upn_key: str):
        self._on_card_cursor(upn_key)
        from gui.large_image_view import LargeImageView
        if self._collection is None:
            return
        existing = getattr(self, "_large_view", None)
        if existing is not None:
            try:
                existing.close()
            except RuntimeError:
                pass
            self._large_view = None
        keys = [c.upn_key for c in self._cards]
        if upn_key not in keys:
            return
        dlg = LargeImageView(self, self._collection, keys, keys.index(upn_key),
                             settings_key="MasterSpeciesViewer", auto_advance=True)
        dlg.picture_changed.connect(self._on_large_view_changed)
        dlg.picture_navigated.connect(self._on_large_view_navigated)
        self._large_view = dlg
        dlg.finished.connect(lambda _=0: setattr(self, "_large_view", None))
        dlg.show()

    def _on_large_view_changed(self, key: str):
        card = self._cards_by_key.get(key)
        if card:
            card.refresh()
        self._resort_if_needed(focus_key=key)
        self._status.setText(f"Saved {key}")

    def _on_large_view_navigated(self, key: str):
        self._on_card_cursor(key)
        card = self._cards_by_key.get(key)
        if card:
            self._scroll.ensureWidgetVisible(card)

    def _on_card_changed(self, upn_key: str):
        self._resort_if_needed(focus_key=upn_key)
        self._status.setText(f"Saved {upn_key}")

    def _on_card_context_menu(self, upn_key: str):
        # Every card here is a master picture → master add/delete menu.
        self._master_context_menu(upn_key)

    def _master_context_menu(self, upn_key: str):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        col = self._collection
        if col is None:
            return
        if col.master_read_only:
            menu = QMenu(self)
            info = menu.addAction("Master is read-only — enable in Repository Settings")
            info.setEnabled(False)
            menu.exec(QCursor.pos())
            return
        menu = QMenu(self)
        act_add = menu.addAction("Add new files for this picture (scan folder)")
        act_del = None
        file_acts = {}
        if col.master_upn_deletable(upn_key):
            menu.addSeparator()
            act_del = menu.addAction("Delete picture (move to trash)")
            nonjpg = col.master_nonjpg_files(upn_key)
            if len(nonjpg) > 1:
                sub = menu.addMenu("Delete single file")
                for fn in nonjpg:
                    file_acts[sub.addAction(fn)] = fn
        elif col.master_upn_has_jpg(upn_key):
            menu.addSeparator()
            info = menu.addAction("Delete blocked — picture has a JPG (protected)")
            info.setEnabled(False)
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen == act_add:
            self._do_master_add(upn_key)
        elif act_del is not None and chosen == act_del:
            self._do_master_delete(upn_key)
        elif chosen in file_acts:
            self._do_master_delete_file(upn_key, file_acts[chosen])

    def _do_master_delete(self, upn_key: str):
        from db.master_mutate import MasterMutateError
        files = self._collection.master_nonjpg_files(upn_key)
        if QMessageBox.question(
                self, "Delete master picture",
                f"Move {len(files)} file(s) to the master trash and remove this "
                f"picture from the collection?\n\nFiles can be restored from "
                f".trash until purged.") != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self._collection.delete_master_picture(upn_key)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Delete blocked", str(e))
            return
        self._status.setText(
            f"Deleted — {len(res['trashed'])} file(s) moved to .trash. "
            f"Run Master → Recount species to refresh rollups.")
        self._rebuild_cards()

    def _do_master_delete_file(self, upn_key: str, fname: str):
        from db.master_mutate import MasterMutateError
        if QMessageBox.question(
                self, "Delete file",
                f"Move {fname} to the master trash? The picture keeps its other "
                f"files.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self._collection.delete_master_file(upn_key, fname)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Delete blocked", str(e))
            return
        self._status.setText(f"Deleted {fname} (moved to .trash).")
        self._rebuild_cards()

    def _do_master_add(self, upn_key: str):
        from db.master_mutate import MasterMutateError
        try:
            res = self._collection.add_master_files(upn_key)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Add files", str(e))
            return
        if not res["added"]:
            self._status.setText(
                "No new files found for this picture in its species folder.")
            return
        parts = []
        if res["jpg"]:
            parts.append(f"{res['jpg']} JPG" + ("s" if res["jpg"] != 1 else ""))
        if res["meta"]:
            parts.append(f"{res['meta']} metafile" + ("s" if res["meta"] != 1 else ""))
        self._status.setText(f"Added {' and '.join(parts)} to the picture.")
        self._rebuild_cards()

    # ── Batch colour-delete (master-only) ──────────────────────────────────
    def _batch_delete_color(self, color: str):
        if self._collection is None or self._master_read_only:
            return
        if not self._current_sci:
            QMessageBox.information(self, f"Delete {color}",
                                    "Select a species first.")
            return
        scope = f"{self._current_display} — master"
        _trip_keys, master_keys = self._collection.color_matches(
            color, species=self._current_display, day=None)
        if not master_keys:
            QMessageBox.information(self, f"No {color} pictures",
                                    f"No {color.lower()} master pictures in {scope}.")
            return
        deletable = [k for k in master_keys if self._collection.master_upn_deletable(k)]
        prot = len(master_keys) - len(deletable)
        lines = [f"{len(master_keys)} {color.lower()} master picture"
                 f"{'s' if len(master_keys) != 1 else ''} in {scope}."]
        if deletable:
            lines.append(f"Master → master .trash: {len(deletable)}")
        if prot:
            lines.append(f"Skipped (have a JPG): {prot}")
        if not deletable:
            QMessageBox.information(self, f"Delete {color}", "\n".join(lines))
            return

        show_thumbs = _config.get("ratingeditor", "delete_preview",
                                  fallback="yes").strip().lower() != "no"
        dlg = DeletePreviewDialog(
            self, self._collection, deletable, color=color, scope=scope,
            breakdown=lines[1:], show_thumbnails=show_thumbs)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.show_preview_pref() != show_thumbs:
            set_setting("ratingeditor", "delete_preview",
                        "yes" if dlg.show_preview_pref() else "no")

        m_deleted = 0
        for k in deletable:
            try:
                self._collection.delete_master_picture(k)
                m_deleted += 1
            except Exception as e:
                logger.error(f"Master delete {k} failed: {e}")
        self._status.setText(
            f"Deleted {m_deleted} master {color.lower()} picture(s). "
            f"Run Master → Recount species to refresh rollups.")
        self._rebuild_cards()

    # ── Re-sort after an edit ──────────────────────────────────────────────
    def _resort_if_needed(self, focus_key: str = None):
        if self._collection is None or not self._current_sci:
            return
        self._collection.rebuild(self._current_display, forced_sci=self._current_sci)
        new_order = [self._collection.item(i).key for i in range(len(self._collection))]
        old_order = [c.upn_key for c in self._cards]
        if old_order == new_order:
            return
        cards_by_key = {c.upn_key: c for c in self._cards}
        while self._grid.count():
            self._grid.takeAt(0)
        placement = self._grid_placement(len(new_order))
        self._cards.clear()
        for idx, key in enumerate(new_order):
            if key not in cards_by_key:
                continue
            c = cards_by_key[key]
            r, col, span, _ = placement[idx]
            self._grid.addWidget(c, r, col, 1, span)
            self._cards.append(c)
        focus_card = cards_by_key.get(focus_key) if focus_key else None
        if focus_card is not None:
            QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(focus_card))
        else:
            QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))
