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
    QGraphicsSimpleTextItem, QGraphicsPathItem, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QWidget, QMessageBox, QSplitter, QComboBox,
    QMenu, QScrollArea, QApplication, QLineEdit, QListWidget, QListWidgetItem)
from PyQt6.QtGui import (QPixmap, QPainter, QColor, QBrush, QPen, QFont,
                         QPainterPath, QPolygonF, QDrag, QLinearGradient)
from PyQt6.QtCore import Qt, QPointF, QUrl, QTimer, pyqtSignal, QMimeData, QEvent
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
    # OpenTopoMap renders to zoom 17 only in well-mapped regions; over remote areas
    # (Costa Rica rainforest, etc.) its tiles stop earlier and it serves a grey
    # "max zoom / layer = 17" PLACEHOLDER instead of relief. Cap Terrain at 15 — its
    # reliable global level — so a zoom-in never lands on those placeholder tiles.
    {"name": "Terrain", "sub": "opentopo", "max": 15, "wash": 0.35,
     "url": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
     "attr": "© OpenTopoMap (CC-BY-SA) · © OpenStreetMap contributors"},
    {"name": "Standard", "sub": "osm", "max": 19, "wash": 0.0,
     "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
     "attr": "© OpenStreetMap contributors"},
]

# Geotag-preview (no-GPS placement): the time-neighbours are drawn green, the
# suggested spot is coloured by confidence — green when the neighbours cluster
# tightly (a confident 'magnet'), amber when they're moderately apart, red when
# the interpolation spans a big gap (or is a one-sided extrapolation) and really
# needs a human to nudge it.
_NEIGHBOUR_COLOR = "#2e7d32"
# "manual" = a spot the user picked themselves (a drop or a drag-move) — blue, no
# confidence claim; the others grade an ESTIMATE.
_CONF_COLOR = {"high": "#2e7d32", "medium": "#f9a825", "low": "#d32f2f",
               "manual": "#1565c0"}

# A UPN worth offering in the drag-to-geotag tray must have a real IMAGE file.
# Sidecar-only records (orphan .dop/.xmp with no picture — e.g. an early
# blue-delete that removed the RAW/JPG but left the .dop behind) can't be placed
# anywhere meaningful and only clutter the "without GPS" tray.
_IMAGE_EXTS = {"orf", "nef", "cr2", "arw", "rw2", "dng", "jpg", "jpeg",
               "heic", "heif", "png", "tif", "tiff"}

# Travel-path ramp. Days are ORDINAL (ordered), not categorical, so this is ONE hue
# light→dark carrying the trip's PROGRESSION (day 1 light → last day dark) — not 20
# invented hues, which is the categorical anti-pattern.
#
# Validated with the dataviz six-checks (ordinal mode) against a WHITE CASING, not
# against the tiles: over the sea (#aad3df) the light steps fail even the 2:1
# ordinal floor, so every leg is a 2px coloured line drawn on a 4px white casing —
# the standard cartographic road trick, which decouples the palette from whatever
# the basemap is doing. On the casing all ten steps clear 2:1.
#
# HONEST LIMIT: the ramp resolves ~8 distinguishable steps (OKLCH ΔL ≥ 0.06 over
# L 0.338–0.764). A longer trip still reads early→late, but adjacent days are NOT
# separable by colour — exact identity comes from the leg tooltip, never colour alone.
_PATH_RAMP = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
              "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
_PATH_CASING = "#ffffff"

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


def _parse_capture_dt(s: str):
    """EXIF DateTimeOriginal ('YYYY:MM:DD HH:MM:SS', maybe subsec/zone) → datetime,
    or None. Used to time-bracket a no-GPS picture between GPS'd neighbours."""
    import datetime
    s = (s or "").strip()
    if len(s) < 19:
        return None
    try:
        return datetime.datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two (lat, lon) points. Used to
    judge how tightly a no-GPS picture's time-neighbours cluster — a tight cluster
    is a confident 'magnet', a wide gap makes the interpolation a rough guess."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _fmt_dist(m: float) -> str:
    return f"{m:.0f} m" if m < 1000 else f"{m / 1000:.1f} km"


