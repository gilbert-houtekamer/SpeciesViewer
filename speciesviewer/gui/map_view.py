"""Native slippy-map (OpenStreetMap tiles via QGraphicsView) — Phase 1a of
maps_plan.md.

A dependency-free interactive map (no QtWebEngine): OSM XYZ tiles rendered as
QGraphicsPixmapItems in Web-Mercator "world pixel" scene coordinates at the
current integer zoom, markers as QGraphicsItems. Read-only species distribution
for now — pins for a species' GPS points, same-spot points merged into one pin
with a count. Pan = hand-drag, wheel = zoom toward the cursor.

Later phases (see maps_plan.md): up to 4 species overlays (1b), zoom-dependent
clustering (1c), drag-to-geotag (2), rectangle-select ↔ grid/large selection (3).

Tiles are disk-cached per machine and fetched only for the visible viewport, with
an honest User-Agent — per the OSM tile usage policy. Attribution is shown on the
map (required).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QGraphicsView, QGraphicsScene, QGraphicsEllipseItem,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QMessageBox)
from PyQt6.QtGui import QPixmap, QPainter, QColor, QBrush, QPen
from PyQt6.QtCore import Qt, QPointF, QUrl, QTimer, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

logger = logging.getLogger("speciesviewer")

TILE = 256
MIN_ZOOM, MAX_ZOOM = 2, 18
_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_USER_AGENT = b"SpeciesViewer/1.0 (gilbert@houtekamer.com)"
_MERC_MAX_LAT = 85.05112878   # Web-Mercator clamp


def _tiles_dir() -> Path:
    from utils.cache_paths import app_data_dir
    return Path(app_data_dir()) / "map_tiles"


def lonlat_to_world(lon: float, lat: float, z: int) -> tuple[float, float]:
    """Longitude/latitude → Web-Mercator world-pixel coords at zoom `z`."""
    n = TILE * (2 ** z)
    lat = max(min(lat, _MERC_MAX_LAT), -_MERC_MAX_LAT)
    lr = math.radians(lat)
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lr) + 1.0 / math.cos(lr)) / math.pi) / 2.0 * n
    return x, y


def world_to_lonlat(x: float, y: float, z: int) -> tuple[float, float]:
    n = TILE * (2 ** z)
    # Clamp into the valid world before projecting. Mid-zoom the view transform
    # can briefly report a scene point far outside [0, n]; without this the
    # sinh() below overflows (OverflowError: math range error).
    x = min(max(x, 0.0), n)
    y = min(max(y, 0.0), n)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lon, lat


class TileMapView(QGraphicsView):
    """The map canvas: tiles + markers, hand-drag pan, wheel zoom-to-cursor."""

    resized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#dfe6ea"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)

        self._z = 3
        self._center = (5.0, 30.0)          # (lon, lat)
        # Species layers: id -> {"color": QColor, "points": [agg], "items": []}.
        # Each aggregated point is {lat, lon, label, count} (same-spot merged).
        self._layers: dict[int, dict] = {}
        self._next_layer_id = 0
        self._tiles: dict[tuple[int, int, int], object] = {}
        self._pending: set = set()
        self._syncing = False               # guard scrollContentsBy re-entry

        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_tile_reply)
        _tiles_dir().mkdir(parents=True, exist_ok=True)

    # ── layers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _aggregate(points: list[dict]) -> list[dict]:
        """Merge same-spot points (rounded to ~1 m) into one pin with a count."""
        agg: dict[tuple, dict] = {}
        for p in points:
            k = (round(p["lat"], 5), round(p["lon"], 5))
            if k in agg:
                agg[k]["count"] += 1
            else:
                agg[k] = {"lat": p["lat"], "lon": p["lon"],
                          "label": p.get("place", ""), "count": 1}
        return list(agg.values())

    def add_layer(self, color: str, points: list[dict],
                  nl: str = "", en: str = "") -> int:
        """Add a coloured species layer; returns its id (for remove_layer).
        `nl`/`en` are the species' Dutch/English names, shown in each pin's
        tooltip together with the same-spot count."""
        lid = self._next_layer_id
        self._next_layer_id += 1
        self._layers[lid] = {"color": QColor(color), "nl": nl, "en": en,
                             "points": self._aggregate(points), "items": []}
        self._draw_layer(lid)
        return lid

    def remove_layer(self, lid: int):
        lay = self._layers.pop(lid, None)
        if lay:
            for it in lay["items"]:
                self._scene.removeItem(it)

    def all_points(self) -> list[dict]:
        return [p for lay in self._layers.values() for p in lay["points"]]

    def _draw_layer(self, lid: int):
        lay = self._layers[lid]
        for it in lay["items"]:
            self._scene.removeItem(it)
        lay["items"] = []
        r = 6
        pen = QPen(QColor("white"))
        pen.setWidth(2)
        brush = QBrush(lay["color"])
        nl, en = lay.get("nl", ""), lay.get("en", "")
        for p in lay["points"]:
            x, y = lonlat_to_world(p["lon"], p["lat"], self._z)
            dot = QGraphicsEllipseItem(x - r, y - r, 2 * r, 2 * r)
            dot.setBrush(brush)
            dot.setPen(pen)
            dot.setZValue(10)
            dot.setToolTip(self._pin_tip(p["count"], nl, en))
            self._scene.addItem(dot)
            lay["items"].append(dot)

    @staticmethod
    def _pin_tip(count: int, nl: str, en: str) -> str:
        """Pin tooltip: '<count>× <Dutch name>' then the English name on its own
        line (count omitted when it's a single picture)."""
        head = (f"{count}× " if count > 1 else "") + (nl or "—")
        return f"{head}\n{en}" if (en and en.lower() != (nl or "").lower()) else head

    # ── view state ───────────────────────────────────────────────────────────
    def fit_to_points(self):
        """Choose zoom + center so every point is visible (world view when the
        species spans continents; close-in when it's local)."""
        pts = self.all_points()
        if not pts:
            self._z, self._center = 2, (5.0, 30.0)
        elif len(pts) == 1:
            p = pts[0]
            self._z, self._center = 10, (p["lon"], p["lat"])
        else:
            lats = [p["lat"] for p in pts]
            lons = [p["lon"] for p in pts]
            minlat, maxlat, minlon, maxlon = min(lats), max(lats), min(lons), max(lons)
            self._center = ((minlon + maxlon) / 2.0, (minlat + maxlat) / 2.0)
            vw = max(self.viewport().width(), 100)
            vh = max(self.viewport().height(), 100)
            z = MAX_ZOOM
            while z > MIN_ZOOM:
                x0, y0 = lonlat_to_world(minlon, maxlat, z)
                x1, y1 = lonlat_to_world(maxlon, minlat, z)
                if (x1 - x0) <= vw * 0.9 and (y1 - y0) <= vh * 0.9:
                    break
                z -= 1
            self._z = z
        self._refresh_all()

    def _refresh_all(self):
        # Guard the WHOLE rebuild: setSceneRect + centerOn both fire
        # scrollContentsBy, which would otherwise recompute _center from a
        # transform that hasn't caught up to the new zoom yet (→ overflow).
        self._syncing = True
        try:
            n = TILE * (2 ** self._z)
            self._scene.setSceneRect(0, 0, n, n)
            self._reposition_markers()
            self._prune_other_zooms()
            cx, cy = lonlat_to_world(self._center[0], self._center[1], self._z)
            self.centerOn(cx, cy)
        finally:
            self._syncing = False
        self._ensure_tiles()

    def _center_on_state(self):
        cx, cy = lonlat_to_world(self._center[0], self._center[1], self._z)
        self._syncing = True
        try:
            self.centerOn(cx, cy)
        finally:
            self._syncing = False

    # ── markers ──────────────────────────────────────────────────────────────
    def _reposition_markers(self):
        for lid in self._layers:
            self._draw_layer(lid)

    # ── tiles ────────────────────────────────────────────────────────────────
    def _ensure_tiles(self):
        if self.scene() is None:
            return
        tl = self.mapToScene(0, 0)
        br = self.mapToScene(self.viewport().width(), self.viewport().height())
        maxt = 2 ** self._z - 1
        tx0 = max(0, int(tl.x() // TILE));  tx1 = min(maxt, int(br.x() // TILE))
        ty0 = max(0, int(tl.y() // TILE));  ty1 = min(maxt, int(br.y() // TILE))
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                self._want_tile(self._z, tx, ty)

    def _want_tile(self, z, tx, ty):
        key = (z, tx, ty)
        if key in self._tiles or key in self._pending:
            return
        cache = _tiles_dir() / str(z) / str(tx) / f"{ty}.png"
        if cache.exists():
            pm = QPixmap(str(cache))
            if not pm.isNull():
                self._place_tile(z, tx, ty, pm)
                return
        self._pending.add(key)
        req = QNetworkRequest(QUrl(_TILE_URL.format(z=z, x=tx, y=ty)))
        req.setRawHeader(b"User-Agent", _USER_AGENT)
        reply = self._nam.get(req)
        reply.setProperty("tilekey", key)

    def _on_tile_reply(self, reply):
        key = reply.property("tilekey")
        reply.deleteLater()
        if key is None:
            return
        z, tx, ty = key
        self._pending.discard(key)
        if reply.error() != QNetworkReply.NetworkError.NoError:
            logger.debug(f"map tile {z}/{tx}/{ty} failed: {reply.errorString()}")
            return
        data = reply.readAll()
        pm = QPixmap()
        if not pm.loadFromData(data):
            return
        cache = _tiles_dir() / str(z) / str(tx) / f"{ty}.png"
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(bytes(data))
        except OSError:
            pass
        if z == self._z:
            self._place_tile(z, tx, ty, pm)

    def _place_tile(self, z, tx, ty, pm):
        key = (z, tx, ty)
        if key in self._tiles:
            return
        item = self._scene.addPixmap(pm)
        item.setOffset(tx * TILE, ty * TILE)
        item.setZValue(-1)
        self._tiles[key] = item

    def _prune_other_zooms(self):
        for key in list(self._tiles):
            if key[0] != self._z:
                self._scene.removeItem(self._tiles.pop(key))

    # ── interaction ──────────────────────────────────────────────────────────
    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        if self._syncing or self.scene() is None:
            return
        c = self.mapToScene(self.viewport().rect().center())
        self._center = world_to_lonlat(c.x(), c.y(), self._z)
        self._ensure_tiles()

    def wheelEvent(self, e):
        step = 1 if e.angleDelta().y() > 0 else -1
        new_z = max(MIN_ZOOM, min(MAX_ZOOM, self._z + step))
        if new_z == self._z:
            return
        vp = e.position()
        scene_pt = self.mapToScene(int(vp.x()), int(vp.y()))
        lon, lat = world_to_lonlat(scene_pt.x(), scene_pt.y(), self._z)
        cvp = self.viewport().rect().center()
        off_x, off_y = vp.x() - cvp.x(), vp.y() - cvp.y()   # cursor offset (px = scene px)
        self._z = new_z
        wx, wy = lonlat_to_world(lon, lat, new_z)            # geo's new world pos
        self._center = world_to_lonlat(wx - off_x, wy - off_y, new_z)  # keep it under cursor
        self._refresh_all()
        e.accept()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._center_on_state()
        self._ensure_tiles()
        self.resized.emit()


# Distinct, colour-blind-friendlier layer palette (up to 4 species overlaid).
_PALETTE = ["#d32f2f", "#1565c0", "#2e7d32", "#8e24aa"]   # red, blue, green, purple
_MAX_LAYERS = 4


class MapView(QDialog):
    """Framed window around the map canvas — up to 4 species overlaid, each its
    own colour, with a legend, an "Add species…" picker, Fit, and the required
    OSM attribution. `master_db` supplies both the picker list and each species'
    GPS points."""

    def __init__(self, parent, master_db, initial_sci: str, initial_display: str,
                 initial_points: list[dict]):
        super().__init__(parent)
        self._db = master_db
        self._used: dict[str, dict] = {}   # sci.casefold() -> {sci,display,color,lid}
        self.setWindowTitle("Species distribution map")
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(940, 680)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QWidget()
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 4, 8, 4)
        self._add_btn = QPushButton("Add species…")
        self._add_btn.setToolTip("Overlay another species (up to 4) to compare ranges")
        self._add_btn.clicked.connect(self._on_add_species)
        h.addWidget(self._add_btn)
        # Legend chips live between the buttons.
        self._legend = QHBoxLayout()
        self._legend.setSpacing(10)
        h.addSpacing(8)
        h.addLayout(self._legend)
        h.addStretch()
        fit_btn = QPushButton("Fit")
        fit_btn.setToolTip("Zoom to fit all points")
        fit_btn.clicked.connect(self._map_fit)
        h.addWidget(fit_btn)
        lay.addWidget(header)

        self._map = TileMapView(self)
        lay.addWidget(self._map, 1)

        # OSM attribution, pinned bottom-right over the map (usage-policy required).
        self._attr = QLabel("© OpenStreetMap contributors", self._map)
        self._attr.setStyleSheet(
            "background: rgba(255,255,255,0.75); color:#333; font-size:9px; "
            "padding:1px 5px; border-top-left-radius:3px;")
        self._attr.adjustSize()
        self._map.resized.connect(self._place_attribution)

        self._add_layer(initial_sci, initial_display, initial_points)
        QTimer.singleShot(0, self._map.fit_to_points)   # after first layout pass

    # ── layers / legend ────────────────────────────────────────────────────────
    def _map_fit(self):
        self._map.fit_to_points()

    def _next_color(self) -> str:
        used = {m["color"] for m in self._used.values()}
        return next((c for c in _PALETTE if c not in used), _PALETTE[-1])

    def _add_layer(self, sci: str, display: str, points: list[dict]):
        key = (sci or "").casefold()
        if not key or key in self._used:
            return
        if len(self._used) >= _MAX_LAYERS:
            QMessageBox.information(self, "Add species",
                                    f"Up to {_MAX_LAYERS} species at once — remove "
                                    f"one first (× in the legend).")
            return
        color = self._next_color()
        # Dutch + English names for the pin tooltips (from the species table);
        # fall back to the display name when the species isn't mirrored there.
        row = (self._db.species_row(sci) if self._db else None) or {}
        nl = (row.get("nl") or display or "").strip()
        en = (row.get("en") or "").strip()
        lid = self._map.add_layer(color, points, nl=nl, en=en)
        self._used[key] = {"sci": sci, "display": display, "color": color,
                           "lid": lid, "n": len(points)}
        self._rebuild_legend()

    def _remove_layer(self, key: str):
        m = self._used.pop(key, None)
        if m:
            self._map.remove_layer(m["lid"])
            self._rebuild_legend()

    def _rebuild_legend(self):
        while self._legend.count():
            item = self._legend.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for key, m in self._used.items():
            chip = QWidget()
            row = QHBoxLayout(chip)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(
                f"background:{m['color']}; border:1px solid #fff; border-radius:6px;")
            row.addWidget(swatch)
            name = QLabel(f"{m['display']} ({m['n']})")
            name.setStyleSheet("font-size: 11px;")
            row.addWidget(name)
            x = QPushButton("×")
            x.setFixedSize(16, 16)
            x.setToolTip(f"Remove {m['display']}")
            x.setStyleSheet("QPushButton{border:none;color:#888;font-weight:bold;}"
                            "QPushButton:hover{color:#d32f2f;}")
            x.clicked.connect(lambda _=False, k=key: self._remove_layer(k))
            row.addWidget(x)
            self._legend.addWidget(chip)

    def _on_add_species(self):
        if self._db is None:
            return
        rows = self._db.species_picker_rows()   # species that actually have pictures
        picked = self._pick_species(rows)
        if not picked:
            return
        sci, display = picked
        if sci.casefold() in self._used:
            return
        points = self._db.gps_points_for_species(sci)
        if not points:
            QMessageBox.information(self, "Add species",
                                    f"No GPS points for {display}.")
            return
        self._add_layer(sci, display, points)
        self._map.fit_to_points()

    def _pick_species(self, rows: list[dict]):
        """Searchable picker over species-with-pictures. Returns (sci, display)."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QComboBox, QCompleter, QDialogButtonBox
        name_to = {}
        names = []
        for r in rows:
            sci = (r.get("sci") or "").strip()
            display = (r.get("nl") or r.get("en") or sci).strip()
            for nm in (r.get("nl"), r.get("en"), sci):
                nm = (nm or "").strip()
                if nm:
                    name_to.setdefault(nm.casefold(), (sci, display))
                    names.append(nm)
        names = sorted(set(names), key=str.casefold)
        dlg = QDialog(self)
        dlg.setWindowTitle("Add species to map")
        dlg.setMinimumWidth(420)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Overlay another species:"))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(names)
        combo.setCurrentIndex(-1)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.lineEdit().setPlaceholderText("Type a Dutch, English or scientific name…")
        comp = QCompleter(names, dlg)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        combo.setCompleter(comp)
        v.addWidget(combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        combo.setFocus()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return name_to.get(combo.currentText().strip().casefold())

    def _place_attribution(self):
        vp = self._map.viewport()
        self._attr.adjustSize()
        self._attr.move(vp.width() - self._attr.width(),
                        vp.height() - self._attr.height())
        self._attr.raise_()
