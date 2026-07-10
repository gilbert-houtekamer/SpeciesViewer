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
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget)
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
        self._color = QColor("#d32f2f")
        self._tiles: dict[tuple[int, int, int], object] = {}
        self._markers: list = []
        self._points: list[dict] = []       # aggregated {lat,lon,label,count}
        self._pending: set = set()
        self._syncing = False               # guard scrollContentsBy re-entry

        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_tile_reply)
        _tiles_dir().mkdir(parents=True, exist_ok=True)

    # ── data ─────────────────────────────────────────────────────────────────
    def set_color(self, color: str):
        self._color = QColor(color)

    def set_points(self, points: list[dict]):
        """Merge same-spot points (rounded to ~1 m) into one pin with a count."""
        agg: dict[tuple, dict] = {}
        for p in points:
            k = (round(p["lat"], 5), round(p["lon"], 5))
            if k in agg:
                agg[k]["count"] += 1
            else:
                agg[k] = {"lat": p["lat"], "lon": p["lon"],
                          "label": p.get("place", ""), "count": 1}
        self._points = list(agg.values())

    # ── view state ───────────────────────────────────────────────────────────
    def fit_to_points(self):
        """Choose zoom + center so every point is visible (world view when the
        species spans continents; close-in when it's local)."""
        if not self._points:
            self._z, self._center = 2, (5.0, 30.0)
        elif len(self._points) == 1:
            p = self._points[0]
            self._z, self._center = 10, (p["lon"], p["lat"])
        else:
            lats = [p["lat"] for p in self._points]
            lons = [p["lon"] for p in self._points]
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
        n = TILE * (2 ** self._z)
        self._scene.setSceneRect(0, 0, n, n)
        self._reposition_markers()
        self._prune_other_zooms()
        self._center_on_state()
        self._ensure_tiles()

    def _center_on_state(self):
        cx, cy = lonlat_to_world(self._center[0], self._center[1], self._z)
        self._syncing = True
        self.centerOn(cx, cy)
        self._syncing = False

    # ── markers ──────────────────────────────────────────────────────────────
    def _reposition_markers(self):
        for m in self._markers:
            self._scene.removeItem(m)
        self._markers = []
        r = 6
        pen = QPen(QColor("white"))
        pen.setWidth(2)
        brush = QBrush(self._color)
        for p in self._points:
            x, y = lonlat_to_world(p["lon"], p["lat"], self._z)
            dot = QGraphicsEllipseItem(x - r, y - r, 2 * r, 2 * r)
            dot.setBrush(brush)
            dot.setPen(pen)
            dot.setZValue(10)
            times = f"  ({p['count']}×)" if p["count"] > 1 else ""
            dot.setToolTip(f"{p['label'] or '—'}{times}\n{p['lat']:.5f}, {p['lon']:.5f}")
            self._scene.addItem(dot)
            self._markers.append(dot)

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


class MapView(QDialog):
    """Framed window around the map canvas: title + point count + Fit + the
    required OSM attribution."""

    def __init__(self, parent, title: str, points: list[dict], *,
                 color: str = "#d32f2f"):
        super().__init__(parent)
        self.setWindowTitle(f"Map — {title}")
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(940, 660)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QWidget()
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 4, 8, 4)
        n = len(points)
        h.addWidget(QLabel(f"<b>{title}</b> — {n} GPS point{'s' if n != 1 else ''}"))
        h.addStretch()
        fit_btn = QPushButton("Fit")
        fit_btn.setToolTip("Zoom to fit all points")
        h.addWidget(fit_btn)
        lay.addWidget(header)

        self._map = TileMapView(self)
        self._map.set_color(color)
        self._map.set_points(points)
        lay.addWidget(self._map, 1)
        fit_btn.clicked.connect(self._map.fit_to_points)

        # OSM attribution, pinned bottom-right over the map (usage-policy required).
        self._attr = QLabel("© OpenStreetMap contributors", self._map)
        self._attr.setStyleSheet(
            "background: rgba(255,255,255,0.75); color:#333; font-size:9px; "
            "padding:1px 5px; border-top-left-radius:3px;")
        self._attr.adjustSize()
        self._map.resized.connect(self._place_attribution)

        # Fit once the viewport has a real size (after the first layout pass).
        QTimer.singleShot(0, self._map.fit_to_points)

    def _place_attribution(self):
        vp = self._map.viewport()
        self._attr.adjustSize()
        self._attr.move(vp.width() - self._attr.width(),
                        vp.height() - self._attr.height())
        self._attr.raise_()
