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
    QGraphicsSimpleTextItem, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QMessageBox, QSplitter, QComboBox)
from PyQt6.QtGui import QPixmap, QPainter, QColor, QBrush, QPen, QFont
from PyQt6.QtCore import Qt, QPointF, QUrl, QTimer, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

logger = logging.getLogger("speciesviewer")

TILE = 256
MIN_ZOOM, MAX_ZOOM = 2, 18
_USER_AGENT = b"SpeciesViewer/1.0 (gilbert@houtekamer.com)"
_MERC_MAX_LAT = 85.05112878   # Web-Mercator clamp

# Selectable basemaps. `url` is an XYZ tile template; `sub` is the per-provider
# disk-cache folder; `max` is the provider's top zoom (we don't fetch beyond it);
# `attr` is the required on-map attribution; `wash` is a white overlay opacity
# that mutes over-saturated tiles (OpenTopoMap's relief is vivid). Both providers
# are openly licensed (OSM / OpenTopoMap CC-BY-SA). Terrain gives the paper-atlas
# relief the user wanted and is the default.
_BASEMAPS = [
    {"name": "Terrain", "sub": "opentopo", "max": 17, "wash": 0.35,
     "url": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
     "attr": "© OpenTopoMap (CC-BY-SA) · © OpenStreetMap contributors"},
    {"name": "Standard", "sub": "osm", "max": 19, "wash": 0.0,
     "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
     "attr": "© OpenStreetMap contributors"},
]

