"""SpeciesPanel — the pretty, web-style species view for the Biota Browser.

The app twin of a website species page (docs/design-docs/species_panel.html):
a hero showing the best photo at its natural aspect (olive-matted, never hard
cropped), a names/classification table, the pictures grouped by age/sex/plumage,
and a sibling-species sidebar. It is the species click-through's default face;
the editable PictureCard grid is one ⚙ Tech panel button away (the Biota Browser
flips its species view — the grid already existed, so Tech is not new code).

Coupling: the panel is built for a single host (BiotaBrowserDialog) and reads a
few of its members (the shared master provider `_collection`, the `_source`, the
`_loader`, the name resolver `_display`) and calls back into it for navigation and
the master card menu. Kept a plain QWidget (not a dialog) so it embeds inline.
"""

import logging
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from gui import theme
from gui.name_copy import make_copyable as _make_copyable

logger = logging.getLogger("speciesviewer")

# palette — mirrors gui/biota_browser, now sourced from gui/theme.py
# (ch.15 global QSS, Phase 2 — no import cycle: theme.py has zero imports
# of its own). _SURFACE2 has no shared counterpart yet, stays local.
_INK = theme.INK; _INK_SOFT = theme.INK_SOFT; _LINE = theme.LINE
_SURFACE = theme.SURFACE; _SURFACE2 = theme.SURFACE_2
_FRAME = theme.ACCENT; _FRAME_INK = theme.ACCENT_INK; _MAT = theme.SURFACE
_DONE = theme.DONE_FG; _DONE_BG = theme.DONE_BG; _READY = theme.READY_FG; _READY_BG = theme.READY_BG

# readable group labels from the master row's derived qualifier fields
_MF = {"male": "Male", "female": "Female", "pair": "Pair", "couple": "Pair"}
_AGE = {"juvenile": "Juvenile", "immature": "Immature", "adult": "Adult",
        "juv": "Juvenile", "imm": "Immature"}
_SW = {"breeding": "Breeding", "summer": "Summer", "winter": "Winter",
       "non-breeding": "Non-breeding"}


def _group_label(mf, age, sw) -> str:
    parts = []
    for v, m in ((mf, _MF), (age, _AGE), (sw, _SW)):
        v = (v or "").strip().lower()
        if v:
            parts.append(m.get(v, v.capitalize()))
    return " · ".join(parts) if parts else "Unspecified"


_SETTINGS_SHUTTER = re.compile(r"^\s*(\S+)\s+sec\b")
_SETTINGS_APERTURE = re.compile(r"\bF([\d.]+)")
_SETTINGS_ISO = re.compile(r"\bISO\s*(\d+)")
_SETTINGS_DISTANCE = re.compile(r"\bdistance\s*([\d.]+)")


def _parse_settings(text):
    """('1/2000', '4.0', '250', '37.4') out of the master's one Settings string.

    Shape: '1/2000 sec at F4.0, ISO 250, distance 37.4 m'. When the camera wrote
    nothing the string is still there as an empty template — ' sec at F, ISO ,
    distance ' — so every field is matched on its own and may come back None
    rather than trusting the commas.
    """
    t = (text or "").strip()
    if not t:
        return (None, None, None, None)
    def grab(rx):
        m = rx.search(t)
        return m.group(1) if m else None
    return (grab(_SETTINGS_SHUTTER), grab(_SETTINGS_APERTURE),
            grab(_SETTINGS_ISO), grab(_SETTINGS_DISTANCE))


def _row_copy_menu(widget, names, pos):
    """Right-click menu for a widget that carries names but owns its clicks."""
    from PyQt6.QtWidgets import QMenu
    from gui.name_copy import add_name_copy_actions
    menu = QMenu(widget)
    if add_name_copy_actions(menu, names):
        menu.exec(widget.mapToGlobal(pos))


def _load_qimage(path):
    from PyQt6.QtGui import QImage
    if path is None:
        return None
    img = QImage(str(path))
    return None if img.isNull() else img


