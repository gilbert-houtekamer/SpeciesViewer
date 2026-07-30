"""Biota Browser — the desktop twin of the website's biota tree.

Walks the taxonomic tree (Kingdom → … → Species) for a scope, showing each level
as a grid of olive-framed cards. Click a taxon to drill in; click a species to see
its complete picture set INLINE in the same window. A left rank rail jumps to any
ancestor rank; a rank filter + search box mirror the website's "search at a
selected taxonomic level". The model is a BiotaSource (gui/biota_source.py) so the
same view serves Trip / Album / Master unchanged — only MasterBiotaSource is wired
today.

Styling follows the Trip-lifecycle board palette; the frame around every photo is
the site's own olive (#6d8764), so it reads as the same collection on the desktop.

Two modes share one window:
  • tree     — olive TaxonCards (child taxa or species leaves), drill on click.
  • species  — the clicked species' master pictures, reusing the SAME machinery as
               the Master Species Viewer: RatingEditorCollection (full master mode)
               + PictureCard + LargeImageView + the master card context menu.
"""

import logging

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget, QScrollArea,
    QGridLayout, QLabel, QLineEdit, QComboBox, QSpinBox, QPushButton, QFrame,
    QStyle,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

from gui.window_state import restore_window, save_window
from gui.image_loader import AsyncImageLoader
from gui.master_ops import MasterCardMenuMixin
from gui.biota_source import RANK_LABEL, RANK_ORDER
from utils.config import _config

logger = logging.getLogger("speciesviewer")

# ── palette (Trip-lifecycle board, light) + the website's olive frame ────────
_INK = "#1c211b"; _INK_SOFT = "#59634f"; _LINE = "#d7ddce"
_SURFACE = "#ffffff"; _BG = "#f5f7f3"
_FRAME = "#6d8764"; _FRAME_INK = "#ffffff"; _MAT = "#ffffff"
_DONE = "#2f7d43"; _DONE_BG = "#e6f1e8"; _READY = "#2563a8"; _READY_BG = "#e4eef8"


def _load_qimage(path):
    """Worker-thread producer: decode a cover JPG (or nothing)."""
    if path is None:
        return None
    img = QImage(str(path))
    return None if img.isNull() else img


class TaxonCard(QFrame):
    """One olive-framed card — a child taxon or a species leaf. Emits its
    scientific name on a left click."""
    clicked = pyqtSignal(str)

    def __init__(self, node, sub_html, card_w, cover_path, loader):
        super().__init__()
        self.setObjectName("tcard")
        self._sci = node["sci"]
        self.setFixedWidth(card_w)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame#tcard {{ background:{_SURFACE}; border:1px solid {_LINE};"
            f" border-radius:9px; }}"
            f"QFrame#tcard:hover {{ border-color:{_FRAME}; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # olive frame → white mat → photo
        frame = QFrame()
        frame.setStyleSheet(f"background:{_FRAME}; border-top-left-radius:9px;"
                            f" border-top-right-radius:9px;")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(6, 6, 6, 6)
        self._photo = QLabel()
        self._photo.setFixedHeight(int((card_w - 12) * 0.8))
        self._photo.setStyleSheet(f"background:#e9ece3; border:3px solid {_MAT};"
                                  f" border-radius:3px; color:{_INK_SOFT};")
        self._photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rank = node.get("rank", "")
        self._photo.setText(RANK_LABEL.get(rank, rank))
        fl.addWidget(self._photo)
        lay.addWidget(frame)

        meta = QWidget()
        ml = QVBoxLayout(meta)
        ml.setContentsMargins(11, 9, 11, 11)
        ml.setSpacing(1)
        nm = QLabel(node["_display"])
        nm.setStyleSheet(f"color:{_INK}; font-weight:600; font-size:14px; border:none;")
        nm.setWordWrap(True)
        ml.addWidget(nm)
        # Secondary line: the scientific name (italic) — UNLESS the title already IS
        # the scientific name (Sci profile / a taxon with no vernacular), in which case
        # show a common name instead. Never repeat what the title already shows.
        disp_cf = (node["_display"] or "").strip().casefold()
        if disp_cf and disp_cf == (node["sci"] or "").strip().casefold():
            secondary, sec_italic = (node.get("en") or node.get("nl") or ""), False
        else:
            secondary, sec_italic = node["sci"], True
        if secondary:
            sci = QLabel(secondary)
            sci.setStyleSheet(f"color:{_INK_SOFT}; font-size:12px; border:none;"
                              + (" font-style:italic;" if sec_italic else ""))
            sci.setWordWrap(True)
            ml.addWidget(sci)
        stat = QLabel(sub_html)
        stat.setTextFormat(Qt.TextFormat.RichText)
        stat.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace;"
                           f" font-size:10.5px; border:none; margin-top:5px;")
        ml.addWidget(stat)
        lay.addWidget(meta)

        if cover_path is not None:
            loader.request(lambda p=cover_path: _load_qimage(p),
                           self._set_cover, target_w=card_w)

    def _set_cover(self, px: QPixmap):
        if px is None or px.isNull():
            return
        w = self._photo.width() or self.width() - 12
        h = self._photo.height()
        self._photo.setPixmap(px.scaled(
            w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation))
        self._photo.setText("")

    def mouseReleaseEvent(self, event):
        fire = (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.pos()))
        super().mouseReleaseEvent(event)
        if fire:
            self.clicked.emit(self._sci)


