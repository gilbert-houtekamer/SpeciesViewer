"""Get GPS from the location TEXT a picture already carries — the mirror of
"Get location".

"Get location" goes GPS → place name (reverse geocoding). This goes the other way:
place name → GPS (forward geocoding). It is for the pictures of the 2014–2016 era,
which typically carry a crude written place ("Helgoland") and no coordinate at all,
because the camera had no receiver.

The result is DELIBERATELY approximate and says so:

  * the coordinate is rounded to whole arcseconds (~31 m), because a place-name
    lookup is worth tens or hundreds of metres and nothing more — writing hundredths
    of an arcsecond would claim an accuracy we do not have;
  * it is stamped GPSProcessingMethod = MANUAL, which is what later lets you DRAG it
    to the real spot on the day map. A camera's own fix carries no such stamp and
    the map refuses to move it.

So the workflow is: seed here (one lookup for all 93 Helgoland pictures), then open
the day map and nudge the groups apart. Never a substitute for a real fix — a way to
get from "nowhere" to "roughly the right rock".
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

logger = logging.getLogger("speciesviewer")

# The written place, most specific first. Joined into ONE query so "Helgoland" plus
# its country beats a same-named village elsewhere.
_QUERY_KEYS = ("Location", "City", "Municipality", "State", "Country")


def location_query(fields: dict) -> str:
    """The place text of one picture, as a geocoder query. Duplicates are dropped —
    Location and City are often both "Helgoland", and repeating it helps nothing."""
    parts, seen = [], set()
    for k in _QUERY_KEYS:
        v = str(fields.get(k, "") or "").strip()
        if v and v.casefold() not in seen:
            seen.add(v.casefold())
            parts.append(v)
    return ", ".join(parts)


def group_by_place(collection, keys) -> dict:
    """{query text: [keys]} for the pictures that NEED this — no GPS, but some place
    text, and a writable sidecar. Grouping is the point: 93 Helgoland pictures are
    ONE lookup, not 93 (and Nominatim would rate-limit us for the other 92)."""
    groups: dict = {}
    for k in keys:
        if collection.has_gps(k):
            continue                      # fill empties; never overwrite a real fix
        if not collection.location_targets(k):
            continue                      # nothing writable (HEIC-only, read-only)
        q = location_query(collection.read_location(k) or {})
        if q:
            groups.setdefault(q, []).append(k)
    return groups


def resolve_places(queries, viewbox=None) -> dict:
    """{query: candidate or None} — one Nominatim call per DISTINCT place, rounded to
    the precision such a lookup is actually worth."""
    from gui.geolocate import search_places, coarse_coord
    out = {}
    for q in queries:
        hits = search_places(q, viewbox=viewbox, limit=1)
        if not hits:
            out[q] = None
            logger.info(f"GPS from location: no match for {q!r}")
            continue
        r = dict(hits[0])
        r["lat"], r["lon"] = coarse_coord(r["lat"], r["lon"])
        out[q] = r
    return out


def _dms(lat: float, lon: float) -> str:
    def one(v, pos, neg):
        h = pos if v >= 0 else neg
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        s = (v - d - m / 60) * 3600
        return f"{d}° {m}' {s:.0f}\" {h}"
    return f"{one(lat, 'N', 'S')}, {one(lon, 'E', 'W')}"


class GpsFromLocationDialog(QDialog):
    """Review before anything is written. One row per distinct written place: what we
    searched for, what OpenStreetMap found, the coordinate, and how many pictures it
    would be given to. Untick a row you don't trust."""

    def __init__(self, parent, groups: dict, resolved: dict):
        super().__init__(parent)
        self.setWindowTitle("Get GPS from location")
        self.resize(940, 420)
        self._rows: list = []

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "These pictures have a written place but no GPS. Each place was looked up "
            "once.\n\nThe coordinate is where the PLACE is, to about 30 m — good enough "
            "to put the pictures on the map, not where the bird was. Afterwards, open "
            "the day's map and drag them onto the exact spot."))

        self._table = QTableWidget(len(groups), 5)
        self._table.setHorizontalHeaderLabels(
            ["Use", "Written place", "Found by OpenStreetMap", "Coordinate (~30 m)", "Pictures"])
        self._table.verticalHeader().setVisible(False)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for c in (0, 1, 3, 4):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)

        for i, (q, keys) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
            hit = resolved.get(q)
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(Qt.CheckState.Checked if hit else Qt.CheckState.Unchecked)
            if not hit:
                use.setFlags(Qt.ItemFlag.ItemIsEnabled)      # nothing to apply
            self._table.setItem(i, 0, use)
            self._table.setItem(i, 1, QTableWidgetItem(q))
            if hit:
                found = f"{hit['name']}" + (f"  [{hit['kind']}]" if hit["kind"] else "")
                self._table.setItem(i, 2, QTableWidgetItem(found))
                self._table.setItem(i, 3, QTableWidgetItem(_dms(hit["lat"], hit["lon"])))
            else:
                miss = QTableWidgetItem("not found — try Edit location…, or place it by hand on the map")
                miss.setForeground(Qt.GlobalColor.darkRed)
                self._table.setItem(i, 2, miss)
                self._table.setItem(i, 3, QTableWidgetItem("—"))
            self._table.setItem(i, 4, QTableWidgetItem(str(len(keys))))
            self._rows.append((q, keys, hit))

        lay.addWidget(self._table, 1)

        found_n = sum(len(k) for _q, k, h in self._rows if h)
        miss_n = sum(len(k) for _q, k, h in self._rows if not h)
        note = QLabel(f"{found_n} picture(s) can be placed"
                      + (f" · {miss_n} could not be looked up" if miss_n else ""))
        note.setStyleSheet("color:#666;")
        lay.addWidget(note)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self._ok = QPushButton("Write GPS")
        self._ok.setDefault(True)
        self._ok.setEnabled(found_n > 0)
        self._ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(self._ok)
        lay.addLayout(btns)

    def accepted_rows(self) -> list:
        """[(keys, hit), …] for the ticked rows."""
        out = []
        for i, (_q, keys, hit) in enumerate(self._rows):
            item = self._table.item(i, 0)
            if hit and item and item.checkState() == Qt.CheckState.Checked:
                out.append((keys, hit))
        return out