def _fmt_dur(seconds: float) -> str:
    s = abs(int(seconds))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{round(s / 60)} min"
    return f"{s / 3600:.1f} h"


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
    bubble_activated = pyqtSignal(object)       # cluster dict on single-click
    bubble_hide_requested = pyqtSignal(object)  # cluster dict on right-click → Hide
    pictures_dropped = pyqtSignal(list, float, float)  # (upns, lat, lon) drop-to-geotag
    group_move_requested = pyqtSignal(object, float, float)  # (cluster, lat, lon) drag-move
    suggestion_moved = pyqtSignal(float, float)  # (lat, lon) — pending pin nudged

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
        self._geo_preview = None            # no-GPS placement preview (lonlat + colour)
        self._geo_items: list = []          # its scene items (rebuilt per zoom)
        self._geo_pin = None                # the suggested-pin item (draggable)
        self._grab_pin = False              # dragging that pin to nudge the spot
        self._pin_moved = False             # it actually moved (→ worth re-geocoding)
        self._grab_data = None              # bubble grabbed for a drag-move (or None)
        self._grab_press = None             # viewport pos where the grab started
        self._moving = False                # past the threshold → dragging a group
        self._ghost: list = []              # transient drag-ghost items (the moving pin)
        self.allow_move = False             # trip map: drag a bubble to move its pictures
        self.allow_hide = False             # trip map: right-click a bubble to hide it
        self.trim_fit = False               # trip map: Fit ignores far outliers
                                            # (species map keeps exact range framing)
        self.allow_drop = False             # trip map: accept no-GPS pics dragged on
        self.setAcceptDrops(True)
        # Travel path (trip/day map): a time-ordered polyline through the points.
        self._path_pts: list = []           # [(lon, lat, day), …] in time order
        self._path_items: list = []         # polyline + arrowheads at current zoom
        self._show_path = False

        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_tile_reply)
        _tiles_dir().mkdir(parents=True, exist_ok=True)

    # ── layers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _aggregate(points: list[dict]) -> list[dict]:
        """Merge same-spot points (rounded to ~1 m) into one pin, keeping the
        picture count and the member UPNs (so a bubble knows its photos). A
        representative place name and the date range are carried too, for the
        trip/day map's date+location tooltip (harmless for the species map)."""
        agg: dict[tuple, dict] = {}
        for p in points:
            k = (round(p["lat"], 5), round(p["lon"], 5))
            if k not in agg:
                agg[k] = {"lat": p["lat"], "lon": p["lon"],
                          "label": p.get("place", ""), "count": 0, "upns": [],
                          "place": p.get("place", ""), "date_min": "", "date_max": ""}
            a = agg[k]
            a["count"] += 1
            if p.get("upn"):
                a["upns"].append(p["upn"])
            if not a["place"] and p.get("place"):
                a["place"] = p["place"]
            d = (p.get("date") or "").strip()
            if d:
                if not a["date_min"] or d < a["date_min"]:
                    a["date_min"] = d
                if not a["date_max"] or d > a["date_max"]:
                    a["date_max"] = d
        return list(agg.values())

    def add_layer(self, color: str, points: list[dict],
                  nl: str = "", en: str = "", sci: str = "",
                  offset: tuple | None = None, tip=None) -> int:
        """Add a coloured layer; returns its id (for remove_layer). `nl`/`en` are
        the species' Dutch/English names (species pin tooltip); `sci` is carried
        on each bubble so a click can pull that species' pictures. `offset` sets
        the rosette shift (default = keyed to the palette slot, so co-located
        species fan out); pass (0, 0) for a single all-species trip layer. `tip`
        is an optional callable(cluster)->str for the tooltip (the trip/day map
        passes a date+location builder); default is the species tooltip."""
        lid = self._next_layer_id
        self._next_layer_id += 1
        if offset is None:
            try:
                offset = _JITTER[_PALETTE.index(color) % len(_JITTER)]
            except ValueError:
                offset = (0, 0)
        self._layers[lid] = {"color": QColor(color), "nl": nl, "en": en,
                             "sci": sci, "offset": offset, "tip": tip,
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
            dmins = [m["date_min"] for m in members if m.get("date_min")]
            dmaxs = [m["date_max"] for m in members if m.get("date_max")]
            clusters.append({
                "lat": sum(lats) / len(lats), "lon": sum(lons) / len(lons),
                "count": sum(m["count"] for m in members), "n": len(members),
                "bounds": (min(lats), max(lats), min(lons), max(lons)),
                "place": next((m.get("place") for m in members if m.get("place")), ""),
                "date_min": min(dmins) if dmins else "",
                "date_max": max(dmaxs) if dmaxs else "",
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
        tip = lay.get("tip")
        ox, oy = lay.get("offset", (0, 0))
        for c in self._cluster(lay["points"]):
            x, y = lonlat_to_world(c["lon"], c["lat"], self._z)
            total = c["count"]
            # Single-picture dot is small; multi grows gently with the count.
            r = 6 if total <= 1 else min(20, 8 + 3 * (len(str(total)) - 1) + 2)
            # Each marker carries its cluster: species + member UPNs (→ filmstrip
            # / large view) + bounds (→ double-click zoom, only when it hides
            # several DISTINCT spots) + place/date (trip tooltip).
            data = {"sci": sci, "nl": nl, "en": en, "upns": c["upns"],
                    "count": total, "n": c["n"],
                    "place": c.get("place", ""), "date_min": c.get("date_min", ""),
                    "date_max": c.get("date_max", ""),
                    "bounds": c["bounds"] if c["n"] > 1 else None}
            dot = QGraphicsEllipseItem(x - r + ox, y - r + oy, 2 * r, 2 * r)
            dot.setBrush(brush)
            dot.setPen(pen)
            dot.setZValue(10)
            dot.setToolTip(tip(c) if tip else self._pin_tip(total, nl, en))
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
    @staticmethod
    def _weighted_pct(values_weights, q):
        """Weighted percentile: q-fraction of total weight lies at or below the
        returned value. Weight = pictures at the spot, so a photo-dense area holds
        the frame and a handful of transit shots far away don't stretch it."""
        vw = sorted(values_weights)
        total = sum(w for _, w in vw) or 1
        target = total * q
        cum = 0.0
        for v, w in vw:
            cum += w
            if cum >= target:
                return v
        return vw[-1][0]

    def _fit_bounds(self):
        """Bounds to frame on. With enough distinct spots, trim the far ~2% tails
        (weighted by picture count) so an outlier like a departure airport doesn't
        blow the frame out to a continent; the outlier is still on the map, a
        zoom-out away. Few spots → exact min/max (nothing to trust as an outlier)."""
        pts = self.all_points()
        lats = [p["lat"] for p in pts]
        lons = [p["lon"] for p in pts]
        if not self.trim_fit or len(pts) < 10:
            return min(lats), max(lats), min(lons), max(lons)
        latw = [(p["lat"], p.get("count", 1)) for p in pts]
        lonw = [(p["lon"], p.get("count", 1)) for p in pts]
        return (self._weighted_pct(latw, 0.02), self._weighted_pct(latw, 0.98),
                self._weighted_pct(lonw, 0.02), self._weighted_pct(lonw, 0.98))

    def fit_to_points(self):
        """Choose zoom + center so the (core) points are visible (world view when
        the species spans continents; close-in when it's local)."""
        pts = self.all_points()
        if not pts:
            self._z, self._center = 2, (5.0, 30.0)
        elif len(pts) == 1:
            p = pts[0]
            self._z, self._center = 10, (p["lon"], p["lat"])
        else:
            minlat, maxlat, minlon, maxlon = self._fit_bounds()
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
            self._draw_path()
            self._reposition_markers()
            self._draw_geo_preview()
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

    # ── travel path (trip/day map) ───────────────────────────────────────────
    def set_travel_path(self, points: list):
        """Give the map a time-ordered [(lon, lat, day), …] to draw as a route line,
        coloured by day. `day` is a 'YYYY-MM-DD' string."""
        self._path_pts = list(points or [])
        self._draw_path()

    def set_path_visible(self, on: bool):
        self._show_path = bool(on)
        self._draw_path()

    def path_days(self) -> list:
        """The trip's distinct days, in order — the host builds the ramp legend."""
        days = []
        for _lon, _lat, d in self._path_pts:
            if d and (not days or days[-1] != d):
                days.append(d)
        return days

    @staticmethod
    def day_color(i: int, n: int) -> str:
        """Ramp colour for day `i` of `n` — light (trip start) → dark (trip end).
        A single-day map takes a mid step (no progression to show).

        Interpolated between ramp steps rather than snapped to them: snapping made
        two different days land on the SAME hex once n > 10 steps, which reads as a
        bug. They stay perceptually close either way (the ramp only resolves ~8
        steps) — that is why the leg tooltip, not the colour, carries identity."""
        if n <= 1:
            return _PATH_RAMP[len(_PATH_RAMP) // 2]
        pos = (i / (n - 1)) * (len(_PATH_RAMP) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(_PATH_RAMP) - 1)
        t = pos - lo
        c0, c1 = QColor(_PATH_RAMP[lo]), QColor(_PATH_RAMP[hi])
        mix = QColor(round(c0.red() + (c1.red() - c0.red()) * t),
                     round(c0.green() + (c1.green() - c0.green()) * t),
                     round(c0.blue() + (c1.blue() - c0.blue()) * t))
        return mix.name()

    _PATH_MIN_SEP = 6      # px: collapse consecutive same-spot points into one node
    _ARROW_MIN_LEG = 34    # px: only arrow legs long enough to read a direction

    def _draw_path(self):
        """Redraw the travel polyline in the current zoom's world coords (like the
        markers, it lives in scene space so it's rebuilt each zoom). Sits above the
        wash (z 0) and below the markers (z 10).

        Drawn per DAY: each day's run gets its own ramp colour and its own tooltip,
        and each run is PREFIXED with the previous day's last point — so the
        overnight leg (the one that is a relocation, if you moved) is drawn in the
        NEW day's colour and the route stays continuous."""
        for it in self._path_items:
            self._scene.removeItem(it)
        self._path_items = []
        if not self._show_path or len(self._path_pts) < 2:
            return
        days = self.path_days()
        n = len(days)
        counts = {}
        for _lon, _lat, d in self._path_pts:
            counts[d] = counts.get(d, 0) + 1
        color_of = {d: self.day_color(i, n) for i, d in enumerate(days)}

        # Split the time-ordered points into consecutive per-day runs.
        runs: list = []
        for lon, lat, d in self._path_pts:
            xy = lonlat_to_world(lon, lat, self._z)
            if not runs or runs[-1][0] != d:
                runs.append((d, [xy]))
            else:
                runs[-1][1].append(xy)

        # Simplify each run: keep a node only when it's far enough from the last kept
        # one (collapses the dense cluster of shots at one spot into a single node;
        # preserves revisits, which are non-consecutive).
        sep2 = self._PATH_MIN_SEP * self._PATH_MIN_SEP
        prev_last = None
        for d, pts in runs:
            kept = [pts[0]]
            for x, y in pts[1:]:
                px, py = kept[-1]
                if (x - px) ** 2 + (y - py) ** 2 >= sep2:
                    kept.append((x, y))
            line_pts = ([prev_last] if prev_last is not None else []) + kept
            prev_last = kept[-1]
            if len(line_pts) < 2:
                continue                       # a lone point draws no line
            n_pics = counts.get(d, 0)
            tip = f"{d} · {n_pics} picture{'' if n_pics == 1 else 's'}"
            self._draw_day_run(line_pts, color_of[d], tip)

    def _draw_day_run(self, pts: list, color: str, tip: str):
        """One day's polyline: a 2px coloured line on a 4px WHITE CASING, so it
        stays legible over both the beige land and the blue sea (the palette was
        validated against the casing, not against the tiles)."""
        path = QPainterPath()
        path.moveTo(*pts[0])
        for p in pts[1:]:
            path.lineTo(*p)
        for width, col, z in ((4, _PATH_CASING, 5), (2, color, 6)):
            pen = QPen(QColor(col))
            pen.setWidth(width)
            pen.setCosmetic(True)      # constant px regardless of the view transform
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            item = QGraphicsPathItem(path)
            item.setPen(pen)
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            item.setZValue(z)
            item.setToolTip(tip)       # identity is NEVER colour-alone
            self._scene.addItem(item)
            self._path_items.append(item)
        for a, b in zip(pts, pts[1:]):
            self._add_direction_arrow(a, b, QColor(color), tip)

    def _add_direction_arrow(self, a, b, color: QColor, tip: str = ""):
        """A small filled triangle at a leg's midpoint pointing a→b (travel
        direction), in that day's colour with a white edge so it reads on any
        basemap. Drawn only for legs long enough to read; scene units are view
        pixels here, so a fixed size stays constant across zoom."""
        (x0, y0), (x1, y1) = a, b
        dx, dy = x1 - x0, y1 - y0
        leg = math.hypot(dx, dy)
        if leg < self._ARROW_MIN_LEG:
            return
        ux, uy = dx / leg, dy / leg          # unit direction
        px, py = -uy, ux                      # unit perpendicular
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        s = 7                                 # arrow half-length (px)
        tip_pt = QPointF(mx + ux * s, my + uy * s)
        back = (mx - ux * s, my - uy * s)
        left = QPointF(back[0] + px * s * 0.66, back[1] + py * s * 0.66)
        right = QPointF(back[0] - px * s * 0.66, back[1] - py * s * 0.66)
        edge = QPen(QColor(_PATH_CASING))
        edge.setWidth(1)
        edge.setCosmetic(True)
        arrow = self._scene.addPolygon(
            QPolygonF([tip_pt, left, right]), edge, QBrush(color))
        arrow.setZValue(7)
        if tip:
            arrow.setToolTip(tip)
        self._path_items.append(arrow)

    # ── markers ──────────────────────────────────────────────────────────────
    def _reposition_markers(self):
        # The highlight is anchored in old-zoom scene coords; drop it on redraw.
        if self._highlight is not None:
            self._scene.removeItem(self._highlight)
            self._highlight = None
        for lid in self._layers:
            self._draw_layer(lid)

    # ── geotag preview (no-GPS placement) ────────────────────────────────────
    def show_geotag_preview(self, neighbours: list, suggestion, conf: str):
        """Draw a placement preview: `neighbours` [(lat, lon), …] as green rings
        (the GPS'd photos taken just before/after in time) and `suggestion`
        (lat, lon) as a filled pin coloured by `conf` ('high'/'medium'/'low').
        Persists across pan/zoom until cleared, so it stays visible behind the
        confirm dialog and while the user pans to judge the estimate."""
        self._geo_preview = {"neighbours": list(neighbours or []),
                             "suggestion": suggestion,
                             "color": _CONF_COLOR.get(conf, _CONF_COLOR["low"])}
        self._draw_geo_preview()

    def clear_geotag_preview(self):
        self._geo_preview = None
        self._draw_geo_preview()

    # ── drag-move ghost (dragging a placed group to a new spot) ───────────────
    def _start_ghost(self, data: dict):
        """A blue pin (with the group's picture count) that follows the cursor
        while dragging a bubble to a new location. Neutral colour so it doesn't
        read as the green/amber/red confidence of an estimate."""
        self._clear_ghost()
        r = 12
        pen = QPen(QColor("white"), 2)
        pen.setCosmetic(True)
        fill = QColor("#1565c0")
        fill.setAlpha(210)
        dot = self._scene.addEllipse(-r, -r, 2 * r, 2 * r, pen, QBrush(fill))
        dot.setZValue(30)
        self._ghost.append((dot, 0.0, 0.0))
        count = data.get("count", 1)
        if count and count > 1:
            lbl = self._scene.addSimpleText(str(count))
            f = QFont()
            f.setPixelSize(10)
            f.setBold(True)
            lbl.setFont(f)
            lbl.setBrush(QBrush(QColor("white")))
            br = lbl.boundingRect()
            lbl.setZValue(31)
            self._ghost.append((lbl, -br.width() / 2, -br.height() / 2))

    def _move_ghost(self, scene_pt: QPointF):
        for item, dx, dy in self._ghost:
            item.setPos(scene_pt.x() + dx, scene_pt.y() + dy)

    def _clear_ghost(self):
        for item, _dx, _dy in self._ghost:
            self._scene.removeItem(item)
        self._ghost = []

    def _draw_geo_preview(self):
        for it in self._geo_items:
            self._scene.removeItem(it)
        self._geo_items = []
        self._geo_pin = None
        gp = self._geo_preview
        if not gp or not gp.get("suggestion"):
            return
        z = self._z
        slat, slon = gp["suggestion"]
        sx, sy = lonlat_to_world(slon, slat, z)
        # Dashed connectors from each neighbour to the suggested spot.
        link = QPen(QColor(90, 90, 90, 170), 1, Qt.PenStyle.DashLine)
        link.setCosmetic(True)
        for nlat, nlon in gp["neighbours"]:
            nx, ny = lonlat_to_world(nlon, nlat, z)
            ln = self._scene.addLine(nx, ny, sx, sy, link)
            ln.setZValue(21)
            self._geo_items.append(ln)
        # Neighbours: hollow green rings (they are the known GPS anchors).
        npen = QPen(QColor(_NEIGHBOUR_COLOR), 2)
        npen.setCosmetic(True)
        nfill = QColor(_NEIGHBOUR_COLOR)
        nfill.setAlpha(60)
        for nlat, nlon in gp["neighbours"]:
            nx, ny = lonlat_to_world(nlon, nlat, z)
            r = 7
            ring = self._scene.addEllipse(nx - r, ny - r, 2 * r, 2 * r,
                                          npen, QBrush(nfill))
            ring.setZValue(22)
            self._geo_items.append(ring)
        # Suggestion: a filled, white-bordered pin in the confidence colour. This
        # one is DRAGGABLE — grab it to nudge the spot before confirming.
        spen = QPen(QColor("white"), 2)
        spen.setCosmetic(True)
        r = 10
        dot = self._scene.addEllipse(sx - r, sy - r, 2 * r, 2 * r,
                                     spen, QBrush(QColor(gp["color"])))
        dot.setZValue(24)
        dot.setToolTip("Drag me to adjust the spot")
        self._geo_items.append(dot)
        self._geo_pin = dot
        # A small white centre dot so the pin reads as a target, not just a blob.
        c = 2
        core = self._scene.addEllipse(sx - c, sy - c, 2 * c, 2 * c,
                                      QPen(Qt.PenStyle.NoPen), QBrush(QColor("white")))
        core.setZValue(25)
        self._geo_items.append(core)

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
    def _bubble_at(self, x: int, y: int):
        """The topmost BUBBLE (marker carrying a cluster dict) at a viewport point,
        as (item, data) — or (None, None).

        Walks the z-ordered stack instead of using itemAt(): decorations sit ABOVE
        the markers (the selection highlight ring at z=20, the drag ghost, the
        geotag preview) and Qt's shape() for an ellipse covers its INTERIOR even
        with no fill — so itemAt() would return the ring and hide the bubble
        underneath it, silently killing grab/select/right-click on a selected
        bubble."""
        for item in self.items(x, y):
            data = item.data(0)
            if isinstance(data, dict):
                return item, data
        return None, None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_vp = e.position()
            self._pan_last = e.position()
            # The pending-placement pin wins over everything: it sits on top and
            # exists only while a confirm is open, so grabbing it means "nudge".
            if self._geo_pin is not None and self._geo_pin in self.items(
                    int(e.position().x()), int(e.position().y())):
                self._grab_pin = True
                self._pin_moved = False
            # Pressing on a bubble arms a possible drag-move (trip map only): a
            # short move past the threshold grabs the group, a plain click still
            # selects it. Empty-map drags keep panning.
            elif self.allow_move:
                _item, data = self._bubble_at(int(e.position().x()),
                                              int(e.position().y()))
                if data is not None and data.get("upns"):
                    self._grab_data = data
                    self._grab_press = e.position()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (self._grab_pin and self._geo_preview
                and (e.buttons() & Qt.MouseButton.LeftButton)):
            sp = self.mapToScene(int(e.position().x()), int(e.position().y()))
            lon, lat = world_to_lonlat(sp.x(), sp.y(), self._z)
            self._geo_preview["suggestion"] = (lat, lon)
            self._pin_moved = True
            self._draw_geo_preview()          # follows the cursor, keeps the rings
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return   # nudging the pin — don't pan
        if self._grab_data is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            if not self._moving:
                if (e.position() - self._grab_press).manhattanLength() > 4:
                    self._moving = True
                    # The ghost is cosmetic — never let it break the actual move.
                    try:
                        self._start_ghost(self._grab_data)
                    except Exception as ex:
                        logger.warning("map: drag ghost failed (move still on): %s",
                                       ex)
            if self._moving:
                try:
                    self._move_ghost(self.mapToScene(int(e.position().x()),
                                                     int(e.position().y())))
                except Exception:
                    pass
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return   # a bubble is grabbed — never pan from it (click still selects)
        if self._pan_last is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            d = e.position() - self._pan_last
            self._pan_last = e.position()
            c = self.mapToScene(self.viewport().rect().center())
            self.centerOn(c.x() - d.x(), c.y() - d.y())   # → scrollContentsBy syncs
        else:
            # Pointing hand over a clickable bubble, plain arrow elsewhere.
            _item, data = self._bubble_at(int(e.position().x()),
                                          int(e.position().y()))
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor
                                      if data is not None
                                      else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self._pan_last = None
        if e.button() == Qt.MouseButton.LeftButton and self._grab_pin:
            self._grab_pin = False
            self._press_vp = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            if self._geo_preview and self._pin_moved:
                lat, lon = self._geo_preview["suggestion"]
                self.suggestion_moved.emit(lat, lon)   # host re-geocodes + updates bar
            self._pin_moved = False
            return
        if e.button() == Qt.MouseButton.LeftButton and self._moving:
            sp = self.mapToScene(int(e.position().x()), int(e.position().y()))
            lon, lat = world_to_lonlat(sp.x(), sp.y(), self._z)
            data = self._grab_data
            self._clear_ghost()
            self._grab_data = self._grab_press = None
            self._moving = False
            self._press_vp = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self.group_move_requested.emit(data, lat, lon)
            return
        self._grab_data = self._grab_press = None
        if self._press_vp is None or e.button() != Qt.MouseButton.LeftButton:
            return
        moved = (e.position() - self._press_vp).manhattanLength()
        self._press_vp = None
        if moved > 4:
            return   # it was a drag (pan), not a click
        item, data = self._bubble_at(int(e.position().x()), int(e.position().y()))
        if data is not None:
            # Single-click = "show me these pictures": highlight + let the host
            # populate the filmstrip. (Double-click zooms — see below.)
            self._set_highlight(item.sceneBoundingRect())
            self.bubble_activated.emit(data)

    def mouseDoubleClickEvent(self, e):
        _item, data = self._bubble_at(int(e.position().x()), int(e.position().y()))
        if data is not None and data.get("bounds"):
            self._zoom_to_bounds(data["bounds"])
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        """Right-click a bubble → offer to hide it (trip map only). Lets the user
        drop a stopover / home / airport cluster that would otherwise stretch the
        frame; the host removes those pictures and its route leg. Left untouched on
        the species map (allow_hide stays False there)."""
        if not self.allow_hide:
            return super().contextMenuEvent(e)
        _item, data = self._bubble_at(e.pos().x(), e.pos().y())
        if data is None:
            return super().contextMenuEvent(e)
        n = data.get("count", 0)
        menu = QMenu(self)
        act = menu.addAction(f"Hide {n} picture{'' if n == 1 else 's'} from map")
        if menu.exec(e.globalPos()) == act:
            self.bubble_hide_requested.emit(data)
        e.accept()

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

    def visible_bounds(self):
        """(minlon, minlat, maxlon, maxlat) of what is on screen, or None. Used to
        bias a place-name search toward the region you are already looking at."""
        try:
            r = self.mapToScene(self.viewport().rect()).boundingRect()
            lon1, lat1 = world_to_lonlat(r.left(),  r.top(),    self._z)
            lon2, lat2 = world_to_lonlat(r.right(), r.bottom(), self._z)
        except (OverflowError, ValueError, ZeroDivisionError):
            return None      # world_to_lonlat can overflow at extreme scene coords
        return (min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2))

    def focus_area(self, coords: list, max_z: int = 13, min_span: float = 0.08):
        """Center + zoom to frame `coords` [(lat, lon), …] at a sensible level
        (capped at `max_z` so a single point lands at ~village zoom, not street).
        Used to jump to the GPS neighbours around a no-GPS picture's capture time.

        `min_span` is the smallest area (in degrees) a single point is padded out
        to. The default keeps a time-bracket legible (~9 km). A place SEARCH passes
        a much smaller span with a higher max_z: you looked up a named hotel, so you
        want its street, not its province."""
        if not coords:
            return
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        cy, cx = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
        # Pad so a single (or tight) bracket becomes a legible area.
        dlat = max(max(lats) - min(lats), min_span)
        dlon = max(max(lons) - min(lons), min_span)
        minlat, maxlat = cy - dlat / 2, cy + dlat / 2
        minlon, maxlon = cx - dlon / 2, cx + dlon / 2
        self._center = (cx, cy)
        vw = max(self.viewport().width(), 100)
        vh = max(self.viewport().height(), 100)
        fit_z = MIN_ZOOM
        for z in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
            x0, y0 = lonlat_to_world(minlon, maxlat, z)
            x1, y1 = lonlat_to_world(maxlon, minlat, z)
            if (x1 - x0) <= vw * 0.8 and (y1 - y0) <= vh * 0.8:
                fit_z = z
                break
        self._z = min(max_z, self._basemap.get("max", MAX_ZOOM), fit_z)
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

    # ── drop-to-geotag (trip map: drag no-GPS pictures onto the map) ─────────────
    def dragEnterEvent(self, e):
        if self.allow_drop and e.mimeData().hasFormat(_MIME_UPNS):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self.allow_drop and e.mimeData().hasFormat(_MIME_UPNS):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if not (self.allow_drop and e.mimeData().hasFormat(_MIME_UPNS)):
            return super().dropEvent(e)
        raw = bytes(e.mimeData().data(_MIME_UPNS)).decode("utf-8")
        keys = [k for k in raw.split("\n") if k]
        sp = self.mapToScene(e.position().toPoint())
        lon, lat = world_to_lonlat(sp.x(), sp.y(), self._z)
        e.acceptProposedAction()
        if keys:
            self.pictures_dropped.emit(keys, lat, lon)


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


# Single all-species trip layer colour (teal). Not in _PALETTE, so it never
# collides with the species overlay palette; the trip map passes offset (0, 0)
# so its markers sit dead-centre (no rosette — there's only one layer).
_TRIP_COLOR = "#00838f"
# When the collection separates own-trip pictures from master reference pictures
# (Trip Species Viewer), the trip's own points are drawn in this pink — well clear
# of the teal master bubbles — so trip vs master reads at a glance.
_TRIP_OWN_COLOR = "#d81b60"

# Drag payload for the no-GPS tray → map drop (newline-joined UPN keys).
_MIME_UPNS = "application/x-speciesviewer-upns"


class PlaceSearchDialog(QDialog):
    """Look a place up BY NAME and get its coordinate — forward geocoding.

    The counterpart to "Suggest a position": that one guesses from the photos taken
    around it in time, this one is for when the camera recorded nothing but YOU
    know where you were ("Jetwing St. Andrews, Nuwara Eliya"). The trip's own map
    area is passed as a soft preference, so the same hotel name in another country
    doesn't win over the one you actually stayed in."""

    def __init__(self, parent, *, viewbox=None, initial: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Find a place by name")
        self.resize(620, 380)
        self._viewbox = viewbox
        self._results: list = []
        self.chosen: dict | None = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Type the name of the place — a hotel, a reserve, a village, a lake.\n"
            "Pick a result to drop a pin there; you can still drag the pin to the "
            "exact spot before placing."))

        row = QHBoxLayout()
        self._field = QLineEdit(initial)
        self._field.setPlaceholderText("e.g. Jetwing St. Andrews, Nuwara Eliya")
        self._field.returnPressed.connect(self._search)
        row.addWidget(self._field)
        self._search_btn = QPushButton("Search")
        self._search_btn.setDefault(True)
        self._search_btn.clicked.connect(self._search)
        row.addWidget(self._search_btn)
        lay.addLayout(row)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _i: self._accept())
        lay.addWidget(self._list, 1)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#666;")
        lay.addWidget(self._status)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self._ok = QPushButton("Show on the map")
        self._ok.setEnabled(False)
        self._ok.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(self._ok)
        lay.addLayout(btns)

        self._list.itemSelectionChanged.connect(
            lambda: self._ok.setEnabled(self._list.currentRow() >= 0))

    def _search(self):
        from gui.geolocate import search_places
        q = self._field.text().strip()
        if not q:
            return
        self._status.setText("Searching…")
        self._list.clear()
        self._ok.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._results = search_places(q, viewbox=self._viewbox)
        finally:
            QApplication.restoreOverrideCursor()
        if not self._results:
            self._status.setText(
                "Nothing found. Try a simpler name, or add the town or country — "
                "OpenStreetMap may not know a small lodge by its full name.")
            return
        for r in self._results:
            kind = f"[{r['kind']}] " if r["kind"] else ""
            self._list.addItem(QListWidgetItem(f"{kind}{r['name']}"))
        self._list.setCurrentRow(0)
        self._status.setText(f"{len(self._results)} result(s) — best match first.")

    def _accept(self):
        row = self._list.currentRow()
        if 0 <= row < len(self._results):
            self.chosen = self._results[row]
            self.accept()


class NoGpsTray(QWidget):
    """Collapsible top strip of the pictures WITHOUT GPS on a trip/day map — the
    drag source for Phase 5b geotagging. Drag one or more thumbnails onto the map
    to give them coordinates + a place. Default-collapsed to a thin bar (laptop
    space is tight); the count line doubles as the expand toggle. Reuses the
    filmstrip's `_StripItem` cell for the thumbnail + selection look."""

    emptied = pyqtSignal()             # last picture placed → host hides the tray
    context_requested = pyqtSignal(str, object)  # (key, global QPoint) on right-click

    def __init__(self, collection, keys, parent=None):
        super().__init__(parent)
        self._coll = collection
        self._keys = list(keys)
        self._selected: set = set()
        self._cells: dict = {}
        self._obj_to_key: dict = {}
        self._press = None
        self._press_key = None
        self._current_key = None       # last-clicked cell (bracket reference)
        self._range_start = None       # '[' anchor for range select
        from gui.large_image_view import _StripItem, _ITEM_W, _ITEM_H
        self._cell_w, self._cell_h = _ITEM_W, _ITEM_H

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self._toggle = QPushButton()
        self._toggle.setCheckable(True)
        self._toggle.setStyleSheet(
            "QPushButton{border:none; text-align:left; padding:4px 8px; color:#333;}"
            "QPushButton:hover{color:#000;}")
        self._toggle.toggled.connect(self._on_toggle)
        v.addWidget(self._toggle)

        self._body = QScrollArea()
        self._body.setWidgetResizable(False)
        self._body.setFixedHeight(_ITEM_H + 16)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._strip = QWidget()
        self._hl = QHBoxLayout(self._strip)
        self._hl.setContentsMargins(4, 2, 4, 2)
        self._hl.setSpacing(6)
        self._hl.addStretch()
        for k in self._keys:
            try:
                item = collection.item_for_key(k)
            except (ValueError, KeyError):
                continue
            cell = _StripItem(item, self._strip)
            cell.request_load()
            cell.setToolTip(self._tip(item, self._fname(collection, k)))
            cell.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # receive [ ] keys
            cell.installEventFilter(self)
            self._hl.insertWidget(self._hl.count() - 1, cell)
            self._cells[k] = cell
            self._obj_to_key[cell] = k
        self._resize_strip()
        self._body.setWidget(self._strip)
        v.addWidget(self._body)
        self._body.setVisible(False)
        self._refresh_header()

    @staticmethod
    def _fname(collection, key) -> str:
        """The picture's actual file name (e.g. IMG_3728.PNG) — the surest ID for a
        black screenshot whose thumbnail can't render."""
        rp = getattr(collection, "rep_filename", None)
        try:
            return rp(key) if rp else ""
        except Exception:
            return ""

    @staticmethod
    def _tip(item, fname: str = "") -> str:
        """Hover tooltip so a black/render-less thumbnail is still identifiable."""
        taken = (getattr(item, "date", "") or "").strip() or "—"
        exts = ", ".join(getattr(item, "file_exts", []) or []) or "—"
        name = getattr(item, "display_name", "") or getattr(item, "key", "")
        lines = [name]
        if fname and fname != name:
            lines.append(f"File: {fname}")
        lines += [f"Taken: {taken}", f"Files: {exts}",
                  "(drag onto the map; right-click for details)"]
        return "\n".join(lines)

    # ── layout / header ──────────────────────────────────────────────────────
    def _resize_strip(self):
        n = len(self._cells)
        self._strip.setFixedSize(max(1, n * (self._cell_w + 6) + 12), self._cell_h + 4)

    def _refresh_header(self):
        n = len(self._cells)
        arrow = "▾" if self._body.isVisible() else "▸"
        self._toggle.setText(
            f"{arrow}  📍 {n} without GPS — drag onto the map to place them")

    def _on_toggle(self, on: bool):
        self._body.setVisible(on)
        self._refresh_header()

    # ── selection ────────────────────────────────────────────────────────────
    def _select(self, key: str, additive: bool):
        if additive:
            self._selected ^= {key}
        elif key not in self._selected:
            self._selected = {key}
        self._repaint_selection()

    def _select_range(self, start: str, end: str):
        """Select the contiguous run start..end (the '[' … ']' gesture)."""
        order = [k for k in self._keys if k in self._cells]   # current visible order
        if start not in order or end not in order:
            if end in order:                                  # no anchor → just 'end'
                self._select(end, additive=False)
            return
        i, j = sorted((order.index(start), order.index(end)))
        self._selected = set(order[i:j + 1])
        self._current_key = end
        self._repaint_selection()

    def _repaint_selection(self):
        for k, cell in self._cells.items():
            cell.set_selected(k in self._selected)

    # ── drag ─────────────────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.ContextMenu:
            key = self._obj_to_key.get(obj)
            if key is not None:
                self.context_requested.emit(key, ev.globalPos())
                return True
        if t == QEvent.Type.KeyPress:
            key = self._obj_to_key.get(obj) or self._current_key
            if ev.key() == Qt.Key.Key_BracketLeft:
                self._range_start = key                    # '[' = start of a range
                return True
            if ev.key() == Qt.Key.Key_BracketRight:
                self._select_range(self._range_start, key)  # ']' = end → select A..B
                return True
        if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            key = self._obj_to_key.get(obj)
            if key is not None:
                self._press = ev.position().toPoint()
                self._press_key = key
                self._current_key = key
                additive = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self._select(key, additive)
        elif t == QEvent.Type.MouseMove and self._press is not None \
                and (ev.buttons() & Qt.MouseButton.LeftButton):
            if (ev.position().toPoint() - self._press).manhattanLength() >= 6:
                self._start_drag()
                self._press = None
        elif t == QEvent.Type.MouseButtonRelease:
            self._press = None
        return super().eventFilter(obj, ev)

    def _start_drag(self):
        keys = [k for k in self._keys if k in self._selected] or [self._press_key]
        keys = [k for k in keys if k in self._cells]
        if not keys:
            return
        md = QMimeData()
        md.setData(_MIME_UPNS, ("\n".join(keys)).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(md)
        lead = self._cells.get(keys[0])
        if lead is not None:
            pm = lead.grab()
            drag.setPixmap(pm)
            drag.setHotSpot(pm.rect().center())
        drag.exec(Qt.DropAction.CopyAction)

    # ── host API ─────────────────────────────────────────────────────────────
    def selected_keys(self) -> list:
        """The currently selected tray pictures, in tray order (the same set a drag
        would carry). Lets a context-menu action apply to the whole selection."""
        return [k for k in self._keys if k in self._selected]

    def remove_keys(self, keys):
        """Drop placed pictures out of the tray (after a successful geotag)."""
        for k in keys:
            cell = self._cells.pop(k, None)
            if cell is not None:
                self._obj_to_key.pop(cell, None)
                self._hl.removeWidget(cell)
                cell.deleteLater()
            self._selected.discard(k)
        self._resize_strip()
        self._refresh_header()
        if not self._cells:
            self.emptied.emit()


class TripMapView(QDialog):
    """Trip / day map (maps_plan.md Phase 5a) — ALL species on the current viewer
    selection, plotted over the host's LIVE `PictureCollection`. Shows where the
    pictures were taken, clusters them spatially, and draws the travel path (how
    you moved), with the same bubble → filmstrip → large-view drill-down as the
    species map. Read-only for now; drag no-GPS pictures onto the map to geotag is
    Phase 5b.

    Unlike `MapView` (master + per-species, builds its own collection), this reads
    the collection it's handed and points the filmstrip / large view straight at
    it — no per-species rebuild. (The two dialogs share enough that a common base
    is a fair later refactor; kept separate now to leave the shipped species map
    untouched.)"""

    # Pictures whose GPS/location we just wrote — the host viewer refreshes their
    # cards and re-applies its filter, so one just placed leaves the 📍 subset.
    pictures_geotagged = pyqtSignal(list)

    def __init__(self, parent, collection, keys: list[str],
                 title: str = "Trip map", loader=None,
                 default_path: bool = False, default_basemap: str = "Standard",
                 trip_root: str = "", read_only: bool = False,
                 read_only_note: str = "", allow_geotag: bool = False):
        super().__init__(parent)
        self._coll = collection
        # CAPTURE ORDER, not the order the keys arrived in. The host hands us
        # all_keys(), which is the UPN dict's insertion order — i.e. whatever order
        # the rows happened to land in the CSV — while the grid the user is looking
        # at renders the collection's `_order`, which is sorted by capture time. The
        # tray of not-yet-placed pictures sat beside that grid in a different order,
        # and so did every bubble's filmstrip. Sorting here fixes both at once: the
        # tray, the filmstrip, and the neighbours a position suggestion reads.
        self._src_keys = self._by_capture(keys or [])
        self._scope = "all"                 # "all" | "species" (header buttons)
        self._keys = list(self._src_keys)   # the scoped subset actually mapped
        self._strip = None
        # Read-only: LOOK, don't touch. Used by the virtual-trip map, whose files
        # are the MASTER's shared originals — moving a bubble or deleting would
        # write into master sidecars. Same call as the Album Rating Editor.
        # Everything that writes is switched off here rather than merely hidden
        # (a tray you can't drag from is a misleading affordance).
        self._read_only = bool(read_only)
        # GEOTAG mode: read-only EXCEPT for placing pictures that have no GPS —
        # the deliberate carve-out that lets this map clean up the master's missing
        # coordinates (PreviewCollection.allow_geotag is the gate that actually
        # permits the write). The no-GPS tray and drag-to-geotag come back; bubble
        # MOVE stays off, because moving OVERWRITES coordinates the camera
        # recorded, and cleanup means filling gaps, never overwriting.
        self._geotag = bool(allow_geotag) and getattr(
            collection, "allow_geotag", False)
        self._can_place = (not self._read_only) or self._geotag
        # Set → the no-GPS tray offers Delete (move to <trip_root>/.trash/<day>);
        # needs a collection that can trash a UPN + the per-day review CSVs here.
        from pathlib import Path as _Path
        self._trip_root = _Path(trip_root) if trip_root else None
        self._can_delete = (not self._read_only
                            and bool(self._trip_root)
                            and hasattr(collection, "trash_upn"))
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags()
                            | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(1000, 760)

        self._ghosts = 0                   # index rows whose files are gone
        points, nogps_keys = self._build_points()
        self._all_points = points          # full set (before any hide)
        self._nogps_keys = nogps_keys      # pictures without GPS (drag-to-geotag)
        self._hidden: set = set()          # UPNs hidden via right-click
        self._placed: set = set()          # UPNs we (re)positioned by hand this session
        self._lids: list[int] = []         # trip layer ids (1, or 2 when split by trip/master)
        self._tray = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QWidget()
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 4, 8, 4)
        self._info = QLabel()
        self._info.setStyleSheet("color:#555;")
        h.addWidget(self._info)
        if self._geotag:
            # Never say "view only" on a map that writes. Placing a picture here
            # edits the MASTER's sidecar, and the header says so plainly.
            note = QLabel("· placing writes to the master")
            note.setStyleSheet("color:#b26500; font-weight:bold;")
            note.setToolTip(
                "These pictures live in the master database. Dragging one from the "
                "tray onto the map writes its GPS + place into the master's own "
                "sidecar — that is the point here: it fixes the master.\n\n"
                "Only pictures WITHOUT GPS can be placed. Nothing else is editable: "
                "you cannot move a picture that already has coordinates, nor change "
                "a rating, colour or file.")
            h.addSpacing(6)
            h.addWidget(note)
        elif self._read_only:
            note = QLabel("· view only")
            note.setStyleSheet("color:#8a6d3b;")
            note.setToolTip(read_only_note or
                            "This map shows the pictures but does not change them.")
            h.addSpacing(6)
            h.addWidget(note)
        self._showall_btn = QPushButton("Show all")
        self._showall_btn.setToolTip("Bring back the pictures you hid from the map")
        self._showall_btn.clicked.connect(self._show_all_hidden)
        self._showall_btn.setVisible(False)
        h.addSpacing(8)
        h.addWidget(self._showall_btn)
        h.addStretch()
        # Scope: the whole handed-over set, or only pictures carrying a species
        # (the real observations, without landscape/context shots). Mirrors the
        # viewer's All / Species buttons; All by default.
        self._all_btn = QPushButton("All")
        self._all_btn.setCheckable(True)
        self._all_btn.setChecked(True)
        self._all_btn.setToolTip("Map every picture in this view")
        self._all_btn.clicked.connect(lambda: self._set_scope("all"))
        self._species_btn = QPushButton("Species")
        self._species_btn.setCheckable(True)
        self._species_btn.setToolTip("Map only the pictures that carry a species")
        self._species_btn.clicked.connect(lambda: self._set_scope("species"))
        h.addWidget(self._all_btn)
        h.addWidget(self._species_btn)
        h.addSpacing(8)
        self._path_chk = QCheckBox("Travel path")
        self._path_chk.setToolTip("Draw a line through the pictures in time order "
                                  "(most useful across a whole trip)")
        self._path_chk.setChecked(default_path)
        self._path_chk.toggled.connect(self._map_set_path)
        h.addWidget(self._path_chk)
        # Ramp legend — the path is coloured by DAY (light = trip start → dark =
        # end). A legend is mandatory for ≥2 series; colour carries progression
        # only, so exact dates come from the leg tooltip.
        self._legend = QWidget()
        lg = QHBoxLayout(self._legend)
        lg.setContentsMargins(6, 0, 0, 0)
        lg.setSpacing(4)
        self._legend_from = QLabel()
        self._legend_bar = QLabel()
        self._legend_to = QLabel()
        for w in (self._legend_from, self._legend_to):
            w.setStyleSheet("color:#52514e; font-size:9px;")   # muted ink, not series colour
        lg.addWidget(self._legend_from)
        lg.addWidget(self._legend_bar)
        lg.addWidget(self._legend_to)
        self._legend.setVisible(False)
        h.addWidget(self._legend)
        h.addSpacing(8)
        h.addWidget(QLabel("Basemap:"))
        self._basemap_combo = QComboBox()
        for bm in _BASEMAPS:
            self._basemap_combo.addItem(bm["name"], bm)
        self._basemap_combo.currentIndexChanged.connect(self._on_basemap_changed)
        h.addWidget(self._basemap_combo)
        h.addSpacing(8)
        fit_btn = QPushButton("Fit")
        fit_btn.setToolTip("Zoom to fit all points")
        fit_btn.clicked.connect(lambda: self._map.fit_to_points())
        h.addWidget(fit_btn)
        lay.addWidget(header)

        # Top tray of the no-GPS pictures (drag onto the map to geotag — Phase 5b).
        # Collapsible + only present when there are any; laptop vertical space.
        self._lay = lay                    # kept: the tray is rebuilt on scope change
        self._tray_index = lay.count()     # tray slot = right after the header
        if self._nogps_keys and self._can_place:
            self._tray = NoGpsTray(collection, self._nogps_keys)
            self._tray.emptied.connect(self._on_tray_emptied)
            self._tray.context_requested.connect(self._tray_context)
            lay.addWidget(self._tray)

        # Inline confirm bar for a pending placement/move. Deliberately NOT a modal
        # dialog: a QMessageBox lands on top of the very spot you're judging, and it
        # would also block dragging the pin to adjust it.
        self._pending = None               # {mode, keys, lat, lon, fields, place}
        self._bar = QWidget()
        self._bar.setStyleSheet("background:#fff8e1; border-top:1px solid #e0c97f;")
        b = QHBoxLayout(self._bar)
        b.setContentsMargins(8, 5, 8, 5)
        self._bar_label = QLabel()
        self._bar_label.setWordWrap(True)
        self._bar_label.setStyleSheet("color:#333; border:none;")
        b.addWidget(self._bar_label, 1)
        self._bar_ok = QPushButton("Place here")
        self._bar_ok.setDefault(True)
        self._bar_ok.clicked.connect(self._confirm_accept)
        b.addWidget(self._bar_ok)
        bar_cancel = QPushButton("Cancel")
        bar_cancel.clicked.connect(self._confirm_cancel)
        b.addWidget(bar_cancel)
        self._bar.setVisible(False)
        lay.addWidget(self._bar)

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._map = TileMapView(self)
        self._map.trim_fit = True          # Fit frames the dense core, not outliers
        self._map.allow_hide = True        # right-click a bubble → hide it (view-only)
        self._map.allow_drop = self._can_place       # accept no-GPS pics dragged on
        # Bubble-move OVERWRITES the GPS of every picture in the bubble. In geotag
        # mode the gesture is permitted, but _move_group then refuses any bubble
        # holding a coordinate the CAMERA recorded — so you can refine a coordinate
        # WE derived (that is the whole point of seeding from a place name) and
        # still never overwrite evidence. "Fill, never overwrite" now reads more
        # precisely: never overwrite what we did not put there ourselves.
        self._map.allow_move = self._can_place
        self._map.bubble_activated.connect(self._on_bubble)
        self._map.bubble_hide_requested.connect(self._on_hide)
        self._map.pictures_dropped.connect(self._on_drop)
        self._map.group_move_requested.connect(self._on_move)
        self._map.suggestion_moved.connect(self._on_pin_nudged)
        self._splitter.addWidget(self._map)
        self._strip_holder = QWidget()
        self._strip_box = QVBoxLayout(self._strip_holder)
        self._strip_box.setContentsMargins(0, 0, 0, 0)
        self._strip_hint = QLabel("Click a bubble to see its pictures here.")
        self._strip_hint.setStyleSheet("color:#888; padding:6px;")
        self._strip_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._strip_box.addWidget(self._strip_hint)
        self._splitter.addWidget(self._strip_holder)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setSizes([620, 150])
        lay.addWidget(self._splitter, 1)

        self._attr = QLabel(_BASEMAPS[0]["attr"], self._map)
        self._attr.setStyleSheet(
            "background: rgba(255,255,255,0.75); color:#333; font-size:9px; "
            "padding:1px 5px; border-top-left-radius:3px;")
        self._attr.adjustSize()
        self._map.resized.connect(self._place_attribution)

        # Default basemap (Standard reads nicer for day/trip photos than the vivid
        # terrain relief); switching triggers _on_basemap_changed on the live map.
        idx = next((i for i, bm in enumerate(_BASEMAPS)
                    if bm["name"] == default_basemap), 0)
        if idx != self._basemap_combo.currentIndex():
            self._basemap_combo.setCurrentIndex(idx)
        else:
            self._on_basemap_changed(idx)

        # One all-species layer + the time-ordered travel path.
        self._render(fit=False)
        self._map.set_path_visible(default_path)
        QTimer.singleShot(0, self._map.fit_to_points)

    def refresh_points(self):
        """Re-read the collection and redraw. For when GPS changed OUTSIDE the map —
        the grid's "Get GPS from location" seeds coordinates while the map is open,
        and without this the new pictures only appear if you close and re-open it."""
        self._end_confirm()          # a pending placement refers to the old point set
        self._all_points, self._nogps_keys = self._build_points()
        self._rebuild_tray()
        self._render(fit=False)      # keep the frame the user is looking at

    # ── scope (All / Species) ────────────────────────────────────────────────
    def _set_scope(self, name: str):
        """Switch the mapped subset. 'species' keeps only pictures that carry a
        species (the real observations — drops landscape/context shots); 'all' is
        everything the viewer handed over. Rebuilds points AND the no-GPS tray."""
        self._all_btn.setChecked(name == "all")
        self._species_btn.setChecked(name == "species")
        if name == self._scope:
            return
        self._scope = name
        self._end_confirm()            # a pending placement refers to the old set
        if name == "species" and hasattr(self._coll, "species_keys"):
            allowed = set(self._coll.species_keys())
            self._keys = [k for k in self._src_keys if k in allowed]
        else:
            self._keys = list(self._src_keys)
        self._all_points, self._nogps_keys = self._build_points()
        self._rebuild_tray()
        self._render(fit=False)        # keep the frame the user is looking at

    def _rebuild_tray(self):
        if self._tray is not None:
            self._lay.removeWidget(self._tray)
            self._tray.deleteLater()
            self._tray = None
        if self._nogps_keys and self._can_place:
            self._tray = NoGpsTray(self._coll, self._nogps_keys)
            self._tray.emptied.connect(self._on_tray_emptied)
            self._tray.context_requested.connect(self._tray_context)
            self._lay.insertWidget(self._tray_index, self._tray)

    # ── render / hide ────────────────────────────────────────────────────────
    def _render(self, fit: bool = True):
        """(Re)draw the single trip layer + travel path from the non-hidden points,
        and refresh the header counts. Called on init and after a hide / show-all."""
        pts = [p for p in self._all_points if p["upn"] not in self._hidden]
        for lid in self._lids:
            self._map.remove_layer(lid)
        self._lids = []
        # When the collection tells trip pictures from master reference ones
        # (Trip Species Viewer), draw the trip's own points green and the master
        # ones teal — two layers, since a bubble's colour is per-layer. The trip
        # layer is added SECOND (drawn on top) with a small rosette shift so a
        # spot with both stays legible. Collections without is_master (the culling
        # map) keep the single teal layer exactly as before.
        is_master = getattr(self._coll, "is_master", None)
        if callable(is_master):
            master_pts = [p for p in pts if is_master(p["upn"])]
            trip_pts = [p for p in pts if not is_master(p["upn"])]
            if master_pts:
                self._lids.append(self._map.add_layer(
                    _TRIP_COLOR, master_pts, offset=(0, 0), tip=self._trip_tip))
            self._lids.append(self._map.add_layer(
                _TRIP_OWN_COLOR, trip_pts,
                offset=((7, -7) if master_pts else (0, 0)), tip=self._trip_tip))
        else:
            self._lids.append(self._map.add_layer(
                _TRIP_COLOR, pts, offset=(0, 0), tip=self._trip_tip))
        ordered = sorted((p for p in pts if p.get("date")), key=lambda p: p["date"])
        # EXIF dates are 'YYYY:MM:DD HH:MM:SS' → the day key is the first 10 chars.
        self._map.set_travel_path(
            [(p["lon"], p["lat"], p["date"][:10].replace(":", "-")) for p in ordered])
        self._update_path_legend()
        n = len(pts)
        parts = [f"{n} picture{'' if n == 1 else 's'} on the map"]
        if self._nogps_keys:
            parts.append(f"{len(self._nogps_keys)} without GPS")
        if self._hidden:
            parts.append(f"{len(self._hidden)} hidden")
        if self._ghosts:
            parts.append(f"{self._ghosts} missing")
        if callable(is_master):
            trip_n = sum(1 for p in pts if not is_master(p["upn"]))
            if 0 < trip_n < len(pts):     # both trip and master on the map
                # Colour the word "pink" pink so the legend explains itself.
                parts.append(f'<span style="color:{_TRIP_OWN_COLOR}; '
                             f'font-weight:600">pink</span> = this trip')
        self._info.setText("  ·  ".join(parts))
        self._info.setToolTip(
            f"{self._ghosts} picture(s) are listed for this trip but their files are "
            f"not on disk, so they are not mapped. Re-run Extract Days to rebuild "
            f"this trip's day data from the master." if self._ghosts else "")
        self._showall_btn.setVisible(bool(self._hidden))
        if fit:
            self._map.fit_to_points()

    def _on_hide(self, cluster: dict):
        """Right-click → Hide: drop this bubble's pictures from the map + route."""
        upns = {u for u in cluster.get("upns", []) if u}
        if not upns:
            return
        self._hidden |= upns
        self._render(fit=False)          # keep the current view; user chose the frame

    def _show_all_hidden(self):
        if not self._hidden:
            return
        self._hidden.clear()
        self._render(fit=True)

    # ── drop-to-geotag (Phase 5b) ────────────────────────────────────────────
    def _on_tray_emptied(self):
        if self._tray is not None:
            self._tray.setVisible(False)

    def _cell_info(self, key: str) -> str:
        """Identify a (often black / render-less) no-GPS picture: capture time,
        file modified time, and which file types are present (reveals e.g. a
        .dop-only or RAW-without-preview picture)."""
        try:
            item = self._coll.item_for_key(key)
        except (ValueError, KeyError):
            return key
        taken = (getattr(item, "date", "") or "").strip() or "—"
        exts = ", ".join(getattr(item, "file_exts", []) or []) or "—"
        name = getattr(item, "display_name", "") or key
        fname = NoGpsTray._fname(self._coll, key)
        mtime = "—"
        try:
            import os
            import datetime
            from pathlib import Path
            rp = getattr(self._coll, "rep_full_path", None)
            p = rp(key) if rp else None
            if p and Path(p).exists():
                mtime = datetime.datetime.fromtimestamp(
                    os.stat(p).st_mtime).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        lines = [name]
        if fname and fname != name:
            lines.append(f"File:  {fname}")
        lines += [f"Taken:  {taken}", f"Modified:  {mtime}", f"Files:  {exts}"]
        return "\n".join(lines)

    def _tray_context(self, key: str, global_pos):
        """Right-click a no-GPS thumbnail → its details + a jump to the map area
        it was probably in (the GPS points bracketing its capture time)."""
        try:
            has_time = bool((self._coll.item_for_key(key).date or "").strip())
        except (ValueError, KeyError):
            has_time = False
        menu = QMenu(self)
        for line in self._cell_info(key).split("\n"):
            act = menu.addAction(line)
            act.setEnabled(False)
        menu.addSeparator()
        est = self._estimate_position(key) if has_time else None
        place_act = None
        if est is not None:
            label = {"high": "confident", "medium": "approximate",
                     "low": "rough guess"}.get(est["conf"], "")
            place_act = menu.addAction(
                f"📍 Suggest a position — {label} (between photos before/after)")
        # The other half of the problem: no timestamp to interpolate from, or no
        # neighbours — but you WROTE DOWN where you were. Name it and we look it up.
        sel = self._tray_selection(key)
        find_act = menu.addAction(
            "🔎 Find a place by name…" if len(sel) == 1 else
            f"🔎 Find a place by name… (place all {len(sel)} selected)")
        zoom_act = menu.addAction("Zoom map to around this time" if has_time
                                  else "Zoom to around this time — no timestamp")
        zoom_act.setEnabled(has_time)   # a screenshot with no capture time can't bracket
        del_act = None
        if self._can_delete:
            menu.addSeparator()
            del_act = menu.addAction("Delete… (move to trash)")
        chosen = menu.exec(global_pos)
        if place_act is not None and chosen == place_act:
            self._suggest_position(key, est)
        elif chosen == find_act:
            self._find_place(sel)
        elif chosen == zoom_act and has_time:
            self._zoom_to_time(key)
        elif del_act is not None and chosen == del_act:
            self._delete_picture(key)

    def _tray_selection(self, key: str) -> list[str]:
        """The tray pictures a menu action should act on: the whole current
        selection when the right-clicked one is part of it (you ctrl-clicked five
        shots of the same bird at the same hotel), otherwise just that one."""
        sel = []
        if self._tray is not None:
            try:
                sel = [k for k in self._tray.selected_keys() if k in self._nogps_keys]
            except AttributeError:
                sel = []
        return sel if key in sel and len(sel) > 1 else [key]

    def _find_place(self, keys: list[str]):
        """Look a place up by NAME and drop the pending pin on it.

        The complement of _suggest_position: that infers a spot from the photos
        taken around it in time, this one is for when you know the place and the
        camera didn't. The result is not committed — it lands as the same draggable
        pin + confirm bar as every other placement, because the hotel's own
        coordinate is where the HOTEL is, not necessarily where the bird was."""
        # Bias the search toward the area already on screen: the same hotel name can
        # exist in three countries, and the trip map knows which one you mean.
        try:
            vb = self._map.visible_bounds()
        except Exception:
            vb = None
        dlg = PlaceSearchDialog(self, viewbox=vb)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.chosen:
            return
        r = dlg.chosen
        # Round to whole arcseconds (~31 m). A place-name lookup is simply not
        # accurate to the 0.3 m that hundredths of an arcsecond would claim, and a
        # visibly coarse coordinate reads as "approximate — still to be refined".
        from gui.geolocate import coarse_coord
        lat, lon = coarse_coord(r["lat"], r["lon"])
        # A named place: zoom to its street, not its province.
        self._map.focus_area([(lat, lon)], max_z=17, min_span=0.004)
        short = r["name"].split(",")[0]
        self._place_dropped(
            keys, lat, lon,
            note=f"Found “{short}” (to the nearest ~30 m). That is where the "
                 f"<i>place</i> is — drag the pin if the picture was taken somewhere "
                 f"else nearby.")

    def _suggest_position(self, key: str, est: dict):
        """Show the estimate as a coloured preview (green time-neighbours + a
        confidence-coloured DRAGGABLE pin), frame it, and open the inline confirm
        bar. The pin can be nudged onto the exact spot before committing."""
        self._map.show_geotag_preview(est["neighbours"],
                                      (est["lat"], est["lon"]), est["conf"])
        self._map.focus_area(list(est["neighbours"]) + [(est["lat"], est["lon"])])
        self._begin_confirm("place", [key], est["lat"], est["lon"],
                            note=est["detail"])

    def _estimate_position(self, key: str):
        """Estimate a no-GPS picture's location by TIME-interpolating between the
        GPS'd photos taken just before and just after it (the trip's own photos as
        a poor-man's tracklog).

        Returns a dict {lat, lon, conf, neighbours, detail} or None when it can't
        bracket. `conf` is 'high'/'medium'/'low': tightly clustered neighbours are
        a confident 'magnet', a wide gap (or a one-sided extrapolation) is a rough
        guess a human should nudge. `neighbours` are the (lat, lon) anchors used;
        `detail` is a one-line explanation for the confirm dialog."""
        try:
            dt = _parse_capture_dt(self._coll.item_for_key(key).date)
        except (ValueError, KeyError):
            dt = None
        if dt is None:
            return None
        dated = [(p, _parse_capture_dt(p.get("date", ""))) for p in self._all_points]
        dated = [(p, t) for p, t in dated if t is not None]
        if not dated:
            return None
        before = [(p, t) for p, t in dated if t <= dt]
        after = [(p, t) for p, t in dated if t >= dt]
        if before and after:
            pb, tb = max(before, key=lambda x: x[1])
            pa, ta = min(after, key=lambda x: x[1])
            span = (ta - tb).total_seconds()
            f = (dt - tb).total_seconds() / span if span else 0.0
            f = max(0.0, min(1.0, f))
            lat = pb["lat"] + f * (pa["lat"] - pb["lat"])
            lon = pb["lon"] + f * (pa["lon"] - pb["lon"])
            gap_m = _haversine_m(pb["lat"], pb["lon"], pa["lat"], pa["lon"])
            # Distance between the brackets is the confidence: a tight cluster
            # (the photos didn't move) makes the answer near-certain — the 'magnet'.
            if gap_m <= 30:
                conf = "high"
            elif gap_m <= 300:
                conf = "medium"
            else:
                conf = "low"
            # A long time gap between fixes means we could have wandered anywhere
            # in between, even if the endpoints happen to be close — soften by one.
            if span > 1200 and conf == "high":
                conf = "medium"
            elif span > 3600 and conf == "medium":
                conf = "low"
            detail = (f"Between two photos {_fmt_dist(gap_m)} apart "
                      f"({_fmt_dur(span)} between them).")
            return {"lat": lat, "lon": lon, "conf": conf,
                    "neighbours": [(pb["lat"], pb["lon"]), (pa["lat"], pa["lon"])],
                    "detail": detail}
        # One-sided: no photo on the other side of this capture time — assume it
        # stayed at the last (or next) known spot. Always a rough guess.
        pool = before or after
        p, _ = (max if before else min)(pool, key=lambda x: x[1])
        side = "before" if before else "after"
        detail = (f"No GPS'd photo {'after' if before else 'before'} this one; "
                  f"placed at the nearest known position ({side} it).")
        return {"lat": p["lat"], "lon": p["lon"], "conf": "low",
                "neighbours": [(p["lat"], p["lon"])], "detail": detail}

    # ── delete (move to trash) ───────────────────────────────────────────────
    def _day_of(self, key: str) -> str | None:
        """The day folder a UPN's files live in (their parent dir name) — used to
        target the right per-day .trash + review CSVs on the whole-trip map."""
        try:
            files = self._coll._files(key)
        except Exception:
            return None
        for fname in files:
            frow = self._coll.fpr.fi.files_dict.get(fname)
            wf = frow.get("WindowsFileName", "") if frow else ""
            if wf:
                from pathlib import Path
                return Path(wf).parent.name
        return None

    def _remove_from_day_csv(self, day: str, key: str):
        """Drop a UPN's rows from that day's data_files/data_pictures CSVs with a
        raw row-filter — preserves every other row VERBATIM (relative paths intact,
        no resolve/re-relativise), unlike a full Files/Pictures rewrite."""
        import csv
        from pathlib import Path
        review = self._trip_root / "metadata" / "review" / day
        for name in ("data_files.csv", "data_pictures.csv"):
            p = review / name
            if not p.exists():
                continue
            try:
                with open(p, "r", newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    fields = reader.fieldnames
                    rows = [r for r in reader
                            if (r.get("UniquePictureName") or "") != key]
                with open(p, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fields, delimiter=";",
                                       quoting=csv.QUOTE_MINIMAL)
                    w.writeheader()
                    w.writerows(rows)
            except Exception as e:
                logger.warning(f"Delete: CSV update failed for {p}: {e}")

    def _delete_picture(self, key: str):
        if not self._can_delete:
            return
        day = self._day_of(key)
        if not day:
            QMessageBox.warning(self, "Delete",
                                "Could not determine this picture's day folder.")
            return
        if QMessageBox.question(
                self, "Delete picture",
                f"Move this picture to the trash?\n\n{self._cell_info(key)}\n\n"
                f"→ {self._trip_root / '.trash' / day / key}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        ) != QMessageBox.StandardButton.Yes:
            return
        moved = self._coll.trash_upn(key, self._trip_root / ".trash" / day)
        if moved is None:
            QMessageBox.information(
                self, "Protected",
                "This picture has a JPG (the finished asset) and is not trashed "
                "from the map. Delete JPG-bearing pictures from the culling view.")
            return
        self._remove_from_day_csv(day, key)
        self._nogps_keys = [k for k in self._nogps_keys if k != key]
        self._all_points = [p for p in self._all_points if p.get("upn") != key]
        if self._tray is not None:
            self._tray.remove_keys([key])
        self._render(fit=False)

    def _zoom_to_time(self, key: str):
        """Zoom the map to the GPS'd pictures taken just before/after this one in
        time — its likely neighbourhood, so a precise drop is a short drag away."""
        try:
            d = (self._coll.item_for_key(key).date or "").strip()
        except (ValueError, KeyError):
            d = ""
        dated = [p for p in self._all_points if p.get("date")]
        if not dated:
            QMessageBox.information(self, "Zoom", "No dated GPS pictures to zoom to.")
            return
        neigh = []
        if d:
            before = [p for p in dated if p["date"] <= d]
            after = [p for p in dated if p["date"] >= d]
            if before:
                neigh.append(max(before, key=lambda p: p["date"]))
            if after:
                neigh.append(min(after, key=lambda p: p["date"]))
        if not neigh:
            neigh = dated
        self._map.focus_area([(p["lat"], p["lon"]) for p in neigh])

    def _place_str(self, fields: dict, lat: float, lon: float) -> str:
        loc = (fields.get("Location") or "").strip()
        if loc:
            return loc
        admin = " · ".join(p for p in (fields.get("City"), fields.get("Municipality"),
                                       fields.get("State"), fields.get("Country"))
                           if p and str(p).strip())
        return admin or f"{lat:.5f}, {lon:.5f}"

    def _on_drop(self, keys: list[str], lat: float, lon: float):
        # Defer so the source's drag.exec() stack fully unwinds before we open a
        # modal confirm / run the reverse-geocode network call.
        QTimer.singleShot(0, lambda: self._place_dropped(keys, lat, lon))

    def _place_dropped(self, keys: list[str], lat: float, lon: float,
                       note: str = ""):
        """A no-GPS picture (or selection) was dropped on the map at (lat, lon):
        show a draggable pin there and open the inline confirm bar (nudge, then
        commit). `note` explains a suggested position, when there is one."""
        keys = [k for k in keys if k in self._nogps_keys]
        if not keys:
            return
        self._map.show_geotag_preview([], (lat, lon), "manual")   # you chose the spot
        self._begin_confirm("place", keys, lat, lon, note=note)

    # ── inline confirm bar (pending placement / move) ─────────────────────────
    def _begin_confirm(self, mode: str, keys: list[str], lat: float, lon: float,
                       note: str = "", warnings: str = ""):
        """Open the non-modal confirm bar for a pending place/move. The map stays
        fully visible and the pin stays draggable, so the spot can be nudged before
        committing (a modal dialog would cover the very spot AND block the drag)."""
        place, fields = self._geocode(lat, lon)
        self._pending = {"mode": mode, "keys": keys, "lat": lat, "lon": lon,
                         "fields": fields, "place": place,
                         "note": note, "warnings": warnings}
        self._bar_ok.setText("Place here" if mode == "place" else "Move here")
        self._refresh_bar()
        self._bar.setVisible(True)

    def _refresh_bar(self):
        p = self._pending
        if not p:
            return
        n = len(p["keys"])
        verb = "Place" if p["mode"] == "place" else "Move"
        bits = [f"<b>{verb} {n} picture{'' if n == 1 else 's'} here?</b> "
                f"{p['place']} · {p['lat']:.5f}, {p['lon']:.5f}"]
        if p["note"]:
            bits.append(p["note"])
        if p["warnings"]:
            bits.append(p["warnings"])
        bits.append("Drag the pin on the map to adjust.")
        self._bar_label.setText("<br>".join(bits))

    def _on_pin_nudged(self, lat: float, lon: float):
        """The pending pin was dragged: re-geocode the new spot and update the bar."""
        if not self._pending:
            return
        place, fields = self._geocode(lat, lon)
        self._pending.update({"lat": lat, "lon": lon,
                              "place": place, "fields": fields})
        self._refresh_bar()

    def _end_confirm(self):
        self._pending = None
        self._bar.setVisible(False)
        self._map.clear_geotag_preview()

    def _confirm_cancel(self):
        self._end_confirm()

    def _confirm_accept(self):
        p = self._pending
        if not p:
            return
        keys, lat, lon = p["keys"], p["lat"], p["lon"]
        place, fields, mode = p["place"], p["fields"], p["mode"]
        written, skipped = self._write_gps_place(keys, lat, lon, fields)
        if mode == "place":
            for k in written:
                try:
                    date = getattr(self._coll.item_for_key(k), "date", "") or ""
                except (ValueError, KeyError):
                    date = ""
                self._all_points.append({"lat": lat, "lon": lon, "upn": k,
                                         "place": place, "date": date.strip()})
            if written:
                self._nogps_keys = [k for k in self._nogps_keys if k not in written]
                if self._tray is not None:
                    self._tray.remove_keys(written)
        else:                                   # move: relocate the existing points
            wset = set(written)
            for pt in self._all_points:
                if pt["upn"] in wset:
                    pt["lat"], pt["lon"], pt["place"] = lat, lon, place
        self._placed.update(written)
        self._end_confirm()
        if written:
            self._render(fit=False)      # keep the view; the dots land in place
            # Let the host viewer refresh: a just-placed picture should drop out of
            # its 📍 "missing GPS" subset instead of lingering in the grid.
            self.pictures_geotagged.emit(list(written))
        self._warn_skipped(skipped)

    def _geocode(self, lat: float, lon: float) -> tuple[str, dict]:
        """Reverse-geocode (lat, lon) → (display place string, location fields),
        under a wait cursor. Shared by place + move."""
        from gui.geolocate import reverse_geocode, place_fields
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            fields = place_fields(reverse_geocode(lat, lon)) or {}
        finally:
            QApplication.restoreOverrideCursor()
        return self._place_str(fields, lat, lon), fields

    def _write_gps_place(self, keys: list[str], lat: float, lon: float,
                         fields: dict) -> tuple[list[str], int]:
        """Write GPS (+ location text when we have a place) into the sidecars and
        refresh the in-memory caches. Returns (written_keys, skipped_count); does NOT
        touch _all_points — callers append (place) or update (move).

        ONE exiftool call for the coordinate and ONE for the place text, however many
        pictures are involved: every picture in a drop or a bubble-move lands on the
        SAME spot, and both writers take a file list. Dragging 62 pictures onto the
        map used to spawn exiftool 124 times (a GPS write and a location write EACH);
        it is now 2 calls total."""
        from gui.geolocate import write_gps, write_location_batch
        has_place = any(fields.values())
        files, written, skipped = [], [], 0
        for k in keys:
            f = self._coll.location_targets(k)
            if not f:
                skipped += 1              # HEIC-only / no writable sidecar
                continue
            files.extend(f)
            written.append(k)
        if not files:
            return [], skipped

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            write_gps(files, lat, lon)
            if has_place:
                write_location_batch(files, fields, overwrite=True)
            # In-memory only from here — no more spawns. Handing the coordinates over
            # is also what makes a MOVE stick: the collection used to keep the
            # pre-move value, so a moved picture snapped back to its old spot the
            # moment the map was re-opened.
            for k in written:
                if has_place:
                    self._coll.note_location_written(k, fields)
                self._coll.note_gps_written(k, lat, lon)
        finally:
            QApplication.restoreOverrideCursor()
        return written, skipped

    def _warn_skipped(self, skipped: int):
        if skipped:
            QMessageBox.information(
                self, "Set location",
                f"{skipped} picture{'' if skipped == 1 else 's'} had no writable "
                f"sidecar (e.g. HEIC-only) and could not be geotagged.")

    def _on_move(self, data: dict, lat: float, lon: float):
        # Defer so the mouse-release handler fully unwinds before we geocode.
        QTimer.singleShot(0, lambda: self._move_group(data, lat, lon))

    def _move_group(self, data: dict, lat: float, lon: float):
        """A bubble was dragged to (lat, lon): drop a draggable pin there and open
        the confirm bar to re-place ALL its pictures (a bubble is a same-spot
        group). Warns when the bubble spans several distinct spots (zoom in for
        just one) and when it overwrites pictures that already carried GPS."""
        present = {p["upn"] for p in self._all_points}
        keys = [k for k in (data.get("upns") or [])
                if k in present and k not in self._hidden]
        if not keys:
            return

        # In geotag mode the coordinates under this bubble are the MASTER's. Moving
        # is allowed ONLY where a human placed them (map drag / place-name lookup) —
        # refining our own guess is the point; overwriting what a camera recorded is
        # not. Provenance comes from GPSProcessingMethod, never from how precise the
        # number looks.
        if self._geotag:
            derived = getattr(self._coll, "gps_is_derived", None)
            fixed = [k for k in keys if not (derived and derived(k))]
            if fixed:
                QMessageBox.information(
                    self, "Cannot move these",
                    f"{len(fixed)} of these {len(keys)} picture(s) carry GPS the "
                    f"CAMERA recorded, and this map does not overwrite that.\n\n"
                    f"Only coordinates a person placed — dragged onto the map, or "
                    f"looked up from a place name — can be moved and refined.")
                return

        spots = data.get("n", 1) or 1
        had_gps = [k for k in keys if k not in self._placed]
        warn = []
        if spots > 1:
            warn.append(f"⚠ This group covers {spots} different spots — cancel and "
                        f"zoom in first if you only meant one.")
        if had_gps:
            if self._geotag:
                # In geotag mode a camera's fix has ALREADY been refused above, so
                # every coordinate here is one a person put there. Saying "from the
                # camera" would be plainly false — and it is the sentence that would
                # make you hesitate over a move that is perfectly safe.
                warn.append(f"{len(had_gps)} of these already carry a position placed "
                            f"by hand (this session or an earlier one); moving replaces it.")
            else:
                # Ordinary trip map: no provenance to consult, so we can only say
                # "not placed by you in THIS session" — it may be the camera's.
                warn.append(f"⚠ {len(had_gps)} of these already have GPS (from the "
                            f"camera or an earlier session); moving overwrites it.")
        self._map.show_geotag_preview([], (lat, lon), "manual")
        self._begin_confirm("move", keys, lat, lon, warnings="<br>".join(warn))

    # ── data ─────────────────────────────────────────────────────────────────
    def _by_capture(self, keys) -> list[str]:
        """Keys in shooting order — the SAME order (and the same tiebreak) the
        collections use to lay out the grid: dated first by DateTimeOriginal, undated
        last, ties broken by name. Reads the date off the in-memory item, so this
        costs no exiftool call.

        Sorting the map's keys here rather than in each consumer is what keeps the
        tray, the bubble filmstrips and the grid telling one story."""
        def key(k: str):
            try:
                dt = (getattr(self._coll.item_for_key(k), "date", "") or "").strip()
            except Exception:
                dt = ""
            return (0, dt, k) if dt else (1, "", k)
        return sorted(keys, key=key)

    def _build_points(self) -> tuple[list[dict], list[str]]:
        """Walk the live collection's keys → map points for the GPS'd ones, and the
        keys of those without (the drag-to-geotag tray, Phase 5b).

        GPS comes from `gps_cached` (in-memory) — NOT `gps()`, which spawns an
        exiftool per file; plotting a day would otherwise be hundreds of spawns.

        GUARDRAIL: a picture whose files are GONE is not plotted. A CSV can hold
        ghost rows (a stale virtual-trip extract carries master index rows for
        pictures that were never filed under biota/ and never copied in) — they
        have GPS, so they would draw bubbles for photos that do not exist and hand
        you a blank filmstrip. `present_keys` is one cached existence pass over
        the collection; `_ghosts` reports the count so the header can say so
        instead of quietly under-counting."""
        present = None
        pk = getattr(self._coll, "present_keys", None)
        if callable(pk):
            try:
                present = set(pk())
            except Exception:
                present = None
        pts: list[dict] = []
        no_gps: list[str] = []
        ghosts = 0
        for k in self._keys:
            if present is not None and k not in present:
                ghosts += 1        # index row without files — not a place we were
                continue
            ll = self._coll.gps_cached(k)
            if not ll:
                if self._has_image(k):
                    no_gps.append(k)   # skip sidecar-only phantoms (orphan .dop)
                continue
            try:
                item = self._coll.item_for_key(k)
                place = self._place_for(item, ll)
                date = getattr(item, "date", "") or ""
            except (ValueError, KeyError, IndexError):
                place, date = f"{ll[0]:.4f}, {ll[1]:.4f}", ""
            pts.append({"lat": ll[0], "lon": ll[1], "upn": k,
                        "place": place, "date": date.strip()})
        self._ghosts = ghosts
        return pts, no_gps

    def _has_image(self, k: str) -> bool:
        """True when the UPN has a real image file (not just orphan sidecars)."""
        try:
            exts = {e.lower() for e in (self._coll.item_for_key(k).file_exts or [])}
        except Exception:
            exts = set()
        if not exts:
            from pathlib import Path
            try:
                exts = {Path(f).suffix.lstrip(".").lower()
                        for f in self._coll._files(k)}
            except Exception:
                return True   # unknown → don't hide a possibly-real picture
        return bool(exts & _IMAGE_EXTS)

    @staticmethod
    def _place_for(item, ll) -> str:
        """A tooltip place for a PLOTTED point: reverse-geocoded Location, else
        City·Municipality·State, else the coordinates. Never 'No GPS data
        available' — the point is on the map, so it has GPS (item.location_line()
        would wrongly say that when the place fields are empty and the provider
        doesn't cache gps_text)."""
        try:
            pf = item.place_fields or {}
        except Exception:
            pf = {}
        loc = (pf.get("Location") or "").strip()
        if loc:
            return loc
        admin = " · ".join(p for p in (pf.get("City"), pf.get("Municipality"),
                                       pf.get("State")) if p and str(p).strip())
        return admin or f"{ll[0]:.4f}, {ll[1]:.4f}"

    @staticmethod
    def _trip_tip(cluster: dict) -> str:
        """Bubble tooltip: place name, then the date (or date range), then the
        picture count — the trip map's 'where + when' instead of a species name."""
        place = (cluster.get("place") or "").strip() or "—"
        n = cluster.get("count", 0)
        d0 = (cluster.get("date_min") or "")[:10].replace(":", "-")
        d1 = (cluster.get("date_max") or "")[:10].replace(":", "-")
        date = d0 if (not d1 or d0 == d1) else f"{d0} → {d1}"
        lines = [place]
        if date:
            lines.append(date)
        lines.append(f"{n} picture" + ("" if n == 1 else "s"))
        return "\n".join(lines)

    # ── header actions ───────────────────────────────────────────────────────
    def _map_set_path(self, on: bool):
        self._map.set_path_visible(on)
        self._update_path_legend()

    def _update_path_legend(self):
        """Gradient legend for the day-coloured travel path (light = first day →
        dark = last). Hidden for a single day, or when the path is off — there is
        no progression to show."""
        days = self._map.path_days()
        show = self._path_chk.isChecked() and len(days) > 1
        self._legend.setVisible(show)
        if not show:
            return
        n = len(days)
        w, hgt = 90, 8
        pm = QPixmap(w, hgt)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        grad = QLinearGradient(0, 0, w, 0)
        for i in range(n):
            grad.setColorAt(i / (n - 1), QColor(TileMapView.day_color(i, n)))
        p.fillRect(0, 0, w, hgt, QBrush(grad))
        p.setPen(QPen(QColor(0, 0, 0, 40)))         # hairline ring
        p.drawRect(0, 0, w - 1, hgt - 1)
        p.end()
        self._legend_bar.setPixmap(pm)
        self._legend_from.setText(days[0][5:])      # MM-DD
        self._legend_to.setText(days[-1][5:])
        self._legend.setToolTip(
            f"Travel path coloured by day — {days[0]} (light) → {days[-1]} (dark), "
            f"{n} days. Colour shows the trip's progression; hover a leg for its "
            f"exact date.")

    def _on_basemap_changed(self, _idx: int):
        entry = self._basemap_combo.currentData()
        if not entry:
            return
        self._attr.setText(self._map.set_basemap(entry))
        self._attr.adjustSize()
        self._place_attribution()

    def _place_attribution(self):
        vp = self._map.viewport()
        self._attr.adjustSize()
        self._attr.move(vp.width() - self._attr.width(),
                        vp.height() - self._attr.height())
        self._attr.raise_()

    # ── bubble → filmstrip → large view (over the live collection directly) ──────
    def _resolves(self, upn: str) -> bool:
        # item_for_key (not index_of) — the live collection may be FILTERED (its
        # _order is a subset), but the map plots all keys and must resolve any of
        # them for the filmstrip / large view.
        try:
            self._coll.item_for_key(upn)
            return True
        except (ValueError, KeyError, IndexError):
            return False

    def _on_bubble(self, cluster: dict):
        keys = [u for u in cluster.get("upns", []) if u and self._resolves(u)]
        if not keys:
            return
        from gui.large_image_view import FilmstripWidget
        strip = FilmstripWidget(self._coll, keys)
        strip.item_clicked.connect(lambda k, ks=keys: self._open_large(ks, k))
        if self._strip is not None:
            self._strip.deleteLater()
        self._strip_hint.hide()
        self._strip = strip
        self._strip_box.addWidget(strip)

    def _open_large(self, keys: list[str], key: str):
        from gui.large_image_view import LargeImageView
        if key not in keys:
            return
        dlg = LargeImageView(self, self._coll, keys, keys.index(key),
                             settings_key="TripMapView", auto_advance=True)
        dlg.picture_changed.connect(self._refresh_strip_item)
        dlg.show()

    def _refresh_strip_item(self, key: str):
        if self._strip is not None:
            try:
                self._strip.refresh_item(key)
            except Exception:
                pass