class BiotaBrowserDialog(MasterCardMenuMixin, QDialog):
    """Scope-agnostic biota tree browser. Master scope only for now."""

    # How many species cards to paint at once in the flat 'all species' view
    # before asking the user to narrow with the search box (the master has ~2200).
    _ALL_CAP = 400

    def __init__(self, parent, start_mode="tree"):
        super().__init__(parent)
        self._start_mode = start_mode   # "tree" | "allspecies"
        self.setWindowTitle("Biota Browser — Master")
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self._geo_key = "BiotaBrowser"
        _st = restore_window(self, self._geo_key, default_size=(1180, 780))
        self.columns = max(2, min(8, _st["cols"])) if _st["cols"] else 5

        self._loader = AsyncImageLoader(self)
        self._source = None
        self._cur = None            # current node scientific name
        self._mode = "tree"         # "tree" | "species"
        self._collection = None     # RatingEditorCollection (full master), for species mode
        self._master_db = None
        self._master_read_only = True
        self._species_view = "pretty"   # "pretty" (SpeciesPanel) | "tech" (grid)
        self._show_all_files = False     # pretty panel: reveal RAW-only frames
        self._pic_cards = []
        self._pic_by_key = {}
        self._large_view = None

        from utils.name_profile import effective_profile
        self._lang = effective_profile()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 4)
        outer.setSpacing(6)

        # ── top bar ─────────────────────────────────────────────────────────
        top = QHBoxLayout()
        self._scope_label = QLabel("Master collection")
        self._scope_label.setStyleSheet(
            f"color:{_DONE}; font-weight:700; font-size:13px;")
        top.addWidget(self._scope_label)
        top.addSpacing(14)

        self._all_btn = QPushButton("All species")
        self._all_btn.setCheckable(True)
        self._all_btn.setToolTip("Show every species as one flat, searchable list")
        self._all_btn.setStyleSheet(
            f"QPushButton {{ border:1px solid {_LINE}; border-radius:6px;"
            f" padding:4px 10px; }}"
            f"QPushButton:checked {{ background:{_DONE_BG}; border-color:{_DONE};"
            f" color:{_DONE}; font-weight:600; }}")
        self._all_btn.clicked.connect(self._on_all_species_toggled)
        top.addWidget(self._all_btn)
        top.addSpacing(10)

        top.addWidget(QLabel("Rank:"))
        self._level_combo = QComboBox()
        self._level_combo.addItem("Any rank", "ANY")
        for rk in RANK_ORDER:
            self._level_combo.addItem(RANK_LABEL[rk], rk)
        self._level_combo.currentIndexChanged.connect(lambda _i: self._render())
        top.addWidget(self._level_combo)
        top.addSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search names at this rank…")
        self._search.setMinimumWidth(240)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        top.addWidget(self._search, 1)

        top.addSpacing(6)
        from utils.name_profile import profile_keys, profile_short_label
        top.addWidget(QLabel("Names:"))
        self._lang_combo = QComboBox()
        for key in profile_keys():
            self._lang_combo.addItem(profile_short_label(key), key)
        _li = self._lang_combo.findData(self._lang)
        if _li >= 0:
            self._lang_combo.setCurrentIndex(_li)
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        top.addWidget(self._lang_combo)
        top.addSpacing(6)

        top.addWidget(QLabel("Columns:"))
        self._col_spin = QSpinBox()
        self._col_spin.setRange(2, 8)
        self._col_spin.setValue(self.columns)
        self._col_spin.valueChanged.connect(self._on_columns_changed)
        top.addWidget(self._col_spin)
        outer.addLayout(top)

        # ── breadcrumb ──────────────────────────────────────────────────────
        self._crumbs_host = QWidget()
        self._crumbs = QHBoxLayout(self._crumbs_host)
        self._crumbs.setContentsMargins(2, 0, 2, 0)
        self._crumbs.setSpacing(2)
        outer.addWidget(self._crumbs_host)

        self._status = QLabel("Initializing…")
        self._status.setStyleSheet(f"color:{_INK_SOFT}; font-size:11px;")
        outer.addWidget(self._status)

        # ── body: rank rail | grid ──────────────────────────────────────────
        body = QHBoxLayout()
        body.setSpacing(10)

        self._rail_host = QWidget()
        self._rail_host.setFixedWidth(178)
        self._rail = QVBoxLayout(self._rail_host)
        self._rail.setContentsMargins(2, 4, 6, 4)
        self._rail.setSpacing(2)
        body.addWidget(self._rail_host)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(2, 2, 2, 2)
        self._grid.setSpacing(12)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._grid_widget)
        body.addWidget(self._scroll, 1)

        # The pretty species panel's sidebar is docked HERE, outside the main
        # scroll, so narrowing the window reflows the content but never slides the
        # sidebar off-screen. Its own vertical scroll handles a long family list.
        # Hidden except in the pretty species view.
        self._sidebar_scroll = QScrollArea()
        self._sidebar_scroll.setWidgetResizable(True)
        self._sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._sidebar_scroll.setFixedWidth(258)
        self._sidebar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._sidebar_scroll.setVisible(False)
        body.addWidget(self._sidebar_scroll, 0)

        outer.addLayout(body, 1)

        QTimer.singleShot(0, self._init_master)

    def closeEvent(self, event):
        self._close_large_view()
        save_window(self, self._geo_key, cols=self.columns)
        super().closeEvent(event)

    # ── init ────────────────────────────────────────────────────────────────
    def _init_master(self):
        master_root = _config.get("master", "masterbiotadatabase", fallback="").strip()
        if not master_root:
            self._status.setText("No master database is configured — set one in Repository Settings.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from db.master_db import MasterDB
            from gui.biota_source import MasterBiotaSource
            db = MasterDB(master_root)
            self._status.setText("Opening master database…")
            QApplication.processEvents()
            db.ensure_fresh(progress_cb=lambda msg: self._status.setText(msg))
            self._source = MasterBiotaSource(db, master_root)
        except Exception as e:
            logger.error(f"Biota Browser: master load failed: {e}", exc_info=True)
            self._status.setText("Master database load failed — see the log.")
            QApplication.restoreOverrideCursor()
            return

        self._master_db = db
        self._master_read_only = _config.get(
            "master", "readonly", fallback="yes").strip().lower() != "no"

        # A provider in full-master mode powers the inline species grid — the same
        # object the Master Species Viewer uses. Built once; each species is a
        # rebuild(forced_sci=…) against it.
        try:
            from engine.filepicture import FilePicture
            from engine.filecsv import Files
            from engine.picturecsv import Pictures
            from gui.rating_provider import RatingEditorCollection
            fpr = FilePicture(None)
            fpr.fi = Files(None)
            fpr.upn = Pictures(None)
            self._collection = RatingEditorCollection(
                fpr, day_objects=None, upn_day_map={}, jpg_only=True,
                species_lang=self._lang, loader=self._loader, view_read_only=False)
            self._collection.set_master(
                db, master_root, include=True,
                read_only=self._master_read_only, full=True)
        except Exception as e:
            logger.error(f"Biota Browser: provider setup failed: {e}", exc_info=True)
            self._collection = None
        finally:
            QApplication.restoreOverrideCursor()

        self._scope_label.setText(self._source.scope_label)
        self._cur = self._source.root()
        if self._start_mode == "allspecies":
            self._mode = "allspecies"
            self._all_btn.setChecked(True)
        self._render()

    def _on_lang_changed(self, _i):
        self._lang = self._lang_combo.currentData()
        if self._collection is not None:
            self._collection.set_species_lang(self._lang)
        self._render()

    def _on_columns_changed(self, value):
        self.columns = value
        self._render()

    def _on_search_changed(self, _t):
        # A search is a query over the current view; leave an open species view
        # (but keep the flat all-species list — there the box just filters it).
        if self._mode == "species" and self._search.text().strip():
            self._mode = "tree"
        self._render()

    def _on_all_species_toggled(self, checked):
        self._close_large_view()
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        if checked:
            self._mode = "allspecies"
        else:
            self._mode = "tree"
            self._cur = self._source.root() if self._source else self._cur
        self._render()

    # ── naming ──────────────────────────────────────────────────────────────
    def _display(self, node) -> str:
        from utils.name_profile import name_for
        return name_for(node, self._lang) or node.get("sci", "")

    # ── breadcrumb ──────────────────────────────────────────────────────────
    def _rebuild_crumbs(self):
        while self._crumbs.count():
            item = self._crumbs.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not self._source or not self._cur:
            return
        if self._mode == "allspecies":
            here = QLabel("All species  ")
            here.setStyleSheet(f"color:{_INK}; font-weight:700;")
            self._crumbs.addWidget(here)
            tag = QLabel("FLAT INDEX")
            tag.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace;"
                              f" font-size:9px; letter-spacing:1px;")
            self._crumbs.addWidget(tag)
            self._crumbs.addStretch(1)
            return
        chain = self._source.chain(self._cur)
        for i, node in enumerate(chain):
            if i:
                sep = QLabel("›")
                sep.setStyleSheet(f"color:{_INK_SOFT};")
                self._crumbs.addWidget(sep)
            last = (i == len(chain) - 1)
            if last:
                here = QLabel(f"{node['sci']}  ")
                style = (f"color:{_INK}; font-weight:700;" if self._mode == "tree"
                         else f"color:{_INK}; font-weight:700; font-style:italic;")
                here.setStyleSheet(style)
                self._crumbs.addWidget(here)
                rk = QLabel(("RAWs" if self._mode == "species"
                             else RANK_LABEL.get(node["rank"], node["rank"]).upper()))
                rk.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace;"
                                 f" font-size:9px; letter-spacing:1px;")
                self._crumbs.addWidget(rk)
            else:
                link = QPushButton(node["sci"])
                link.setFlat(True)
                link.setCursor(Qt.CursorShape.PointingHandCursor)
                link.setStyleSheet(
                    f"QPushButton {{ color:{_FRAME}; font-weight:600; border:none;"
                    f" padding:2px 4px; text-align:left; }}"
                    f"QPushButton:hover {{ color:{_READY}; background:{_READY_BG};"
                    f" border-radius:5px; }}")
                link.clicked.connect(lambda _c=False, k=node["sci"]: self._go(k))
                self._crumbs.addWidget(link)
        self._crumbs.addStretch(1)

    # ── rank rail ───────────────────────────────────────────────────────────
    def _rebuild_rail(self):
        while self._rail.count():
            item = self._rail.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not self._source or not self._cur:
            return
        head = QLabel("JUMP TO RANK")
        head.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace;"
                           f" font-size:9px; letter-spacing:1px; padding:2px 6px;")
        self._rail.addWidget(head)

        if self._mode == "allspecies":
            note = QLabel("  Flat species index —\n  no tree position.")
            note.setStyleSheet(f"color:{_INK_SOFT}; font-size:11px; padding:6px 8px;")
            note.setWordWrap(True)
            self._rail.addWidget(note)
            self._rail.addStretch(1)
            return

        chain = self._source.chain(self._cur)
        by_rank = {n["rank"]: n for n in chain}
        cur_rank = self._source.node(self._cur)["rank"] if self._source.node(self._cur) else None
        for rk in RANK_ORDER:
            node = by_rank.get(rk)
            row = QPushButton()
            row.setFlat(True)
            row.setCursor(Qt.CursorShape.PointingHandCursor if node else Qt.CursorShape.ArrowCursor)
            name = node["sci"] if node else "—"
            row.setText(f"  {name}")
            row.setToolTip(RANK_LABEL[rk])
            is_cur = (node is not None and rk == cur_rank)
            if is_cur:
                row.setStyleSheet(
                    f"QPushButton {{ text-align:left; border:none; border-radius:6px;"
                    f" padding:5px 8px; color:{_DONE}; font-weight:600; background:{_DONE_BG}; }}")
            elif node:
                row.setStyleSheet(
                    f"QPushButton {{ text-align:left; border:none; border-radius:6px;"
                    f" padding:5px 8px; color:{_INK_SOFT}; }}"
                    f"QPushButton:hover {{ background:{_SURFACE}; color:{_INK}; }}")
                row.clicked.connect(lambda _c=False, k=node["sci"]: self._go(k))
            else:
                row.setEnabled(False)
                row.setStyleSheet(
                    f"QPushButton {{ text-align:left; border:none; padding:5px 8px;"
                    f" color:{_LINE}; }}")
            # rank tag on the right via a compact two-widget row
            wrap = QWidget()
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(0)
            wl.addWidget(row, 1)
            tag = QLabel(RANK_LABEL[rk].upper())
            tag.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace;"
                              f" font-size:8px; letter-spacing:1px; padding-right:6px;")
            tag.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            wl.addWidget(tag, 0)
            self._rail.addWidget(wrap)
        self._rail.addStretch(1)

    def _go(self, sci):
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._close_large_view()
        self._mode = "tree"
        self._cur = sci
        self._render()

    # ── grid helpers ─────────────────────────────────────────────────────────
    def _card_width(self):
        sb = self._scroll.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        avail = self._scroll.width() - sb - 2 * self._scroll.frameWidth() - 8
        if avail < 200:
            avail = 1000
        gaps = self._grid.spacing() * (self.columns - 1)
        return max(150, (avail - gaps) // self.columns)

    def _clear_grid(self):
        self._loader.cancel_all()
        if self._collection is not None:
            self._collection.cancel_pending_loads()
        self._pic_cards = []
        self._pic_by_key = {}
        # Reset column stretches — otherwise a previous multi-column taxon layout
        # would squeeze a single full-width widget (the pretty panel) into one column.
        for c in range(8):
            self._grid.setColumnStretch(c, 0)
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # ── render dispatch ──────────────────────────────────────────────────────
    def _render(self):
        if self._source is None:
            return
        self._clear_grid()
        self._all_btn.setChecked(self._mode == "allspecies")
        # The rank rail makes sense only while browsing the tree; the sidebar only
        # in the pretty species view.
        self._rail_host.setVisible(self._mode != "species")
        self._sidebar_scroll.setVisible(self._mode == "species"
                                        and self._species_view == "pretty")
        self._rebuild_crumbs()
        self._rebuild_rail()
        if self._mode == "allspecies":
            self._render_all_species()
        elif self._search.text().strip():
            self._render_search()
        elif self._mode == "species":
            if self._species_view == "tech":
                self._render_species()
            else:
                self._render_species_pretty()
        else:
            self._render_children()

    # ── species: pretty panel (default) ─────────────────────────────────────
    def _render_species_pretty(self):
        node = self._source.node(self._cur)
        display = self._display(dict(node)) if node else self._cur
        n_pic = self._source.counts(self._cur)[1]
        self._status.setText(f"{display} — {n_pic} photos · {self._source.scope_label}")
        if self._collection is None:
            self._show_empty("Picture provider unavailable — see the log.")
            return
        from gui.species_panel import SpeciesPanel
        panel = SpeciesPanel(self)
        panel.show_species(self._cur)
        self._grid.setColumnStretch(0, 1)
        self._grid.addWidget(panel, 0, 0)
        # Dock the panel's sidebar outside the scroll (setWidget deletes the old one).
        sidebar = panel.sidebar_widget()
        if sidebar is not None:
            self._sidebar_scroll.setWidget(sidebar)
        # the large viewer + card sync work off these
        self._pic_cards = panel.pic_cards
        self._pic_by_key = {c.upn_key: c for c in panel.pic_cards}
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))

    # Panel → host hooks
    def _panel_open_tech(self):
        self._species_view = "tech"
        self._render()

    def _panel_open_pretty(self):
        self._species_view = "pretty"
        self._render()

    def _panel_toggle_all_files(self):
        self._show_all_files = not self._show_all_files
        self._render()

    def _panel_show_map(self, sci):
        from PyQt6.QtWidgets import QMessageBox
        if self._master_db is None:
            return
        points = self._master_db.gps_points_for_species(sci)
        if not points:
            QMessageBox.information(
                self, "Map",
                "No GPS points for this species yet — older material predates in-camera GPS.")
            return
        from gui.map_view import MapView
        node = self._source.node(sci)
        display = self._display(dict(node)) if node else sci
        dlg = MapView(self, self._master_db, sci, display, points)
        dlg.show()

    # ── flat: all species ────────────────────────────────────────────────────
    def _render_all_species(self):
        raw = self._search.text().strip()
        q = raw.lower()
        nodes = self._source.all_species()
        if q:
            nodes = [n for n in nodes
                     if any(q in (n[f] or "").lower() for f in ("sci", "nl", "en"))]
        total = len(nodes)
        capped = nodes[:self._ALL_CAP]
        if q:
            self._status.setText(
                f"{total} species match “{raw}” · {self._source.scope_label}")
        else:
            more = (f" (showing first {len(capped)} — type to search all)"
                    if total > len(capped) else "")
            self._status.setText(
                f"All species — {total}{more} · {self._source.scope_label}")
        if not capped:
            self._show_empty("No species match." if q else "No species.")
            return
        self._lay_taxon_cards(capped)

    # ── tree: children ───────────────────────────────────────────────────────
    def _render_children(self):
        nodes = self._source.children(self._cur)
        n_sp, n_pic = self._source.counts(self._cur)
        node = self._source.node(self._cur)
        title = node["sci"] if node else self._cur
        self._status.setText(
            f"{title} — {n_sp} species · {n_pic} photos · {self._source.scope_label}")
        if not nodes:
            self._show_empty("Nothing photographed here.")
            return
        self._lay_taxon_cards(nodes)

    # ── tree: rank search ────────────────────────────────────────────────────
    def _render_search(self):
        query = self._search.text().strip()
        rank = self._level_combo.currentData()
        nodes = self._source.search(query, rank)
        label = f"Search “{query}”"
        if rank and rank != "ANY":
            label += f" · {RANK_LABEL.get(rank, rank)}"
        self._status.setText(
            f"{len(nodes)} match(es) — {label} · {self._source.scope_label}")
        if not nodes:
            self._show_empty(f"Nothing at this rank matches “{query}”.")
            return
        self._lay_taxon_cards(nodes)

    def _lay_taxon_cards(self, nodes):
        card_w = self._card_width()
        for c in range(self.columns):
            self._grid.setColumnStretch(c, 1)
        for idx, node in enumerate(nodes):
            node = dict(node)
            node["_display"] = self._display(node)
            n_sp, n_pic = self._source.counts(node["sci"])
            if node["rank"] == "SPECIES":
                sub = f"<b style='color:{_FRAME}'>{n_pic}</b> photos"
            else:
                sub = (f"<b style='color:{_FRAME}'>{n_sp}</b> species"
                       f" &nbsp;·&nbsp; {n_pic} photos")
            cover = self._source.cover_path(node["sci"])
            card = TaxonCard(node, sub, card_w, cover, self._loader)
            card.clicked.connect(self._on_taxon_clicked)
            self._grid.addWidget(card, idx // self.columns, idx % self.columns)
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))

    def _show_empty(self, text):
        empty = QLabel(text)
        empty.setStyleSheet(f"color:{_INK_SOFT}; font-size:14px; padding:24px 6px;")
        self._grid.addWidget(empty, 0, 0)

    def _on_taxon_clicked(self, sci):
        if self._source.is_species(sci):
            self._open_species(sci)
        else:
            self._go(sci)

    # ── species: inline picture grid ─────────────────────────────────────────
    def _open_species(self, sci):
        self._close_large_view()
        self._mode = "species"
        self._species_view = "pretty"
        self._cur = sci
        self._render()

    def _render_species(self):
        node = self._source.node(self._cur)
        display = self._display(dict(node)) if node else self._cur
        parent = self._source.node(node["up"]) if node and node.get("up") else None

        # header row: back link (left) + a toggle back to the pretty panel (right)
        header = QWidget()
        hl = QHBoxLayout(header); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(8)
        back = QPushButton(f"←  {parent['sci']}" if parent else "←  back")
        back.setFlat(True)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet(
            f"QPushButton {{ color:{_READY}; font-weight:600; border:none;"
            f" padding:4px 2px; text-align:left; }}"
            f"QPushButton:hover {{ text-decoration:underline; }}")
        back.clicked.connect(lambda: self._go(parent["sci"]) if parent
                             else self._go(self._source.root()))
        hl.addWidget(back)
        hl.addStretch(1)
        pretty = QPushButton("✦ Pretty view")
        pretty.setCursor(Qt.CursorShape.PointingHandCursor)
        pretty.setToolTip("Back to the species page (hero, grouped photos, siblings)")
        pretty.setStyleSheet(
            f"QPushButton {{ color:{_DONE}; border:1px solid {_LINE}; background:{_DONE_BG};"
            f" border-radius:7px; padding:5px 11px; font-weight:600; }}"
            f"QPushButton:hover {{ border-color:{_DONE}; }}")
        pretty.clicked.connect(self._panel_open_pretty)
        hl.addWidget(pretty)
        self._grid.addWidget(header, 0, 0, 1, self.columns)

        if self._collection is None:
            self._show_empty_at(1, "Picture provider unavailable — see the log.")
            return

        self._collection.set_species_lang(self._lang)
        self._collection.set_jpg_only(True)
        self._collection.set_camera_filter(set())
        self._collection.set_sort("rate")
        try:
            self._collection.set_color_filter(set())
        except Exception:
            pass
        self._collection.rebuild(display, forced_sci=self._cur.casefold())

        n = len(self._collection)
        ro = " (read-only)" if self._master_read_only else ""
        self._status.setText(
            f"{n} master picture{'s' if n != 1 else ''} for {display}{ro}")
        if n == 0:
            self._show_empty_at(1, f"No pictures for {display}.")
            return

        from gui.rating_editor import PictureCard
        card_w = self._card_width()
        for c in range(self.columns):
            self._grid.setColumnStretch(c, 1)
        for idx in range(n):
            item = self._collection.item(idx)
            card = PictureCard(item, card_w, show_kind_tag=False)
            card.card_changed.connect(self._on_pic_changed)
            card.card_clicked.connect(self._on_pic_cursor)
            card.card_double_clicked.connect(self._on_pic_open)
            card.card_right_clicked.connect(self._on_pic_menu)
            r, col = 1 + idx // self.columns, idx % self.columns
            self._grid.addWidget(card, r, col)
            self._pic_cards.append(card)
            self._pic_by_key[item.key] = card
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(0))

    def _show_empty_at(self, row, text):
        empty = QLabel(text)
        empty.setStyleSheet(f"color:{_INK_SOFT}; font-size:14px; padding:20px 6px;")
        self._grid.addWidget(empty, row, 0, 1, self.columns)

    # `_after_master_change` (MasterCardMenuMixin) calls this after a mutation.
    def _rebuild_cards(self):
        if self._mode == "species":
            self._render()

    # ── picture card interactions (mirror Master Species Viewer) ─────────────
    def _on_pic_cursor(self, upn_key):
        pass

    def _on_pic_changed(self, upn_key):
        self._status.setText(f"Saved {upn_key}")

    def _on_pic_menu(self, upn_key):
        self._master_context_menu(upn_key)

    def _on_pic_open(self, upn_key):
        from gui.large_image_view import LargeImageView
        if self._collection is None:
            return
        self._close_large_view()
        keys = [c.upn_key for c in self._pic_cards]
        if upn_key not in keys:
            return
        dlg = LargeImageView(self, self._collection, keys, keys.index(upn_key),
                             settings_key="BiotaBrowser", auto_advance=True)
        dlg.picture_changed.connect(lambda _k: self._sync_pic_cards())
        dlg.picture_navigated.connect(self._on_large_view_navigated)
        self._large_view = dlg
        dlg.finished.connect(self._on_large_view_finished)
        dlg.show()

    def _sync_pic_cards(self):
        for card in self._pic_cards:
            try:
                card.refresh()
            except RuntimeError:
                pass

    def _on_large_view_navigated(self, key):
        card = self._pic_by_key.get(key)
        if card:
            self._scroll.ensureWidgetVisible(card)

    def _on_large_view_finished(self, _=0):
        self._large_view = None
        self._sync_pic_cards()

    def _close_large_view(self):
        lv = getattr(self, "_large_view", None)
        if lv is None:
            return
        self._large_view = None
        try:
            lv.finished.disconnect(self._on_large_view_finished)
        except (TypeError, RuntimeError):
            pass
        try:
            lv.close()
        except RuntimeError:
            pass
