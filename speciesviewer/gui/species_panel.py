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

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap

logger = logging.getLogger("speciesviewer")

# palette — mirrors gui/biota_browser (kept local to avoid an import cycle; both
# fold into the shared gui/theme.py when the ch.15 global QSS lands)
_INK = "#1c211b"; _INK_SOFT = "#59634f"; _LINE = "#d7ddce"
_SURFACE = "#ffffff"; _SURFACE2 = "#eef1ea"
_FRAME = "#6d8764"; _FRAME_INK = "#ffffff"; _MAT = "#ffffff"
_DONE = "#2f7d43"; _DONE_BG = "#e6f1e8"; _READY = "#2563a8"; _READY_BG = "#e4eef8"

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


def _load_qimage(path):
    from PyQt6.QtGui import QImage
    if path is None:
        return None
    img = QImage(str(path))
    return None if img.isNull() else img


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

        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(16, 8, 16, 16)
        bl.setSpacing(16)
        main = QWidget(); ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0); ml.setSpacing(6)
        ml.addWidget(self._build_names(src))
        ml.addWidget(self._build_photos(pics))
        ml.addStretch(1)
        bl.addWidget(main, 1)
        bl.addWidget(self._build_sidebar(src, node), 0)
        self._outer.addWidget(body, 1)

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
        nm = QLabel(display)
        nm.setStyleSheet(f"color:{_INK}; font-size:28px; font-weight:700;")
        nm.setWordWrap(True)
        il.addWidget(nm)
        sub = node["sci"] + (f" · {node['en']}" if node.get("en")
                             and node["en"].casefold() != display.casefold() else "")
        sci = QLabel(sub)
        sci.setStyleSheet(f"color:{_INK_SOFT}; font-style:italic; font-size:15px;")
        sci.setWordWrap(True)
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
        il.addStretch(1)
        lay.addWidget(ident, 1)

        cover = host._source.cover_path(self._sci)
        if cover is not None:
            host._loader.request(lambda p=cover: _load_qimage(p), self._set_hero, target_w=760)
        else:
            self._hero_label.setText("no photo")
        return wrap

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
        tech = QPushButton("⚙ Tech panel")
        tech.setToolTip("Open the editable grid — stars, colour, delete, best-JPG pick")
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

        def cell(text, r, c, *, head=False, tag=False, italic=False):
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
            lbl.setStyleSheet(style)
            grid.addWidget(lbl, r, c)

        cell("", 0, 0, head=True)
        for ci, (label, _rk) in enumerate(cols):
            cell(label.title(), 0, ci + 1, head=True)
        for ri, (lang, field) in enumerate((("NL", "nl"), ("EN", "en"), ("Sci", "sci"))):
            cell(lang, ri + 1, 0, tag=True)
            for ci, (_label, rk) in enumerate(cols):
                n = by_rank.get(rk)
                cell((n.get(field) if n else "") or "", ri + 1, ci + 1,
                     italic=(field == "sci"))
        for ci in range(len(cols) + 1):
            grid.setColumnStretch(ci, 1 if ci else 0)
        holder = QWidget(); holder.setLayout(grid)
        bl.addWidget(holder)
        return box

    # photos grouped by age/sex/plumage
    def _build_photos(self, pics):
        from gui.rating_editor import PictureCard
        host = self._host
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 10, 0, 0); bl.setSpacing(4)

        # (re)build the shared provider for this species; jpg-only pretty view
        col = host._collection
        col.set_species_lang(host._lang)
        col.set_jpg_only(True)
        col.set_camera_filter(set())
        try:
            col.set_color_filter(set())
        except Exception:
            pass
        col.set_sort("rate")
        col.rebuild(host._display(dict(host._source.node(self._sci))),
                    forced_sci=self._sci.casefold())

        n = len(col)
        hdr = QLabel(f"PHOTOS · {n}")
        hdr.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:10px;"
                          f" letter-spacing:1px; margin-top:6px;")
        bl.addWidget(hdr)
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
        groups = {}
        order = []
        for i in range(n):
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
                card = PictureCard(it, card_w, show_kind_tag=False)
                card.card_double_clicked.connect(host._on_pic_open)
                card.card_right_clicked.connect(host._on_pic_menu)
                card.card_changed.connect(host._on_pic_changed)
                grid.addWidget(card, idx // ncols, idx % ncols)
                self._pic_cards.append(card)
            for c in range(ncols):
                grid.setColumnStretch(c, 1)
            bl.addWidget(row_wrap)
        return box

    # sidebar: sibling species (same family) + orders with photos
    def _build_sidebar(self, src, node):
        host = self._host
        side = QWidget(); side.setFixedWidth(248)
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
                more = QLabel(f"  …and {len(sibs) - self._MAX_SIBS} more")
                more.setStyleSheet(f"color:{_INK_SOFT}; font-size:11px; padding:4px 6px;")
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
            orders = sorted(src.children(klass["sci"]),
                            key=lambda o: -src.counts(o["sci"])[1])[:6]
            for o in orders:
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

        cover = host._source.cover_path(target)
        if cover is not None:
            host._loader.request(
                lambda p=cover: _load_qimage(p),
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