# Distinct, colour-blind-friendlier layer palette (up to 4 species overlaid).
_PALETTE = ["#d32f2f", "#1565c0", "#2e7d32", "#8e24aa"]   # red, blue, green, purple
_MAX_LAYERS = 4
# Fixed per-colour pixel offset (a rosette): where two species share a spot,
# each fans out in its own diagonal instead of hiding the other. Constant screen
# pixels (scene==view px at scale 1), so it's a tight cluster at any zoom and
# imperceptible where species don't overlap. Indexed by palette position. Sized
# so two overlapping COUNT BUBBLES (not just dots) keep their numbers legible —
# the diagonal means the visible horizontal gap is ~1.4× smaller than this.
_JITTER = [(-8, -8), (8, -8), (8, 8), (-8, 8)]           # NW, NE, SE, SW


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
    bubble_activated = pyqtSignal(object)   # cluster dict on single-click

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Manual pan (not ScrollHandDrag) so the cursor is a normal pointer that
        # turns into a hand only over a clickable bubble — the grab-hand implied
        # "drag only" and hid that bubbles are clickable.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#dfe6ea"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)

        self._z = 3
        self._center = (5.0, 30.0)          # (lon, lat)
        self._basemap = _BASEMAPS[0]        # current tile provider
        # Species layers: id -> {"color": QColor, "points": [agg], "items": []}.
        # Each aggregated point is {lat, lon, label, count} (same-spot merged).
        self._layers: dict[int, dict] = {}
        self._next_layer_id = 0
        self._tiles: dict = {}
        self._wash = None                   # white overlay that mutes vivid tiles
        self._pending: set = set()
        self._syncing = False               # guard scrollContentsBy re-entry
        self._press_vp = None               # click-vs-drag detection
        self._pan_last = None               # last pos during a manual pan drag
        self._highlight = None              # transient ring around the active bubble

        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_tile_reply)
        _tiles_dir().mkdir(parents=True, exist_ok=True)

    # ── layers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _aggregate(points: list[dict]) -> list[dict]:
        """Merge same-spot points (rounded to ~1 m) into one pin, keeping the
        picture count and the member UPNs (so a bubble knows its photos)."""
        agg: dict[tuple, dict] = {}
        for p in points:
            k = (round(p["lat"], 5), round(p["lon"], 5))
            if k not in agg:
                agg[k] = {"lat": p["lat"], "lon": p["lon"],
                          "label": p.get("place", ""), "count": 0, "upns": []}
            agg[k]["count"] += 1
            if p.get("upn"):
                agg[k]["upns"].append(p["upn"])
        return list(agg.values())

    def add_layer(self, color: str, points: list[dict],
                  nl: str = "", en: str = "", sci: str = "") -> int:
        """Add a coloured species layer; returns its id (for remove_layer).
        `nl`/`en` are the species' Dutch/English names (pin tooltip); `sci` is
        the scientific name, carried on each bubble so a click can pull that
        species' pictures."""
        lid = self._next_layer_id
        self._next_layer_id += 1
        # Rosette offset keyed to the colour's palette slot, so co-located
        # species fan out instead of hiding each other.
        try:
            offset = _JITTER[_PALETTE.index(color) % len(_JITTER)]
        except ValueError:
            offset = (0, 0)
        self._layers[lid] = {"color": QColor(color), "nl": nl, "en": en,
                             "sci": sci, "offset": offset,
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

    # Cluster distinct spots within this many screen pixels at the current zoom
    # into one bubble; zooming in shrinks the geographic span a pixel covers, so
    # clusters split apart naturally.
    _CLUSTER_PX = 46

    def _cluster(self, points: list[dict]) -> list[dict]:
        """Greedy pixel-distance clustering of the layer's distinct spots at the
        current zoom. Each cluster carries the picture total, the member count,
        a centroid, and the lat/lon bounds (for click-to-zoom). O(n²) but n is a
        few dozen per species."""
        proj = [(lonlat_to_world(p["lon"], p["lat"], self._z), p) for p in points]
        used = [False] * len(proj)
        r2 = self._CLUSTER_PX * self._CLUSTER_PX
        clusters = []
        for i, ((xi, yi), _pi) in enumerate(proj):
            if used[i]:
                continue
            used[i] = True
            members = [proj[i][1]]
            for j in range(i + 1, len(proj)):
                if used[j]:
                    continue
                (xj, yj), _pj = proj[j]
                if (xj - xi) ** 2 + (yj - yi) ** 2 <= r2:
                    used[j] = True
                    members.append(proj[j][1])
            lats = [m["lat"] for m in members]
            lons = [m["lon"] for m in members]
            clusters.append({
                "lat": sum(lats) / len(lats), "lon": sum(lons) / len(lons),
                "count": sum(m["count"] for m in members), "n": len(members),
                "bounds": (min(lats), max(lats), min(lons), max(lons)),
                "upns": [u for m in members for u in m.get("upns", [])]})
        return clusters

    def _draw_layer(self, lid: int):
        lay = self._layers[lid]
        for it in lay["items"]:
            self._scene.removeItem(it)
        lay["items"] = []
        pen = QPen(QColor("white"))
        pen.setWidth(2)
        brush = QBrush(lay["color"])
        nl, en = lay.get("nl", ""), lay.get("en", "")
        sci = lay.get("sci", "")
        ox, oy = lay.get("offset", (0, 0))
        for c in self._cluster(lay["points"]):
            x, y = lonlat_to_world(c["lon"], c["lat"], self._z)
            total = c["count"]
            # Single-picture dot is small; multi grows gently with the count.
            r = 6 if total <= 1 else min(20, 8 + 3 * (len(str(total)) - 1) + 2)
            # Each marker carries its cluster: species + member UPNs (→ filmstrip
            # / large view) + bounds (→ double-click zoom, only when it hides
            # several DISTINCT spots).
            data = {"sci": sci, "nl": nl, "en": en, "upns": c["upns"],
                    "count": total, "n": c["n"],
                    "bounds": c["bounds"] if c["n"] > 1 else None}
            dot = QGraphicsEllipseItem(x - r + ox, y - r + oy, 2 * r, 2 * r)
            dot.setBrush(brush)
            dot.setPen(pen)
            dot.setZValue(10)
            dot.setToolTip(self._pin_tip(total, nl, en))
            dot.setData(0, data)
            self._scene.addItem(dot)
            lay["items"].append(dot)
            if total > 1:
                label = QGraphicsSimpleTextItem(str(total))
                f = QFont()
                f.setPixelSize(10)
                f.setBold(True)
                label.setFont(f)
                label.setBrush(QBrush(QColor("white")))
                br = label.boundingRect()
                label.setPos(x + ox - br.width() / 2, y + oy - br.height() / 2)
                label.setZValue(11)
                label.setData(0, data)   # clicking the number acts on the bubble too
                label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self._scene.addItem(label)
                lay["items"].append(label)

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
            self._apply_wash()
            self._reposition_markers()
            self._prune_other_zooms()
            cx, cy = lonlat_to_world(self._center[0], self._center[1], self._z)
            self.centerOn(cx, cy)
        finally:
            self._syncing = False
        self._ensure_tiles()

    def _apply_wash(self):
        """A translucent white sheet over the tiles (below the markers) that mutes
        an over-saturated basemap. Recreated per zoom since it covers the scene."""
        if self._wash is not None:
            self._scene.removeItem(self._wash)
            self._wash = None
        w = self._basemap.get("wash", 0.0)
        if w > 0:
            self._wash = self._scene.addRect(
                self._scene.sceneRect(),
                QPen(Qt.PenStyle.NoPen), QBrush(QColor("white")))
            self._wash.setOpacity(w)
            self._wash.setZValue(0)   # above tiles (-1), below markers (10)

    def _center_on_state(self):
        cx, cy = lonlat_to_world(self._center[0], self._center[1], self._z)
        self._syncing = True
        try:
            self.centerOn(cx, cy)
        finally:
            self._syncing = False

    # ── markers ──────────────────────────────────────────────────────────────
    def _reposition_markers(self):
        # The highlight is anchored in old-zoom scene coords; drop it on redraw.
        if self._highlight is not None:
            self._scene.removeItem(self._highlight)
            self._highlight = None
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

    def _tile_cache(self, sub, z, tx, ty):
        return _tiles_dir() / sub / str(z) / str(tx) / f"{ty}.png"

    def _want_tile(self, z, tx, ty):
        sub = self._basemap["sub"]
        key = (sub, z, tx, ty)
        if key in self._tiles or key in self._pending:
            return
        cache = self._tile_cache(sub, z, tx, ty)
        if cache.exists():
            pm = QPixmap(str(cache))
            if not pm.isNull():
                self._place_tile(key, pm)
                return
        self._pending.add(key)
        req = QNetworkRequest(QUrl(self._basemap["url"].format(z=z, x=tx, y=ty)))
        req.setRawHeader(b"User-Agent", _USER_AGENT)
        reply = self._nam.get(req)
        reply.setProperty("tilekey", key)

    def _on_tile_reply(self, reply):
        key = reply.property("tilekey")
        reply.deleteLater()
        if key is None:
            return
        sub, z, tx, ty = key
        self._pending.discard(key)
        if reply.error() != QNetworkReply.NetworkError.NoError:
            logger.debug(f"map tile {sub} {z}/{tx}/{ty} failed: {reply.errorString()}")
            return
        data = reply.readAll()
        pm = QPixmap()
        if not pm.loadFromData(data):
            return
        cache = self._tile_cache(sub, z, tx, ty)
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(bytes(data))
        except OSError:
            pass
        # Only paint if it's still the current provider + zoom (a switch may have
        # happened while this was in flight).
        if sub == self._basemap["sub"] and z == self._z:
            self._place_tile(key, pm)

    def _place_tile(self, key, pm):
        if key in self._tiles:
            return
        _sub, _z, tx, ty = key
        item = self._scene.addPixmap(pm)
        item.setOffset(tx * TILE, ty * TILE)
        item.setZValue(-1)
        self._tiles[key] = item

    def _prune_other_zooms(self):
        cur = (self._basemap["sub"], self._z)
        for key in list(self._tiles):
            if (key[0], key[1]) != cur:
                self._scene.removeItem(self._tiles.pop(key))

    def set_basemap(self, entry: dict):
        """Switch tile provider: drop the old tiles, clamp zoom to the provider's
        max, and refetch. Returns the provider's attribution for the host label."""
        self._basemap = entry
        self._z = min(self._z, entry.get("max", MAX_ZOOM))
        self._refresh_all()
        return entry["attr"]

    # ── interaction ──────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_vp = e.position()
            self._pan_last = e.position()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan_last is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            d = e.position() - self._pan_last
            self._pan_last = e.position()
            c = self.mapToScene(self.viewport().rect().center())
            self.centerOn(c.x() - d.x(), c.y() - d.y())   # → scrollContentsBy syncs
        else:
            # Pointing hand over a clickable bubble, plain arrow elsewhere.
            item = self.itemAt(int(e.position().x()), int(e.position().y()))
            over = item is not None and isinstance(item.data(0), dict)
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if over
                                      else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self._pan_last = None
        if self._press_vp is None or e.button() != Qt.MouseButton.LeftButton:
            return
        moved = (e.position() - self._press_vp).manhattanLength()
        self._press_vp = None
        if moved > 4:
            return   # it was a drag (pan), not a click
        item = self.itemAt(int(e.position().x()), int(e.position().y()))
        data = item.data(0) if item is not None else None
        if isinstance(data, dict):
            # Single-click = "show me these pictures": highlight + let the host
            # populate the filmstrip. (Double-click zooms — see below.)
            self._set_highlight(item.sceneBoundingRect())
            self.bubble_activated.emit(data)

    def mouseDoubleClickEvent(self, e):
        item = self.itemAt(int(e.position().x()), int(e.position().y()))
        data = item.data(0) if item is not None else None
        if isinstance(data, dict) and data.get("bounds"):
            self._zoom_to_bounds(data["bounds"])
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def _set_highlight(self, rect):
        """A transient ring around the clicked bubble (until the next redraw)."""
        if self._highlight is not None:
            self._scene.removeItem(self._highlight)
        pad = 3
        self._highlight = self._scene.addEllipse(
            rect.adjusted(-pad, -pad, pad, pad),
            QPen(QColor("#ffd54f"), 3), QBrush(Qt.BrushStyle.NoBrush))
        self._highlight.setZValue(20)

    def _zoom_to_bounds(self, bounds):
        """Zoom so a clicked cluster's members fill most of the view — and always
        at least one level deeper, so a click never feels stuck."""
        minlat, maxlat, minlon, maxlon = bounds
        self._center = ((minlon + maxlon) / 2.0, (minlat + maxlat) / 2.0)
        vw = max(self.viewport().width(), 100)
        vh = max(self.viewport().height(), 100)
        fit_z = MIN_ZOOM
        for z in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
            x0, y0 = lonlat_to_world(minlon, maxlat, z)
            x1, y1 = lonlat_to_world(maxlon, minlat, z)
            if (x1 - x0) <= vw * 0.7 and (y1 - y0) <= vh * 0.7:
                fit_z = z
                break
        top = min(MAX_ZOOM, self._basemap.get("max", MAX_ZOOM))
        self._z = min(top, max(fit_z, self._z + 1))
        self._refresh_all()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        if self._syncing or self.scene() is None:
            return
        c = self.mapToScene(self.viewport().rect().center())
        self._center = world_to_lonlat(c.x(), c.y(), self._z)
        self._ensure_tiles()

    def wheelEvent(self, e):
        step = 1 if e.angleDelta().y() > 0 else -1
        top = min(MAX_ZOOM, self._basemap.get("max", MAX_ZOOM))
        new_z = max(MIN_ZOOM, min(top, self._z + step))
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
    """Framed window around the map canvas — up to 4 species overlaid, each its
    own colour, with a legend, an "Add species…" picker, Fit, and the required
    OSM attribution. `master_db` supplies both the picker list and each species'
    GPS points."""

    def __init__(self, parent, master_db, initial_sci: str = "",
                 initial_display: str = "", initial_points: list[dict] | None = None):
        super().__init__(parent)
        self._db = master_db
        self._used: dict[str, dict] = {}   # sci.casefold() -> {sci,display,color,lid,n}
        from gui.image_loader import AsyncImageLoader
        self._loader = AsyncImageLoader(self)
        self._species_colls: dict[str, object] = {}   # sci.casefold() -> collection
        self._strip = None                             # current FilmstripWidget
        self._strip_coll = None                        # its collection (for large view)
        self.setWindowTitle("Master species distribution map")
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(1000, 760)

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
        h.addWidget(QLabel("Basemap:"))
        self._basemap_combo = QComboBox()
        for bm in _BASEMAPS:
            self._basemap_combo.addItem(bm["name"], bm)
        self._basemap_combo.setToolTip(
            "Map style — Standard (streets), Terrain (relief/contours), or Atlas")
        self._basemap_combo.currentIndexChanged.connect(self._on_basemap_changed)
        h.addWidget(self._basemap_combo)
        h.addSpacing(8)
        fit_btn = QPushButton("Fit")
        fit_btn.setToolTip("Zoom to fit all points")
        fit_btn.clicked.connect(self._map_fit)
        h.addWidget(fit_btn)
        lay.addWidget(header)

        # Map on top, filmstrip below, in a resizable/collapsible splitter.
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._map = TileMapView(self)
        self._map.bubble_activated.connect(self._on_bubble)
        self._splitter.addWidget(self._map)
        self._strip_holder = QWidget()
        self._strip_box = QVBoxLayout(self._strip_holder)
        self._strip_box.setContentsMargins(0, 0, 0, 0)
        self._strip_hint = QLabel("Click a bubble to see its pictures here.")
        self._strip_hint.setStyleSheet("color:#888; padding:6px;")
        self._strip_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._strip_box.addWidget(self._strip_hint)
        self._splitter.addWidget(self._strip_holder)
        self._splitter.setStretchFactor(0, 1)   # the map takes the extra space
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setSizes([620, 150])
        lay.addWidget(self._splitter, 1)

        # Attribution, pinned bottom-right over the map (usage-policy required);
        # updated when the basemap changes. Starts on the default provider.
        self._attr = QLabel(_BASEMAPS[0]["attr"], self._map)
        self._attr.setStyleSheet(
            "background: rgba(255,255,255,0.75); color:#333; font-size:9px; "
            "padding:1px 5px; border-top-left-radius:3px;")
        self._attr.adjustSize()
        self._map.resized.connect(self._place_attribution)

        if initial_sci and initial_points:
            self._add_layer(initial_sci, initial_display, initial_points)
        QTimer.singleShot(0, self._map.fit_to_points)   # after first layout pass

    # ── layers / legend ────────────────────────────────────────────────────────
    def _map_fit(self):
        self._map.fit_to_points()

    def _on_basemap_changed(self, _idx: int):
        entry = self._basemap_combo.currentData()
        if not entry:
            return
        attr = self._map.set_basemap(entry)
        self._attr.setText(attr)
        self._attr.adjustSize()
        self._place_attribution()

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
        lid = self._map.add_layer(color, points, nl=nl, en=en, sci=sci)
        self._used[key] = {"sci": sci, "display": display, "color": color,
                           "lid": lid, "n": len(points)}
        self._rebuild_legend()

    # ── bubble → filmstrip → large viewer ────────────────────────────────────────
    def _collection_for_species(self, sci: str, display: str):
        """A full-master RatingEditorCollection for one species, built lazily and
        cached. Reused as BOTH the filmstrip's and the large viewer's collection —
        the same object the Master Species Viewer uses, so its master editing
        rules + thumbnail/large-view paths come for free. Editing follows the
        machine's master policy: stars/colour are writable when
        `master.readonly = no` (read-only otherwise), exactly like the Master
        Species Viewer. jpg_only=False so every GPS'd picture (JPG or RAW-only)
        resolves."""
        key = (sci or "").casefold()
        coll = self._species_colls.get(key)
        if coll is None:
            from engine.filepicture import FilePicture
            from engine.filecsv import Files
            from engine.picturecsv import Pictures
            from gui.rating_provider import RatingEditorCollection
            from utils.config import _config
            read_only = _config.get(
                "master", "readonly", fallback="yes").strip().lower() != "no"
            fpr = FilePicture(None)
            fpr.fi = Files(None)
            fpr.upn = Pictures(None)
            coll = RatingEditorCollection(
                fpr, day_objects=None, upn_day_map={}, jpg_only=False,
                species_lang="NL", loader=self._loader, view_read_only=False)
            coll.set_master(self._db, self._db.master_root, include=True,
                            read_only=read_only, full=True)
            coll.rebuild(display or sci, forced_sci=key)
            self._species_colls[key] = coll
        return coll

    def _on_bubble(self, cluster: dict):
        """Single-click on a bubble → fill the filmstrip with its pictures."""
        upns = [u for u in cluster.get("upns", []) if u]
        sci = cluster.get("sci", "")
        display = self._used.get(sci.casefold(), {}).get("display", sci)
        if not upns or not sci:
            return
        coll = self._collection_for_species(sci, display)
        # Keep only UPNs the collection actually resolves (defensive).
        keys = [u for u in upns if self._resolves(coll, u)]
        if not keys:
            return
        from gui.large_image_view import FilmstripWidget
        strip = FilmstripWidget(coll, keys)
        strip.item_clicked.connect(lambda k, c=coll, ks=keys: self._open_large(c, ks, k))
        # Swap it in below the map.
        if self._strip is not None:
            self._strip.deleteLater()
        self._strip_hint.hide()
        self._strip = strip
        self._strip_coll = coll
        self._strip_box.addWidget(strip)

    @staticmethod
    def _resolves(coll, upn: str) -> bool:
        try:
            coll.index_of(upn)
            return True
        except (ValueError, KeyError):
            return False

    def _open_large(self, coll, keys: list[str], key: str):
        """Filmstrip thumbnail → master large viewer over this bubble's pictures.
        Editable per the master policy (stars/colour when master is writable)."""
        from gui.large_image_view import LargeImageView
        if key not in keys:
            return
        dlg = LargeImageView(self, coll, keys, keys.index(key),
                             settings_key="MapView", auto_advance=True)
        # An edit in the large view must show up on the filmstrip cell too.
        dlg.picture_changed.connect(self._refresh_strip_item)
        dlg.show()

    def _refresh_strip_item(self, key: str):
        if self._strip is not None:
            try:
                self._strip.refresh_item(key)
            except Exception:
                pass

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
