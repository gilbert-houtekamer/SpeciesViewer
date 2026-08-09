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
    QStyle, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

from gui.window_state import restore_window, save_window
from gui.image_loader import AsyncImageLoader
from gui.master_ops import MasterCardMenuMixin
from gui.biota_source import RANK_LABEL, RANK_ORDER
from utils.config import _config
from gui import theme

logger = logging.getLogger("speciesviewer")

# ── palette (Trip-lifecycle board, light) + the website's olive frame ────────
# Sourced from gui/theme.py (ch.15 global QSS, Phase 2) — was a local
# near-duplicate of Species Panel's/Trip lifecycle board's own copies.
_INK = theme.INK; _INK_SOFT = theme.INK_SOFT; _LINE = theme.LINE
_SURFACE = theme.SURFACE; _SURFACE2 = theme.SURFACE_2; _BG = theme.BG
_FRAME = theme.ACCENT; _FRAME_INK = theme.ACCENT_INK; _MAT = theme.SURFACE
_DONE = theme.DONE_FG; _DONE_BG = theme.DONE_BG; _READY = theme.READY_FG; _READY_BG = theme.READY_BG


def _load_qimage(path):
    """Worker-thread producer: decode a cover JPG (or nothing)."""
    if path is None:
        return None
    img = QImage(str(path))
    return None if img.isNull() else img


def _load_cover_spec(spec):
    """Worker-thread producer for a cover spec ('jpg'|'raw', Path). A JPG is
    decoded directly; a RAW's embedded preview is extracted once (cached) via the
    shared exiftool — so a taxon whose pictures are all RAW (common on an
    unprocessed trip) still shows a real image instead of a blank tile."""
    if not spec:
        return None
    kind, path = spec
    if kind == "raw":
        try:
            from gui.preview_provider import preview_cache_dir_for
            from gui.preview_extract import extract_preview_to
            cache = preview_cache_dir_for(path.parent) / (path.stem + ".jpg")
            if not cache.exists():
                cache.parent.mkdir(parents=True, exist_ok=True)
                if not extract_preview_to(path, cache):
                    return None
            img = QImage(str(cache))
        except Exception:
            return None
    else:
        img = QImage(str(path))
    return None if img.isNull() else img


def _fit_font_px(text, width, base=14, floor=11, max_lines=2):
    """Pick a pixel font size so `text` word-wraps into at most `max_lines` at
    width `width`, shrinking from `base` down to `floor`; if even `floor` needs
    more, it is used anyway (up to 3 lines). Returns (px, reserved_height) so the
    caller can set a minimum height and never clip. This keeps a very long taxon
    name (e.g. a Dutch order name) inside roughly two normal lines' worth of space
    instead of overflowing the card."""
    from PyQt6.QtGui import QFont, QFontMetrics
    text = text or ""
    wrap = int(Qt.TextFlag.TextWordWrap)
    px = base
    while True:
        fm = QFontMetrics(_px_font(px))
        r = fm.boundingRect(0, 0, max(10, width), 100000, wrap, text)
        n = max(1, round(r.height() / max(1, fm.lineSpacing())))
        if n <= max_lines or px <= floor:
            return px, r.height()
        px -= 1


def _px_font(px):
    from PyQt6.QtGui import QFont
    f = QFont()
    f.setPixelSize(px)
    return f


