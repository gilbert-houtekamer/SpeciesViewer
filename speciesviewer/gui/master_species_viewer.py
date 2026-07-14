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
        self._lang_combo.setFixedWidth(72)
        self._lang_combo.setToolTip("Species name language")
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self._lang_combo)
        top.addSpacing(12)

        # Camera filter + sort order — mirror the Trip Species Viewer. The camera
        # list is scoped to the species in view (populated after each rebuild).
        self._camera_combo = QComboBox()
        self._camera_combo.setMinimumWidth(130)
        self._camera_combo.setToolTip("Show only master pictures from this camera body")
        self._camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        top.addWidget(self._camera_combo)
        top.addSpacing(8)

        self._sort_combo = QComboBox()
        for label, data in (("Rate ↓", "rate"),
                            ("Time ↑", "time_asc"),
                            ("Time ↓", "time_desc"),
                            ("Camera", "camera")):
            self._sort_combo.addItem(label, data)
        self._sort_combo.setToolTip(
            "Card order — Rate (best first), capture time (old→new / new→old), "
            "or camera body")
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        top.addWidget(self._sort_combo)
        top.addSpacing(8)

        self._map_btn = QPushButton("Map…")
        self._map_btn.setToolTip("Show this species' GPS points on a map (distribution)")
        self._map_btn.clicked.connect(self._on_show_map)
        top.addWidget(self._map_btn)

        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setToolTip(
            "Re-read this species from the master and repaint.\n"
            "Also ingests any new file rendered into the species folder since the "
            "last look — a fresh DxO JPG is invisible to the master until something "
            "scans for it, which is why a regenerated page can come back unchanged.")
        self._refresh_btn.setMaximumWidth(32)
        self._refresh_btn.clicked.connect(self._on_refresh)
        top.addWidget(self._refresh_btn)

        self._regen_btn = QPushButton("↻ Web")
        self._regen_btn.setToolTip(
            "Rebuild this species' website page from the current master data and "
            "upload it — page AND the pictures it links to — to the live site")
        self._regen_btn.clicked.connect(self._on_regenerate)
        top.addWidget(self._regen_btn)
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
        # Regenerate re-publishes to the live site from the full master — a
        # writable-master (BigBoy) action; hide it on a read-only SmallMaster.
        self._regen_btn.setVisible(enabled)

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
        # A new species starts unfiltered by camera — its bodies differ, and the
        # list is only known after the rebuild below (avoids a stale filter that
        # would show an empty grid). blockSignals: no extra rebuild.
        self._camera_combo.blockSignals(True)
        self._camera_combo.setCurrentIndex(0)
        self._camera_combo.blockSignals(False)
        self._rebuild_cards()

    def _on_lang_changed(self, _idx: int):
        self._species_lang = self._lang_combo.currentText()
        if self._current_sci:
            self._current_display = self._display_name_for(self._current_sci)
            self._species_field.setText(self._current_display)
            self._rebuild_cards()

    def _on_refresh(self):
        """Re-read this species from the master, ingesting anything new on disk.

        The master learns about a file only when something scans for it. Render a
        tighter crop in PhotoLab and the species folder gains a `_DxO.jpg` that no
        row anywhere mentions — so "Regenerate page" faithfully rebuilds the page
        from the files it knows, and the page comes back unchanged. This is the
        button that closes that gap."""
        if self._collection is None or not self._current_sci:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        added, resized, rerated, failed = [], [], [], 0
        try:
            self._master_db.ensure_fresh()
            if not self._master_read_only:
                pics = self._master_db.pictures_for_species(self._current_sci)
                keys = [p["UniquePictureName"] for p in pics
                        if p.get("UniquePictureName")]
                # Whole species per call, never per picture: ONE folder listing + ONE
                # exiftool call ingests whatever is new and corrects any JPG whose pixels
                # moved under us (a PhotoLab re-export over its own name).
                try:
                    res = self._collection.refresh_master_species(pics)
                    added = res.get("added", []) or []
                    resized = res.get("resized", []) or []
                except Exception as e:
                    failed += 1
                    logger.warning(f"refresh: species scan failed: {e}", exc_info=True)
                # Stars/colour an external tool wrote into a sidecar are neither a new
                # file nor a changed pixel size, so nothing above sees them. A second
                # batched read — again ONE exiftool call for the whole species.
                if keys:
                    self._collection.rebuild(self._current_display,
                                             forced_sci=self._current_sci)
                    try:
                        rerated = self._collection.resync_master_metadata(keys)
                    except Exception as e:
                        logger.warning(f"refresh: metadata resync failed: {e}")
        finally:
            QApplication.restoreOverrideCursor()

        self._rebuild_cards()
        bits = []
        if added:
            names = ", ".join(added[:3]) + (f" +{len(added) - 3} more"
                                            if len(added) > 3 else "")
            bits.append(f"ingested {len(added)} new file(s): {names}")
        if resized:
            r = resized[0]
            more = f" +{len(resized) - 1} more" if len(resized) > 1 else ""
            bits.append(f"corrected {len(resized)} changed JPG(s): "
                        f"{r['fname']} {r['old']} → {r['new']}{more}")
        if rerated:
            c = rerated[0]
            more = f" +{len(rerated) - 1} more" if len(rerated) > 1 else ""
            bits.append(f"picked up {len(rerated)} rating/colour change(s) from disk: "
                        f"{c['key']} {c['rating'][0] or '0'}→{c['rating'][1]} stars{more}")
        if bits:
            self._status.setText("Refreshed — " + "; ".join(bits) +
                                 ". Right-click a card to choose which JPG publishes.")
        elif failed:
            self._status.setText(
                f"Refreshed — {failed} picture(s) could not be scanned (see the log).")
        else:
            self._status.setText("Refreshed — nothing new or changed on disk.")

    def _on_jpg_only_toggled(self, checked: bool):
        self._jpg_only = checked
        self._rebuild_cards()

    def _on_camera_changed(self, _idx: int):
        self._rebuild_cards()

    def _on_sort_changed(self, _idx: int):
        self._rebuild_cards()

    def _selected_cameras(self) -> set:
        model = self._camera_combo.currentData()
        return {model} if model else set()

    def _populate_camera_combo(self):
        """Refill the camera combo from the species currently in view (its master
        rows are loaded by the preceding rebuild). blockSignals keeps this from
        re-triggering a rebuild; the current pick is preserved when still present."""
        if self._collection is None:
            return
        from gui.rating_provider import NO_CAMERA
        prev = self._camera_combo.currentData()
        self._camera_combo.blockSignals(True)
        self._camera_combo.clear()
        self._camera_combo.addItem("All cameras", None)
        for model in self._collection.available_cameras(self._current_display):
            # The no-camera bucket is a real, selectable group — the waarneming.nl
            # imports never recorded a body — so give it a name instead of a blank row.
            # Reads as a camera name in a list of camera names, which is what it is.
            label = "Unknown" if model == NO_CAMERA else model
            self._camera_combo.addItem(label, model)
        if prev:
            idx = self._camera_combo.findData(prev)
            self._camera_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._camera_combo.blockSignals(False)

    # ── Regenerate this species' website page ──────────────────────────────
    def _on_regenerate(self):
        if not self._current_sci:
            QMessageBox.information(self, "Regenerate page",
                                    "Type a species name first, then regenerate its page.")
            return
        name = self._current_display or self._current_sci
        if QMessageBox.question(
                self, "Regenerate species page",
                f"Rebuild the website page for “{name}” from the current master "
                f"data and upload it to the live website?\n\n"
                f"This re-picks the best photos (per age/sex/plumage) and "
                f"republishes just this species' page. It does not change your "
                f"master pictures.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        from PyQt6.QtWidgets import QProgressDialog
        prog = QProgressDialog("Regenerating…", None, 0, 0, self)
        prog.setWindowTitle("Regenerate species page")
        prog.setWindowModality(Qt.WindowModality.ApplicationModal)
        prog.setCancelButton(None)
        prog.setMinimumDuration(0)
        prog.show()
        QApplication.processEvents()

        def _cb(msg):
            prog.setLabelText(msg)
            QApplication.processEvents()

        try:
            from engine.regenerate_species import regenerate_species_page
            res = regenerate_species_page(
                self._current_sci, self._master_db, progress_cb=_cb)
        except Exception as e:
            logger.error(f"Regenerate species failed: {e}", exc_info=True)
            res = {"ok": False, "error": str(e)}
        finally:
            prog.close()

        if res.get("ok"):
            self._status.setText(f"Regenerated and uploaded the page for {name}.")
            from urllib.parse import quote
            base = _config.get("websettings", "base_url", fallback="").strip().rstrip("/")
            if not base:
                base = "https://houtekamer.com"
            elif not base.startswith("http"):
                base = "https://" + base
            links = []
            for u in res.get("urls", []):
                display = base + u
                href = base + quote(u, safe="/")   # %20-encode spaces, keep slashes
                links.append(f'<a href="{href}">{display}</a>')
            box = QMessageBox(self)
            box.setWindowTitle("Regenerate species page")
            box.setIcon(QMessageBox.Icon.Information)
            box.setTextFormat(Qt.TextFormat.RichText)
            box.setText(f"Done — the page for “{name}” was rebuilt and uploaded."
                        f"<br><br>" + "<br>".join(links))
            box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            box.exec()
        else:
            QMessageBox.warning(self, "Regenerate species page",
                                res.get("error", "Regeneration failed — see the log."))

    def _on_columns_changed(self, value: int):
        self.columns = value
        self._rebuild_cards()

    def _on_show_map(self):
        if not self._current_sci or self._master_db is None:
            QMessageBox.information(self, "Map",
                                    "Type a species name first, then open the map.")
            return
        points = self._master_db.gps_points_for_species(self._current_sci)
        if not points:
            QMessageBox.information(
                self, "Map",
                f"No GPS points for {self._current_display}.\n\n"
                f"About 40% of the master has no GPS — older material predates "
                f"in-camera GPS. (Map-based backfill is a later phase.)")
            return
        from gui.map_view import MapView
        dlg = MapView(self, self._master_db, self._current_sci,
                      self._current_display or self._current_sci, points)
        dlg.show()

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
        self._collection.set_camera_filter(self._selected_cameras())
        self._collection.set_sort(self._sort_combo.currentData())
        self._collection.rebuild(self._current_display, forced_sci=self._current_sci)
        # Refresh the camera list now that this species' master rows are loaded.
        self._populate_camera_combo()
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

        # Which JPG is the picture's public face. Only worth asking when there is
        # more than one to choose between — with a single render the code and the
        # human cannot disagree.
        pin_acts, unpin_act, jpgdel_acts = {}, None, {}
        jpgs = col.master_jpgs(upn_key)
        if len(jpgs) > 1:
            menu.addSeparator()
            sub = menu.addMenu("Use this JPG on the website")
            for j in jpgs:
                mark = " ✓ shown now" if j["best"] else ""
                pin = " 📌" if j["pinned"] else ""
                a = sub.addAction(f"{j['fname']}   ({j['size']}){mark}{pin}")
                a.setCheckable(True)
                a.setChecked(bool(j["pinned"]))
                pin_acts[a] = j["fname"]
            if any(j["pinned"] for j in jpgs):
                sub.addSeparator()
                unpin_act = sub.addAction("Let the code choose (biggest wins)")
            dsub = menu.addMenu("Delete a JPG (move to trash)")
            for j in jpgs:
                jpgdel_acts[dsub.addAction(f"{j['fname']}   ({j['size']})")] = j["fname"]

        act_move = None
        if col.master_upn_moveable(upn_key):
            act_move = menu.addAction("Move to existing species…")
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
            info = menu.addAction("Delete / Move blocked — picture has a JPG (protected)")
            info.setEnabled(False)
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen == act_add:
            self._do_master_add(upn_key)
        elif chosen in pin_acts:
            self._do_pin_jpg(upn_key, pin_acts[chosen])
        elif unpin_act is not None and chosen == unpin_act:
            self._do_pin_jpg(upn_key, "")
        elif chosen in jpgdel_acts:
            self._do_master_delete_jpg(upn_key, jpgdel_acts[chosen])
        elif act_move is not None and chosen == act_move:
            self._do_master_move(upn_key)
        elif act_del is not None and chosen == act_del:
            self._do_master_delete(upn_key)
        elif chosen in file_acts:
            self._do_master_delete_file(upn_key, file_acts[chosen])

    def _do_pin_jpg(self, upn_key: str, fname: str):
        if not self._collection.pin_master_jpg(upn_key, fname):
            QMessageBox.warning(self, "Choose JPG", "Could not set the picture's JPG.")
            return
        if fname:
            self._status.setText(
                f"{fname} is now this picture's website JPG. "
                f"Regenerate the page to publish it.")
        else:
            self._status.setText(
                "Back to the code's choice (biggest JPG wins). "
                "Regenerate the page to publish it.")
        self._rebuild_cards()

    def _do_master_delete_jpg(self, upn_key: str, fname: str):
        from db.master_mutate import MasterMutateError
        jpgs = self._collection.master_jpgs(upn_key)
        last = len([j for j in jpgs if j["fname"] != fname]) == 0
        extra = ("\n\nThis is the picture's LAST JPG — it will have no website "
                 "picture until you render a new one." if last else "")
        if QMessageBox.question(
                self, "Delete JPG",
                f"Move {fname} to the master trash?\n\nThe picture keeps its RAW and "
                f"its other renders. Restorable from .trash until purged.{extra}"
                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self._collection.delete_master_jpg(upn_key, fname)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Delete blocked", str(e))
            return
        self._status.setText(
            f"Deleted {fname} — {len(res['trashed'])} file(s) moved to .trash. "
            f"Regenerate the page to publish the change.")
        self._rebuild_cards()

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
            f"Deleted — {len(res['trashed'])} file(s) moved to .trash; "
            f"species counts updated.")
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

    def _do_master_move(self, upn_key: str):
        from db.master_mutate import MasterMutateError
        targets = self._collection.master_species_targets()
        if not targets:
            QMessageBox.information(self, "Move to species",
                                    "No master species list is available.")
            return
        target = self._ask_master_species(upn_key, targets)
        if not target:
            return
        # Confirm before relocating real master files on disk.
        n_files = len(self._collection.master_nonjpg_files(upn_key))
        tname = target.get("nl") or target.get("sci")
        src = self._current_display or "its current species"
        if QMessageBox.question(
                self, "Move to species",
                f"Move this picture from “{src}” to “{tname}”?\n\n"
                f"{n_files} file(s) will be relocated into the {tname} folder, the "
                f"species keyword rewritten, and the master database updated. "
                f"This is reversible (files are snapshotted).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self._collection.move_master_picture(upn_key, target)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Move blocked", str(e))
            return
        except Exception as e:
            logger.error(f"Master move {upn_key} failed: {e}", exc_info=True)
            QMessageBox.warning(self, "Move failed", str(e))
            return
        self._status.setText(
            f"Moved to {res['target']} — {len(res['moved'])} file(s) relocated; "
            f"species counts updated.")
        self._rebuild_cards()

    def _ask_master_species(self, upn_key: str, targets: list[dict]) -> dict | None:
        """Searchable picker over the whole master species list (species.csv) —
        the master's Move targets are any species ever seen, not just those with
        pictures. Returns the chosen {"sci","nl","en","target_path"} row, or None."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox
        name_to_row: dict[str, dict] = {}
        names: list[str] = []
        for row in targets:
            for k in ("nl", "en", "sci"):
                v = (row.get(k) or "").strip()
                if v:
                    name_to_row.setdefault(v.casefold(), row)
                    names.append(v)
        names = sorted(set(names), key=str.casefold)
        dlg = QDialog(self)
        dlg.setWindowTitle("Move to existing species")
        dlg.setMinimumWidth(440)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Move this picture to (any species ever seen):"))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(names)
        combo.setCurrentIndex(-1)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.lineEdit().setPlaceholderText("Type a Dutch, English or scientific name…")
        completer = QCompleter(names, dlg)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        combo.setCompleter(completer)
        lay.addWidget(combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        combo.setFocus()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        chosen = combo.currentText().strip()
        row = name_to_row.get(chosen.casefold())
        if row is None:
            QMessageBox.warning(self, "Unknown species",
                                f"“{chosen}” isn’t a master species — pick one "
                                f"from the list.")
            return None
        return row

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
            f"Deleted {m_deleted} master {color.lower()} picture(s); "
            f"species counts updated.")
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