def run_gps_from_location(parent, collection, keys, viewbox=None) -> list:
    """Look up the written place of every no-GPS picture in `keys`, review, then write.
    Returns the keys actually given a coordinate (so the caller can refresh cards)."""
    from gui.geolocate import write_gps, write_location_batch, GPS_MANUAL

    groups = group_by_place(collection, keys)
    if not groups:
        QMessageBox.information(
            parent, "Get GPS from location",
            "Nothing to do: these pictures either already have GPS, carry no written "
            "place to look up, or have no writable sidecar.\n\nIf the place is missing, "
            "add it first with “Edit location…”.")
        return []

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        resolved = resolve_places(list(groups), viewbox=viewbox)
    finally:
        QApplication.restoreOverrideCursor()

    dlg = GpsFromLocationDialog(parent, groups, resolved)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return []

    written: list = []
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        for grp_keys, hit in dlg.accepted_rows():
            lat, lon = hit["lat"], hit["lon"]
            for k in grp_keys:
                files = collection.location_targets(k)
                if not files:
                    continue
                # MANUAL: a human's place name, not a receiver. This is what lets the
                # map move it later — and what stops it being mistaken for evidence.
                if write_gps(files, lat, lon, method=GPS_MANUAL):
                    collection.note_gps_written(k)
                    written.append(k)
    finally:
        QApplication.restoreOverrideCursor()

    logger.info(f"GPS from location: wrote a coordinate to {len(written)} picture(s) "
                f"across {len(dlg.accepted_rows())} place(s).")
    if written:
        QMessageBox.information(
            parent, "Get GPS from location",
            f"{len(written)} picture(s) now have an approximate coordinate.\n\n"
            f"They are marked as placed by hand, so you can open the day's map and "
            f"drag them onto the exact spot. Pictures whose GPS came from a camera "
            f"stay locked.")
    return written