class TaxonCard(QFrame):
    """One olive-framed card — a child taxon or a species leaf. Emits its
    scientific name on a left click; right-click copies its names as plain text
    (the card is the same widget in the tree and in the flat all-species index,
    so both get it)."""
    clicked = pyqtSignal(str)

    def __init__(self, node, sub_html, card_w, cover_spec, loader):
        super().__init__()
        self.setObjectName("tcard")
        self._sci = node["sci"]
        # Only the names — the card holds no reference to the node otherwise.
        self._names = {k: node.get(k) for k in ("nl", "en", "sci")}
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
        self._photo.setStyleSheet(f"background:{_SURFACE2}; border:3px solid {_MAT};"
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
        # SPECIES nodes follow the display profile (e.g. NL then EN); higher taxa
        # keep the compact "name + scientific" pairing — a genus/family vernacular
        # can be long and the Latin name is the stable label there. Never repeat
        # the title. Long names are shrunk to fit (see _fit_font_px) so the card
        # never clips.
        disp = node["_display"]
        if node.get("rank", "") == "SPECIES":
            render_lines = list(node.get("_display_lines") or [(disp, False)])
        else:
            disp_cf = (disp or "").strip().casefold()
            if disp_cf and disp_cf == (node.get("sci") or "").strip().casefold():
                sec = node.get("en") or node.get("nl") or ""
                render_lines = [(disp, True)] + ([(sec, False)] if sec else [])
            else:
                render_lines = [(disp, False)] + ([(node["sci"], True)]
                                                  if node.get("sci") else [])
        inner_w = card_w - 24
        for i, (text, is_sci) in enumerate(render_lines):
            base = 14 if i == 0 else 12
            px, h = _fit_font_px(text, inner_w, base=base,
                                 floor=(11 if i == 0 else 10),
                                 max_lines=2)
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setMinimumHeight(h)
            css = (f"color:{_INK}; font-weight:600;" if i == 0
                   else f"color:{_INK_SOFT};")
            css += f" font-size:{px}px; border:none;"
            if is_sci:
                css += " font-style:italic;"
            lbl.setStyleSheet(css)
            ml.addWidget(lbl)
        stat = QLabel(sub_html)
        stat.setTextFormat(Qt.TextFormat.RichText)
        stat.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace;"
                           f" font-size:10.5px; border:none; margin-top:5px;")
        ml.addWidget(stat)
        lay.addWidget(meta)

        if cover_spec is not None:
            loader.request(lambda s=cover_spec: _load_cover_spec(s),
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

    def contextMenuEvent(self, event):
        """Right-click = copy this taxon's names. Left-click still navigates —
        the card owns its clicks, so the names cannot be a selectable label here."""
        from gui.name_copy import add_name_copy_actions
        menu = QMenu(self)
        if not add_name_copy_actions(menu, self._names):
            return
        menu.exec(event.globalPos())


class BiotaBrowserDialog(MasterCardMenuMixin, QDialog):
    """Scope-agnostic biota tree browser. Master scope only for now."""

    # How many species cards to paint at once in the flat 'all species' view
    # before asking the user to narrow with the search box (the master has ~2200).
    _ALL_CAP = 400
    # Don't run the (whole-tree) name search until this many characters — a 1-2
    # char query matches almost everything and scans the whole master needlessly.
    _SEARCH_MIN = 3

    def __init__(self, parent, start_mode="tree", *, repo_root=None,
                 scope_title=None, read_only_scope=False, cache_name=None,
                 album_from=None, album_to=None, start_sci=None):
        super().__init__(parent)
        self._start_mode = start_mode   # "tree" | "allspecies"
        # Open straight to this species' pretty page (the Master Species Viewer's
        # "✦ Species page" link jumps here). Honoured once the source has loaded.
        self._start_sci = (start_sci or "").strip() or None
        # An album is a date window on the master (album_from/to set): it reads the
        # real master, restricted to the range, rather than a repo of its own.
        self._album_from = album_from
        self._album_to = album_to
        # Scope: default is the master (repo_root None → master config). A trip is
        # browsed by passing its repo root + its own cache_name (so it never
        # clobbers master.db) + read_only_scope=True (editing stays in the
        # authorized Tech panels elsewhere).
        self._repo_root = repo_root
        self._scope_title = scope_title or "Master collection"
        self._read_only_scope = read_only_scope
        self._cache_name = cache_name
        self.setWindowTitle(f"Biota Browser — {self._scope_title}")
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
        self._history = []               # navigation back-stack (view snapshots)
        # Flat all-species view: the filtered list, how many cards are laid
        # out, and the ceiling that survives a re-render (column change).
        self._all_nodes = []
        self._all_shown = 0
        self._all_limit = self._ALL_CAP
        self._more_btn = None
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
        self._back_btn = QPushButton("←")
        self._back_btn.setFixedWidth(34)
        self._back_btn.setToolTip("Back to the previous view")
        self._back_btn.setEnabled(False)
        self._back_btn.setStyleSheet(
            f"QPushButton {{ border:1px solid {_LINE}; border-radius:6px; padding:4px 0;"
            f" font-size:15px; color:{_INK}; }}"
            f"QPushButton:hover:enabled {{ border-color:{_READY}; color:{_READY}; }}"
            f"QPushButton:disabled {{ color:{_LINE}; }}")
        self._back_btn.clicked.connect(self._go_back)
        top.addWidget(self._back_btn)
        top.addSpacing(8)
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

    def keyPressEvent(self, event):
        """Ctrl+Home / Ctrl+End jump to the top / bottom of the card grid — the
        all-species index runs to a couple of thousand cards, and the tree views
        and search results share the same scroll area."""
        from gui.viewer_keys import handle_scroll_ends
        if handle_scroll_ends(event, self._scroll):
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._close_large_view()
        save_window(self, self._geo_key, cols=self.columns)
        super().closeEvent(event)

    # ── init ────────────────────────────────────────────────────────────────
    def _init_master(self):
        is_album = bool(self._album_from and self._album_to)
        # An album reads the REAL master (default cache), windowed by dates; a trip
        # reads its own repo (its own cache); otherwise the configured master.
        root = ((_config.get("master", "masterbiotadatabase", fallback="") if is_album
                 else (self._repo_root or _config.get("master", "masterbiotadatabase", fallback="")))
                ).strip()
        if not root:
            self._status.setText("No database is configured — set one in Repository Settings.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            from db.master_db import MasterDB
            from gui.biota_source import MasterBiotaSource, AlbumBiotaSource
            db = MasterDB(root, cache_name=(None if is_album else self._cache_name))
            self._status.setText("Opening database…")
            QApplication.processEvents()
            db.ensure_fresh(progress_cb=lambda msg: self._status.setText(msg))
            if is_album:
                self._source = AlbumBiotaSource(db, root, self._album_from,
                                                self._album_to, label=self._scope_title)
            else:
                self._source = MasterBiotaSource(db, root)
                self._source.scope_label = self._scope_title
        except Exception as e:
            logger.error(f"Biota Browser: load failed: {e}", exc_info=True)
            self._status.setText("Database load failed — see the log.")
            QApplication.restoreOverrideCursor()
            return

        self._master_db = db
        # A trip scope is always read-only here (editing goes through the
        # authorized Tech panels); the master follows its machine policy.
        self._master_read_only = self._read_only_scope or _config.get(
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
                db, root, include=True,
                read_only=self._master_read_only, full=True)
        except Exception as e:
            logger.error(f"Biota Browser: provider setup failed: {e}", exc_info=True)
            self._collection = None
        finally:
            QApplication.restoreOverrideCursor()

        self._scope_label.setText(self._source.scope_label)
        self._cur = self._source.root()
        if self._start_sci and self._source.node(self._start_sci):
            # Land on the requested species' pretty page (external "✦ Species page"
            # jump). Falls through to the normal root view if the master doesn't
            # carry it.
            self._cur = self._start_sci
            self._mode = "species"
            self._species_view = "pretty"
        elif self._start_mode == "allspecies":
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
        # Only act once the query is long enough to be worth a tree scan.
        if self._mode == "species" and len(self._search.text().strip()) >= self._SEARCH_MIN:
            self._mode = "tree"
        self._all_limit = self._ALL_CAP   # a new query starts from the top
        self._render()

    def _on_all_species_toggled(self, checked):
        self._push_history()
        self._close_large_view()
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._all_limit = self._ALL_CAP
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

    def _display_lines_for(self, node) -> list:
        """The node's names as (text, is_sci) lines for the current profile —
        deduped, fallback-filled. The TaxonCard renders these for SPECIES nodes;
        higher taxa keep the tidy 'name + scientific' pairing instead (a genus/
        family's vernacular can be very long, and the Latin name is the useful
        stable label there)."""
        from utils.name_profile import name_lines
        return name_lines(node, self._lang)

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

    # ── navigation history (back-stack) ─────────────────────────────────────
    def _snapshot(self):
        return {"mode": self._mode, "cur": self._cur, "view": self._species_view,
                "search": self._search.text(), "level": self._level_combo.currentIndex(),
                "all_files": self._show_all_files}

    def _push_history(self):
        if self._source is None:
            return
        snap = self._snapshot()
        if self._history and self._history[-1] == snap:
            return
        self._history.append(snap)
        if len(self._history) > 50:
            self._history.pop(0)
        self._back_btn.setEnabled(True)

    def _go_back(self):
        if not self._history:
            return
        self._close_large_view()
        snap = self._history.pop()
        self._mode = snap["mode"]
        self._cur = snap["cur"]
        self._species_view = snap["view"]
        self._show_all_files = snap["all_files"]
        self._search.blockSignals(True)
        self._search.setText(snap["search"])
        self._search.blockSignals(False)
        self._level_combo.blockSignals(True)
        self._level_combo.setCurrentIndex(snap["level"])
        self._level_combo.blockSignals(False)
        self._back_btn.setEnabled(bool(self._history))
        self._render()

    def _go(self, sci):
        self._push_history()
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
        self._more_btn = None        # about to be deleted with the rest
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
        elif len(self._search.text().strip()) >= self._SEARCH_MIN:
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
        """Open the more-capable standalone Species Viewer for this species, at
        this browser's scope (Master / Trip / Album). Replaces the old in-window
        edit grid: the standalone viewer adds species search, scope/camera
        filters, colour-delete and the ✦ Species… round-trip — and, for a Trip,
        it can actually edit (the in-window grid was read-only over a trip repo)."""
        sci = self._cur
        mw = self.parent()
        if not sci or mw is None:
            return
        if self._album_from and self._album_to:
            fn = getattr(mw, "open_virttrip_rating_editor", None)
        elif self._repo_root:
            fn = getattr(mw, "open_rating_editor", None)
        else:
            fn = getattr(mw, "open_master_species_viewer", None)
        if fn is not None:
            fn(initial_sci=sci)

    def _panel_open_pretty(self):
        self._species_view = "pretty"
        self._render()

    def _panel_toggle_all_files(self):
        self._show_all_files = not self._show_all_files
        self._render()

    def _real_master(self):
        """The REAL master DB (default cache), opened lazily and cached — used to
        draw a species' whole-master range behind a TRIP's own points on the map.
        A trip browses its OWN repo cache (self._master_db), which knows only the
        trip's pictures; the master range comes from here. Returns None when no
        master is configured or it fails to open."""
        if getattr(self, "_real_master_db", None) is not None:
            return self._real_master_db
        root = _config.get("master", "masterbiotadatabase", fallback="").strip()
        if not root:
            return None
        try:
            from db.master_db import MasterDB
            db = MasterDB(root)           # default cache = the real master.db
            db.ensure_fresh()             # cheap no-op when the master is fresh
            self._real_master_db = db
            return db
        except Exception as e:
            logger.error(f"Biota Browser: real-master open failed: {e}", exc_info=True)
            return None

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
        # Scope-aware highlight (backlog ch.14): keep the species' WHOLE master
        # range on the map, but draw THIS scope's own pictures in a standout colour.
        #  • Album — self._master_db IS the real master, `points` already spans it;
        #    scope_upns gives the album's own (date-range) UPNs.
        #  • Trip — self._master_db is only the trip's cache, so `points` is just the
        #    trip's own pictures. Fetch the whole-master range from the real master,
        #    union the trip's points in (an un-committed trip isn't in the master),
        #    and highlight the trip's own UPNs. The MapView runs on the real master
        #    so Add-species / bubble→pictures work against the full collection.
        #  • Master — no highlight; the plain single-colour map.
        db_for_map = self._master_db
        trip_db = None
        highlight = highlight_label = None
        if self._source.date_range() is not None:              # album
            highlight = self._source.scope_upns(sci) or set()
            highlight_label = self._source.scope_label or "This album"
        elif self._repo_root:                                  # trip
            real = self._real_master()
            if real is not None:
                trip_upns = {(p.get("upn") or "") for p in points}
                merged = {(p.get("upn") or ""): p
                          for p in real.gps_points_for_species(sci)}
                for p in points:                               # add un-committed trip pts
                    merged.setdefault(p.get("upn") or "", p)
                points = list(merged.values())
                highlight = trip_upns
                highlight_label = self._source.scope_label or "This trip"
                db_for_map = real
                # The trip's own points may be un-committed (not in the master), so
                # hand the map the trip repo to resolve their pictures on click.
                trip_db = self._master_db
        dlg = MapView(self, db_for_map, sci, display, points,
                      highlight_upns=highlight, highlight_label=highlight_label or "",
                      trip_db=trip_db)
        dlg.show()

    # ── flat: all species ────────────────────────────────────────────────────
    def _render_all_species(self):
        raw = self._search.text().strip()
        q = raw.lower()
        nodes = self._source.all_species()
        if q:
            nodes = [n for n in nodes
                     if any(q in (n[f] or "").lower() for f in ("sci", "nl", "en"))]
        # Held for "show more", which appends the next batch to the cards already
        # on screen rather than laying the whole list out again.
        self._all_nodes = nodes
        self._all_shown = min(self._all_limit, len(nodes))
        if not self._all_shown:
            self._status.setText(
                f"0 species match “{raw}” · {self._source.scope_label}" if q
                else f"All species — 0 · {self._source.scope_label}")
            self._show_empty("No species match." if q else "No species.")
            return
        self._lay_taxon_cards(nodes[:self._all_shown])
        self._update_all_footer()

    def _update_all_footer(self):
        """Status line + the "show more" row under the last card."""
        total = len(self._all_nodes)
        shown = self._all_shown
        raw = self._search.text().strip()
        scope = self._source.scope_label
        if raw:
            self._status.setText(f"{total} species match “{raw}” · {scope}")
        else:
            self._status.setText(f"All species — showing {shown} of {total} · {scope}"
                                 if shown < total else
                                 f"All species — {total} · {scope}")
        if self._more_btn is not None:
            self._more_btn.setParent(None)
            self._more_btn.deleteLater()
            self._more_btn = None
        if shown >= total:
            return
        rest = total - shown
        step = min(self._ALL_CAP, rest)
        btn = QPushButton(f"Show {step} more  ({rest} still to come)")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ border:1px solid {_LINE}; border-radius:8px;"
            f" padding:9px 18px; color:{_READY}; background:{_READY_BG};"
            f" font-weight:600; }}"
            f"QPushButton:hover {{ border-color:{_READY}; }}")
        btn.clicked.connect(self._show_more_species)
        self._more_btn = btn
        row = (shown + self.columns - 1) // self.columns
        self._grid.addWidget(btn, row, 0, 1, max(1, self.columns))

    def _show_more_species(self):
        """Append the next batch. Deliberately NOT a re-render: only the new
        cards are built, so revealing the whole list costs the same in total as
        showing it at once — where re-rendering each time would cost
        400+800+1200+… card builds."""
        start = self._all_shown
        end = min(start + self._ALL_CAP, len(self._all_nodes))
        if end <= start:
            return
        if self._more_btn is not None:
            self._more_btn.setParent(None)
            self._more_btn.deleteLater()
            self._more_btn = None
        self._lay_taxon_cards(self._all_nodes[start:end], offset=start)
        self._all_shown = end
        self._all_limit = end        # survives a re-render (column change, etc.)
        self._update_all_footer()

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

    def _lay_taxon_cards(self, nodes, offset=0):
        """Lay `nodes` into the grid starting at card position `offset`.

        A non-zero offset APPENDS to what is already there (and leaves the scroll
        position alone). That is what keeps "show more" linear: each batch builds
        only its own cards, so revealing the whole master costs the same as
        showing it in one go. Re-rendering from zero on every click would cost
        400+800+1200+… — quadratic in the number of batches."""
        card_w = self._card_width()
        for c in range(self.columns):
            self._grid.setColumnStretch(c, 1)
        for i, node in enumerate(nodes):
            idx = offset + i
            node = dict(node)
            node["_display"] = self._display(node)
            node["_display_lines"] = self._display_lines_for(node)
            n_sp, n_pic = self._source.counts(node["sci"])
            if node["rank"] == "SPECIES":
                sub = f"<b style='color:{_FRAME}'>{n_pic}</b> photos"
            else:
                sub = (f"<b style='color:{_FRAME}'>{n_sp}</b> species"
                       f" &nbsp;·&nbsp; {n_pic} photos")
            cover = self._source.cover_spec(node["sci"])
            card = TaxonCard(node, sub, card_w, cover, self._loader)
            card.clicked.connect(self._on_taxon_clicked)
            # Top-align each cell: a card keeps its natural height and sits at the
            # top of its cell, so a taller neighbour in the row never stretches its
            # photo frame, and a short row never balloons to fill the window.
            self._grid.addWidget(card, idx // self.columns, idx % self.columns,
                                 Qt.AlignmentFlag.AlignTop)
        if not offset:
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
    def show_species_page(self, sci: str) -> bool:
        """Navigate to this species' pretty page (used when a standalone viewer's
        "✦ Species…" reuses an already-open browser). Returns False if the source
        isn't loaded yet or doesn't carry the species."""
        if self._source is None or not sci or self._source.node(sci) is None:
            return False
        self._species_view = "pretty"
        self._open_species(sci)
        return True

    def _open_species(self, sci):
        self._push_history()
        # Leave any active search behind — otherwise _render's search branch wins
        # over the species view and the pretty page never shows (the click would
        # appear to do nothing but hide the rank rail).
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
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
        pretty = QPushButton("✦ Species page")
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

        # An album borrows the full-master collection but shows only its own
        # (date-range) pictures — filter to that UPN set (None = show all).
        scope = self._source.scope_upns(self._cur)
        items = [self._collection.item(i) for i in range(len(self._collection))]
        if scope is not None:
            items = [it for it in items if it.key in scope]

        n = len(items)
        ro = " (read-only)" if self._master_read_only else ""
        self._status.setText(
            f"{n} picture{'s' if n != 1 else ''} for {display}{ro}")
        if n == 0:
            self._show_empty_at(1, f"No pictures for {display}.")
            return

        from gui.rating_editor import PictureCard
        card_w = self._card_width()
        for c in range(self.columns):
            self._grid.setColumnStretch(c, 1)
        for idx, item in enumerate(items):
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