class _Photo(QLabel):
    """A photo label that positions small corner badges over itself."""
    def __init__(self):
        super().__init__()
        self._badges = []   # (widget, corner)

    def add_badge(self, w, corner):
        w.setParent(self)
        self._badges.append((w, corner))
        w.show()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        m = 6
        for w, corner in self._badges:
            w.adjustSize()
            x = m if "l" in corner else self.width() - w.width() - m
            y = m if "t" in corner else self.height() - w.height() - m
            w.move(x, y)


class PhotoTile(QFrame):
    """View-only olive-matted photo tile for the pretty panel — the site's look.
    Double-click opens the large viewer; right-click opens the master card menu.
    All editing lives in the Tech panel; this tile only shows state (# = has a
    JPG, colour label dot, star rating)."""

    def __init__(self, item, collection, loader, tile_w, on_open, on_menu):
        super().__init__()
        self._item = item
        self.upn_key = item.key
        self._on_open = on_open
        self._on_menu = on_menu
        self.setObjectName("ptile")
        self.setStyleSheet(
            f"QFrame#ptile {{ background:{_FRAME}; border-radius:7px; }}"
            f"QFrame#ptile:hover {{ background:#5f7a56; }}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(tile_w)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(4)

        ph_h = max(90, int((tile_w - 10) * 0.66))
        self._photo = _Photo()
        self._photo.setFixedHeight(ph_h)
        self._photo.setStyleSheet(f"background:{_SURFACE2}; border:3px solid {_MAT};"
                                  f" border-radius:3px;")
        self._photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._photo)

        # # badge (has a JPG) — the marker the user asked for
        self._hash = QLabel("#")
        self._hash.setStyleSheet(
            "color:#fff; background:rgba(60,64,58,.78); font-family:Consolas,monospace;"
            " font-size:8px; font-weight:700; padding:0px 3px; border-radius:3px;")
        self._photo.add_badge(self._hash, "tl")
        self._hash.setVisible(item.has_jpg)

        self._dot = QLabel()
        self._dot.setFixedSize(11, 11)
        self._photo.add_badge(self._dot, "tr")

        self._stars = QLabel()
        self._stars.setStyleSheet("color:#fff; font-size:9px;"
                                  " background:rgba(0,0,0,.30); border-radius:3px; padding:0 3px;")
        self._photo.add_badge(self._stars, "br")

        jpg = collection.jpg_path(item.key)
        cap = QHBoxLayout(); cap.setContentsMargins(2, 0, 2, 1); cap.setSpacing(6)
        if jpg is not None:
            fname = jpg.name
        else:
            rp = None
            try:
                rp = collection.raw_path(item.key)
            except Exception:
                rp = None
            if rp is not None:
                fname = rp.name
            else:
                exts = getattr(item, "file_exts", []) or []
                fname = (exts[0] + " · " if exts else "") + item.key[:14]
        self._fn = QLabel(fname)
        self._fn.setStyleSheet(f"color:{_FRAME_INK}; font-family:Consolas,monospace; font-size:9px;")
        self._fn.setWordWrap(False)
        cap.addWidget(self._fn, 1)
        self._date = QLabel((item.date or "")[:10])
        self._date.setStyleSheet("color:rgba(255,255,255,.75); font-family:Consolas,monospace; font-size:9px;")
        cap.addWidget(self._date, 0)
        lay.addLayout(cap)

        self._paint_state()

        # Image via the provider — a JPG render when there is one, otherwise the
        # RAW's embedded preview (extracted off the GUI thread, crop applied). This
        # is the same path PictureCard uses, so RAW-only frames show a real picture
        # rather than a placeholder.
        self._photo.setText("…")
        item.request_thumbnail(tile_w * 2, self._set_photo)

    def _paint_state(self):
        from gui.rating_editor import COLOR_HEX
        col = COLOR_HEX.get(self._item.color, "")
        if col:
            self._dot.setStyleSheet(f"background:{col}; border:1.5px solid rgba(255,255,255,.85);"
                                    f" border-radius:6px;")
            self._dot.setVisible(True)
        else:
            self._dot.setVisible(False)
        r = max(0, min(5, self._item.rating))
        self._stars.setText("★" * r if r else "")
        self._stars.setVisible(r > 0)

    def _set_photo(self, px: QPixmap):
        if px is None or px.isNull():
            self._photo.setText("no preview")
            return
        w = self._photo.width() or self.width() - 10
        h = self._photo.height()
        # Contain (not cover): show the WHOLE photo, matted — a centre-crop hides
        # too much of a bird in a wide or tall frame.
        self._photo.setPixmap(px.scaled(
            w, h, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self._photo.setText("")

    def refresh(self):
        # colour / stars may have changed in the large view or Tech panel
        self._paint_state()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._on_open(self.upn_key)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self._on_menu(self.upn_key)
        else:
            super().mousePressEvent(e)


class SpeciesPanel(QWidget):
    """Built once, refreshed per species via show_species(sci)."""

    _MAX_SIBS = 24   # cap the sidebar's async cover loads

    def __init__(self, host):
        super().__init__()
        self._host = host          # BiotaBrowserDialog
        self._sci = None
        self._hero_label = None
        self._pic_cards = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

    # ── public ───────────────────────────────────────────────────────────────
    @property
    def pic_cards(self):
        return self._pic_cards

    def show_species(self, sci: str):
        self._sci = sci
        self._rebuild()

    # ── build ────────────────────────────────────────────────────────────────
    def _clear(self):
        self._pic_cards = []
        while self._outer.count():
            item = self._outer.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _rebuild(self):
        self._clear()
        host = self._host
        src = host._source
        node = src.node(self._sci)
        if node is None:
            return
        display = host._display(dict(node))
        pics = src.pictures_for_species(self._sci)   # raw rows (qualifier fields)

        self._outer.addWidget(self._build_hero(node, display, pics))
        self._outer.addWidget(self._build_actions())

        # Main content only — the sidebar is hosted OUTSIDE the scroll by the
        # Biota Browser (so narrowing the window never slides it off-screen).
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(16, 8, 16, 16); ml.setSpacing(6)
        ml.addWidget(self._build_names(src))
        ml.addWidget(self._build_photos(pics))
        ml.addStretch(1)
        self._outer.addWidget(main, 1)

        self._sidebar = self._build_sidebar(src, node)

    def sidebar_widget(self):
        """The siblings / orders sidebar — the Biota Browser docks this to the
        right of the scroll (built during show_species)."""
        return getattr(self, "_sidebar", None)

    # hero: best photo (natural aspect, olive mat) + identity
    def _build_hero(self, node, display, pics):
        host = self._host
        wrap = QWidget()
        wrap.setStyleSheet(f"background:{_SURFACE};")
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(22)

        frame = QFrame()
        frame.setStyleSheet(f"background:{_FRAME}; border-radius:9px;")
        frame.setFixedHeight(300)
        fl = QVBoxLayout(frame); fl.setContentsMargins(9, 9, 9, 9)
        self._hero_label = QLabel("loading…")
        self._hero_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hero_label.setStyleSheet(
            f"background:{_MAT}; border:4px solid {_MAT}; border-radius:4px; color:{_INK_SOFT};")
        self._hero_label.setMinimumWidth(220)
        fl.addWidget(self._hero_label)
        lay.addWidget(frame, 0)

        ident = QWidget(); il = QVBoxLayout(ident)
        il.setContentsMargins(0, 6, 0, 6); il.setSpacing(2)
        il.addStretch(1)
        # Title + subtitle carry all three names between them; both are copyable
        # as plain text (right-click), for picture captions in a printed book.
        from utils.name_profile import all_names_ordered
        from gui.name_copy import all_three_entries
        lines = all_names_ordered(node, host._lang)
        extras = all_three_entries([("", t) for t, _ in lines])

        nm = QLabel(display)
        nm.setStyleSheet(f"color:{_INK}; font-size:28px; font-weight:700;")
        nm.setWordWrap(True)
        _make_copyable(nm, display, extras)
        il.addWidget(nm)
        # Subtitle = the OTHER two names (all three shown across title + subtitle),
        # ordered by the display profile. This detail page is the sibling of the
        # large image viewer, which shows all three too; the profile only sets
        # which name leads. The full triple also lives in the table below.
        sub = " · ".join(t for t, _ in lines[1:])
        if sub:
            sci = QLabel(sub)
            sci.setStyleSheet(f"color:{_INK_SOFT}; font-style:italic; font-size:15px;")
            sci.setWordWrap(True)
            _make_copyable(sci, sub,
                           [(f'Copy "{t}"', t) for t, _ in lines[1:]] + extras)
            il.addWidget(sci)

        # Star rating = the best XMP star among this species' pictures (0–5).
        # NOT node['rate'] — that is currentHighestRating, a computed 0–100-ish
        # score, which would peg the hero at 5 stars for everything.
        def _si(v):
            try:
                return int(str(v).strip() or 0)
            except (TypeError, ValueError):
                return 0
        rate = min(5, max((_si(p.get("Rating")) for p in pics), default=0))
        if rate:
            stars = QLabel("★" * rate + "☆" * (5 - rate))
            stars.setStyleSheet(f"color:{_FRAME}; font-size:15px; letter-spacing:1px; margin-top:6px;")
            il.addWidget(stars)

        n_pics = len(pics)
        n_gps = sum(1 for p in pics if (p.get("GPSPosition") or "").strip())
        countries = [c for c in {(p.get("Country") or "").strip() for p in pics} if c]
        chips = QHBoxLayout(); chips.setSpacing(8); chips.setContentsMargins(0, 12, 0, 0)
        from gui.biota_source import RANK_LABEL
        chips.addWidget(self._chip(f"Species · {node.get('rank','').title()}"
                                   if node.get("rank") else "Species", rank=True))
        chips.addWidget(self._chip(f"{n_pics} photos", strong=str(n_pics)))
        if n_gps:
            chips.addWidget(self._chip(f"{n_gps} GPS points", strong=str(n_gps)))
        if countries:
            chips.addWidget(self._chip(" · ".join(sorted(countries)[:3])))
        chips.addStretch(1)
        wrapchips = QWidget(); wrapchips.setLayout(chips)
        il.addWidget(wrapchips)
        # Resolved BEFORE the EXIF block, which describes the hero picture and so
        # has to know which one it is.
        cover = host._source.cover_spec(self._sci)
        exif = self._build_exif(pics, cover)
        if exif is not None:
            il.addWidget(exif)
        il.addStretch(1)
        lay.addWidget(ident, 1)

        if cover is not None:
            from gui.biota_browser import _load_cover_spec
            host._loader.request(lambda s=cover: _load_cover_spec(s), self._set_hero, target_w=760)
        else:
            self._hero_label.setText("no photo")
        return wrap

    # ── EXIF under the chips ─────────────────────────────────────────────────
    @staticmethod
    def _hero_row(pics, cover):
        """The picture row the hero image on this page came from.

        The EXIF block describes THE PICTURE YOU ARE LOOKING AT, so it has to be
        that picture's row and not an average of the species. The cover is picked
        as a file path (best_dir/best_jpg), so it is matched back by file name —
        BestJPG is unique within a species. Falls back to the best-rated row, then
        the first, so the block never silently describes nothing.
        """
        if not pics:
            return None
        if cover:
            try:
                from pathlib import Path as _P
                name = _P(cover[1]).name.strip().lower()
                for p in pics:
                    if (p.get("BestJPG") or "").strip().lower() == name:
                        return p
            except (IndexError, TypeError, OSError):
                pass

        def rating(p):
            try:
                return int(str(p.get("Rating") or 0).strip() or 0)
            except (TypeError, ValueError):
                return 0
        return max(pics, key=rating)

    @staticmethod
    def _exif_rows(row):
        """camera / lens / exposure / distance / size / date for ONE picture —
        the same block the large image viewer shows, for the hero photo.

        Not available here: focal length. The master keeps shutter, aperture, ISO
        and distance packed into one `Settings` string and has no focal-length
        column at all, so the viewer's "focal 367.0 mm @ 46.75 m" can only be
        half-answered — the distance half, on its own line.

        Returns [(label, value), …], only for the lines that have something.
        """
        if not row:
            return []
        def val(field):
            return (row.get(field) or "").strip()

        rows = []
        cam = val("CameraModel")
        if cam:
            rows.append(("camera", cam))
        # The long name is what the large viewer shows and the more useful of the
        # two; the short one is the fallback when this picture has no long name.
        lens = val("LensLongName") or val("LensShortName")
        if lens:
            rows.append(("lens", lens))

        shutter, aperture, iso, dist = _parse_settings(val("Settings"))
        shot = [t for t in (f"{shutter}s" if shutter else "",
                            f"f/{aperture}" if aperture else "",
                            f"ISO {iso}" if iso else "") if t]
        if shot:
            rows.append(("exposure", "  ·  ".join(shot)))
        if dist:
            rows.append(("distance", f"{dist} m"))

        size = val("ImageSize")
        jpg = val("JPGSize")
        if size or jpg:
            rows.append(("size", (size + (f"   (JPG {jpg})" if jpg else "")) if size
                         else f"JPG {jpg}"))

        # 'YYYY:MM:DD HH:MM:SS' -> 'YYYY-MM-DD HH:MM:SS' (only the date's colons).
        stamp = val("DateTimeOriginal")
        if stamp:
            rows.append(("date", stamp[:19].replace(":", "-", 2)))
        return rows

    def _build_exif(self, pics, cover):
        rows = self._exif_rows(self._hero_row(pics, cover))
        if not rows:
            return None
        box = QWidget()
        gl = QGridLayout(box)
        gl.setContentsMargins(0, 10, 0, 0)
        gl.setHorizontalSpacing(14)
        gl.setVerticalSpacing(2)
        hdr = QLabel("EXIF")
        hdr.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace;"
                          f" font-size:10px; letter-spacing:1px;")
        gl.addWidget(hdr, 0, 0, 1, 2)
        for r, (label, value) in enumerate(rows, start=1):
            k = QLabel(label)
            k.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:11px;")
            k.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            v = QLabel(value)
            v.setWordWrap(True)
            v.setStyleSheet(f"color:{_INK}; font-family:Consolas,monospace; font-size:11px;")
            # Copyable like every other fact on this page — a lens name goes
            # straight into a photobook caption.
            _make_copyable(v, value)
            gl.addWidget(k, r, 0)
            gl.addWidget(v, r, 1)
        gl.setColumnStretch(1, 1)
        return box

    def _set_hero(self, px: QPixmap):
        if px is None or px.isNull() or self._hero_label is None:
            if self._hero_label is not None:
                self._hero_label.setText("no photo")
            return
        h = 300 - 18 - 8
        self._hero_label.setPixmap(px.scaledToHeight(
            h, Qt.TransformationMode.SmoothTransformation))
        self._hero_label.setText("")

    def _chip(self, text, strong=None, rank=False):
        c = QLabel(text)
        if rank:
            c.setStyleSheet(
                f"color:{_DONE}; background:{_DONE_BG}; border:1px solid {_LINE};"
                f" border-radius:12px; padding:3px 11px; font-family:Consolas,monospace;"
                f" font-size:11px; font-weight:600;")
        else:
            c.setStyleSheet(
                f"color:{_INK_SOFT}; background:{_SURFACE2}; border:1px solid {_LINE};"
                f" border-radius:12px; padding:3px 11px; font-family:Consolas,monospace; font-size:11px;")
        return c

    # action row: Map + Tech panel
    def _build_actions(self):
        host = self._host
        wrap = QWidget()
        wrap.setStyleSheet(f"background:{_SURFACE}; border-bottom:1px solid {_LINE};")
        lay = QHBoxLayout(wrap); lay.setContentsMargins(16, 4, 16, 10); lay.setSpacing(8)
        lay.addStretch(1)
        mapb = QPushButton("🗺 Map")
        mapb.setStyleSheet(self._btn_css())
        mapb.clicked.connect(lambda: host._panel_show_map(self._sci))
        lay.addWidget(mapb)
        tech = QPushButton("✎ Edit panel")
        tech.setToolTip("Open the Species Viewer to edit these pictures — "
                        "stars, colour, delete, best-JPG pick")
        tech.setStyleSheet(self._btn_css(tech=True))
        tech.clicked.connect(host._panel_open_tech)
        lay.addWidget(tech)
        return wrap

    def _btn_css(self, tech=False):
        if tech:
            return (f"QPushButton {{ border:1px solid {_READY}; color:{_READY};"
                    f" background:{_READY_BG}; border-radius:7px; padding:6px 12px; font-weight:600; }}"
                    f"QPushButton:hover {{ background:#d7e6f6; }}")
        return (f"QPushButton {{ border:1px solid {_LINE}; color:{_INK};"
                f" background:{_SURFACE}; border-radius:7px; padding:6px 12px; }}"
                f"QPushButton:hover {{ border-color:{_READY}; color:{_READY}; }}")

    # names & classification table
    def _build_names(self, src):
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 6, 0, 8); bl.setSpacing(6)
        hdr = QLabel("NAMES & CLASSIFICATION")
        hdr.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:10px;"
                          f" letter-spacing:1px;")
        bl.addWidget(hdr)

        chain = src.chain(self._sci)
        by_rank = {n["rank"]: n for n in chain}
        cols = [("SPECIES", "SPECIES"), ("FAMILY", "FAMILY"),
                ("ORDER", "ORDER"), ("CLASS", "CLASS")]
        grid = QGridLayout(); grid.setSpacing(0); grid.setContentsMargins(0, 0, 0, 0)

        def cell(text, r, c, *, head=False, tag=False, italic=False, copy_extras=None):
            lbl = QLabel(text or "—")
            lbl.setWordWrap(True)
            style = (f"border:1px solid {_LINE}; padding:6px 9px; font-size:12.5px;")
            if head:
                style += (f" background:{_SURFACE2}; color:{_INK_SOFT};"
                          f" font-family:Consolas,monospace; font-size:9.5px; letter-spacing:.5px;")
            elif tag:
                style += (f" color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:9px;")
            else:
                style += f" color:{_INK};"
                if italic:
                    style += " font-style:italic;"
                # Every name in this table is copyable on its own — the table is
                # where captions for a printed photobook get picked up.
                _make_copyable(lbl, text, copy_extras)
            lbl.setStyleSheet(style)
            grid.addWidget(lbl, r, c)

        from gui.name_copy import node_names, all_three_entries
        # Per column (rank), the "copy the whole set" entries every cell in it offers.
        combined = {rk: all_three_entries(node_names(by_rank.get(rk)))
                    for _lbl, rk in cols}

        cell("", 0, 0, head=True)
        for ci, (label, _rk) in enumerate(cols):
            cell(label.title(), 0, ci + 1, head=True)
        for ri, (lang, field) in enumerate((("NL", "nl"), ("EN", "en"), ("Sci", "sci"))):
            cell(lang, ri + 1, 0, tag=True)
            for ci, (_label, rk) in enumerate(cols):
                n = by_rank.get(rk)
                cell((n.get(field) if n else "") or "", ri + 1, ci + 1,
                     italic=(field == "sci"), copy_extras=combined[rk])
        for ci in range(len(cols) + 1):
            grid.setColumnStretch(ci, 1 if ci else 0)
        holder = QWidget(); holder.setLayout(grid)
        bl.addWidget(holder)
        return box

    # photos grouped by age/sex/plumage
    def _build_photos(self, pics):
        host = self._host
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 10, 0, 0); bl.setSpacing(4)

        # (re)build the shared provider for this species. "Show all files" (a
        # session-level host flag) drops the jpg-only filter so RAW-only frames
        # appear too — marked by the ABSENCE of a #.
        show_all = getattr(host, "_show_all_files", False)
        col = host._collection
        col.set_species_lang(host._lang)
        col.set_jpg_only(not show_all)
        col.set_camera_filter(set())
        try:
            col.set_color_filter(set())
        except Exception:
            pass
        col.set_sort("rate")
        col.rebuild(host._display(dict(host._source.node(self._sci))),
                    forced_sci=self._sci.casefold())

        # An album shows only its own (date-range) pictures out of the full-master
        # collection — count over the scoped items (None = all).
        scope = host._source.scope_upns(self._sci)
        idxs = [i for i in range(len(col))
                if scope is None or col.item(i).key in scope]
        n = len(idxs)
        n_raw = sum(1 for i in idxs if not col.item(i).has_jpg) if show_all else 0
        head_row = QWidget()
        hrl = QHBoxLayout(head_row); hrl.setContentsMargins(0, 6, 0, 0); hrl.setSpacing(8)
        hdr = QLabel(f"PHOTOS · {n - n_raw}" + (f"  +  {n_raw} RAW" if n_raw else ""))
        hdr.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:10px;"
                          f" letter-spacing:1px;")
        hrl.addWidget(hdr)
        hrl.addStretch(1)
        allbtn = QPushButton("Show all files")
        allbtn.setCheckable(True)
        allbtn.setChecked(show_all)
        allbtn.setToolTip("Include RAW-only frames — # marks the ones that have a JPG")
        allbtn.setStyleSheet(
            f"QPushButton {{ border:1px solid {_LINE}; border-radius:6px; padding:3px 10px;"
            f" font-size:12px; color:{_INK_SOFT}; }}"
            f"QPushButton:checked {{ background:{_READY_BG}; border-color:{_READY};"
            f" color:{_READY}; font-weight:600; }}")
        allbtn.clicked.connect(host._panel_toggle_all_files)
        hrl.addWidget(allbtn)
        bl.addWidget(head_row)
        if n == 0:
            empty = QLabel("No pictures for this species.")
            empty.setStyleSheet(f"color:{_INK_SOFT}; padding:16px 2px;")
            bl.addWidget(empty)
            return box

        # qualifier per UPN from the raw rows; group the sorted items by it
        qual = {}
        for p in pics:
            k = (p.get("UniquePictureName") or "")
            qual[k] = _group_label(p.get("malefemale"), p.get("ageintext"), p.get("summerwinter"))
        # Group the SCOPED items (idxs already excludes anything outside an album).
        groups = {}
        order = []
        for i in idxs:
            it = col.item(i)
            g = qual.get(it.key, "Unspecified")
            if g not in groups:
                groups[g] = []; order.append(g)
            groups[g].append(it)
        # Unspecified always last
        order = [g for g in order if g != "Unspecified"] + \
                (["Unspecified"] if "Unspecified" in groups else [])

        card_w = host._card_width()
        # A single "Unspecified" group is just a flat set — no header adds nothing.
        show_headers = not (len(order) == 1 and order[0] == "Unspecified")
        for g in order:
            items = groups[g]
            if show_headers:
                gh = QLabel(f"{g}   {len(items)} photo{'s' if len(items) != 1 else ''}")
                gh.setStyleSheet(f"color:{_INK}; font-weight:600; font-size:13.5px; margin-top:12px;")
                bl.addWidget(gh)
            row_wrap = QWidget()
            grid = QGridLayout(row_wrap); grid.setContentsMargins(0, 4, 0, 0); grid.setSpacing(10)
            ncols = max(2, host.columns)
            for idx, it in enumerate(items):
                tile = PhotoTile(it, col, host._loader, card_w,
                                 host._on_pic_open, host._on_pic_menu)
                grid.addWidget(tile, idx // ncols, idx % ncols,
                               Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                self._pic_cards.append(tile)
            for c in range(ncols):
                grid.setColumnStretch(c, 1)
            bl.addWidget(row_wrap)
        return box

    # sidebar: sibling species (same family) + orders with photos
    def _build_sidebar(self, src, node):
        host = self._host
        side = QWidget()
        side.setStyleSheet(f"background:{_SURFACE}; border-left:1px solid {_LINE};")
        sl = QVBoxLayout(side); sl.setContentsMargins(14, 8, 12, 12); sl.setSpacing(4)

        parent = src.node(node["up"]) if node.get("up") else None
        if parent is not None:
            title = QLabel(f"OTHER {parent['sci'].split(' ')[-1].upper()} (FAMILY)"
                           if parent["rank"] == "FAMILY" else "SIBLING SPECIES")
            title.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace;"
                                f" font-size:10px; letter-spacing:1px;")
            sl.addWidget(title)
            sibs = src.children(parent["sci"])
            for s in sibs[:self._MAX_SIBS]:
                sl.addWidget(self._sib_row(s, current=(s["sci"] == node["sci"])))
            if len(sibs) > self._MAX_SIBS:
                more = QPushButton(f"…and {len(sibs) - self._MAX_SIBS} more")
                more.setFlat(True)
                more.setCursor(Qt.CursorShape.PointingHandCursor)
                more.setStyleSheet(
                    f"QPushButton {{ color:{_READY}; font-size:11px; padding:4px 6px;"
                    f" border:none; text-align:left; }}"
                    f"QPushButton:hover {{ text-decoration:underline; }}")
                more.clicked.connect(lambda _c=False, k=parent["sci"]: host._go(k))
                sl.addWidget(more)

        # orders-with-photos (under the class ancestor)
        chain = src.chain(self._sci)
        klass = next((c for c in chain if c["rank"] == "CLASS"), None)
        if klass is not None:
            sl.addSpacing(10)
            oh = QLabel("ORDERS WITH PHOTOS")
            oh.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace;"
                             f" font-size:10px; letter-spacing:1px;")
            sl.addWidget(oh)
            # ALL orders that have photos, in taxonomic order (children() already
            # sorts by TargetPath — the numeric "040 …" prefix). The sidebar scrolls.
            for o in src.children(klass["sci"]):
                sl.addWidget(self._sib_row(o, order=True))
        sl.addStretch(1)
        return side

    def _sib_row(self, node, current=False, order=False):
        host = self._host
        row = QPushButton()
        row.setFlat(True)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setFixedHeight(40)
        bg = _DONE_BG if current else "transparent"
        row.setStyleSheet(
            f"QPushButton {{ text-align:left; border:none; border-radius:6px; background:{bg}; }}"
            f"QPushButton:hover {{ background:{_SURFACE2}; }}")
        lay = QHBoxLayout(row); lay.setContentsMargins(6, 3, 8, 3); lay.setSpacing(9)
        thumb = QLabel()
        thumb.setFixedSize(36, 27)
        thumb.setStyleSheet(f"border:2px solid {_FRAME}; border-radius:4px; background:{_SURFACE2};")
        lay.addWidget(thumb)
        txt = QWidget(); tl = QVBoxLayout(txt); tl.setContentsMargins(0, 0, 0, 0); tl.setSpacing(0)
        disp = host._display(dict(node))
        nm = QLabel(disp)
        nm.setStyleSheet(f"color:{_DONE if current else _INK}; font-size:12.5px;"
                         f" font-weight:{'600' if current else '400'}; border:none;")
        tl.addWidget(nm)
        if disp.casefold() != (node["sci"] or "").casefold():
            s = QLabel(node["sci"])
            s.setStyleSheet(f"color:{_INK_SOFT}; font-style:italic; font-size:10.5px; border:none;")
            tl.addWidget(s)
        lay.addWidget(txt, 1)
        cnt = QLabel(str(host._source.counts(node["sci"])[1]))
        cnt.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:10px; border:none;")
        lay.addWidget(cnt, 0)

        target = node["sci"]
        if order:
            row.clicked.connect(lambda _c=False, k=target: host._go(k))
        else:
            row.clicked.connect(lambda _c=False, k=target: host._open_species(k))

        # Right-click a sidebar row to copy its names — the row's own click
        # navigates, so the names here can't be selectable labels either.
        names = {k: node.get(k) for k in ("nl", "en", "sci")}
        row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        row.customContextMenuRequested.connect(
            lambda pos, _r=row, _n=names: _row_copy_menu(_r, _n, pos))

        cover = host._source.cover_spec(target)
        if cover is not None:
            from gui.biota_browser import _load_cover_spec
            host._loader.request(
                lambda s=cover: _load_cover_spec(s),
                lambda px, _t=thumb: self._set_thumb(px, _t), target_w=80)
        return row

    def _set_thumb(self, px, label):
        if px is None or px.isNull():
            return
        try:
            label.setPixmap(px.scaled(36, 27, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                      Qt.TransformationMode.SmoothTransformation))
        except RuntimeError:
            pass
