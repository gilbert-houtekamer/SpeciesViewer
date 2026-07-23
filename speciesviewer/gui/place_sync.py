"""Master ▸ "Sync placed GPS into the index…" — reflect the place name and the
MANUAL provenance flag a HAND-PLACED coordinate already carries in its sidecar into
the master index.

Why it exists: geotagging writes the coordinate, the place name and the MANUAL flag
into the picture's XMP sidecar, but the master's own mirror used to copy ONLY the
coordinate. So a picture placed in an earlier session sat in the master with a
coordinate and nothing else: the album/day cards (which read the master index, not
each sidecar) showed raw decimals instead of the name, and the map — which reads
provenance from the master — mistook the hand-placed fix for a camera one and refused
to move it. New geotags now mirror all three; this backfills the ones placed before.

It invents nothing: it reads what the sidecar already holds and copies it up. A
picture whose sidecar has no place name, or whose coordinate really is a camera fix
(no MANUAL flag), is left exactly as it is.
"""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QProgressDialog

logger = logging.getLogger("speciesviewer")

_PLACE_KEYS = ("Location", "City", "Municipality", "State", "Country")


def _sidecar_path_by_upn(db, upns) -> dict:
    """upn -> the best on-disk file to read place/provenance from: the .xmp sidecar,
    else the .jpg (a JPG-only picture keeps its place text in the JPG itself)."""
    best: dict = {}
    for fname, frow in db.files_for_upns(upns).items():
        upn = frow.get("UniquePictureName") or ""
        path = frow.get("WindowsFileName") or ""
        ext = (frow.get("WindowsExtension") or "").lower()
        if not upn or not path:
            continue
        rank = {"xmp": 2, "jpg": 1, "jpeg": 1}.get(ext, 0)
        if rank == 0:
            continue
        cur = best.get(upn)
        if cur is None or rank > cur[0]:
            best[upn] = (rank, path)
    return {u: p for u, (_r, p) in best.items()}


def apply_sync(master_root, candidates, *, progress_cb=None, should_stop=None) -> dict:
    """Read each candidate's sidecar and mirror its existing place name + MANUAL flag
    into the master index. `candidates` is the list from
    MasterDB.pictures_missing_place_or_provenance(). Returns
    {names, provenance, scanned}.

    Takes the master ROOT (not a MasterDB): a SQLite connection is thread-affine, so
    this — which runs on a worker thread — opens its OWN MasterDB here rather than
    reusing the main thread's scan handle."""
    from db.master_db import MasterDB
    from gui.geolocate import read_place_and_provenance_batch, GPS_MANUAL
    db = MasterDB(master_root)
    need_place = {c["upn"] for c in candidates if c.get("need_place")}
    need_prov = {c["upn"] for c in candidates if c.get("need_prov")}
    path_by_upn = _sidecar_path_by_upn(db, [c["upn"] for c in candidates])
    items = list(path_by_upn.items())

    read: dict = {}
    CHUNK = 200
    for i in range(0, len(items), CHUNK):
        if should_stop and should_stop():
            break
        chunk = items[i:i + CHUNK]
        read.update(read_place_and_provenance_batch([p for _u, p in chunk]))
        if progress_cb:
            progress_cb(min(i + CHUNK, len(items)), len(items))

    loc_updates: dict = {}
    prov_updates: dict = {}
    for upn, path in path_by_upn.items():
        d = read.get(os.path.normcase(os.path.normpath(path)), {})
        if upn in need_place:
            loc = {k: d[k] for k in _PLACE_KEYS if d.get(k)}
            if loc:
                loc_updates[upn] = loc
        if upn in need_prov:
            if (d.get("GPSProcessingMethod", "") or "").strip().upper() == GPS_MANUAL:
                prov_updates[upn] = GPS_MANUAL

    names = db.apply_location_fields(loc_updates)
    provenance = db.apply_provenance(prov_updates)
    return {"names": names, "provenance": provenance, "scanned": len(candidates)}


class _SyncThread(QThread):
    """The sidecar reads (hundreds of exiftool calls, batched 200 at a time) run off
    the UI thread so the progress dialog stays alive and cancellable."""
    progress = pyqtSignal(int, int)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, master_root, candidates, parent=None):
        super().__init__(parent)
        self._root, self._candidates, self._stop = master_root, candidates, False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            res = apply_sync(
                self._root, self._candidates,
                progress_cb=lambda d, t: self.progress.emit(d, t),
                should_stop=lambda: self._stop)
        except Exception as e:
            logger.exception("place sync failed")
            self.failed.emit(str(e))
            return
        self.done.emit(res)


def open_place_sync(main_window, from_date=None, to_date=None, scope_label=""):
    """Sync placed GPS into the master index. Scoped to a camera-date range (an
    album's fromdate..todate) when given — the sane default, since the provenance
    column is empty for nearly every GPS picture, so a master-wide run would read
    tens of thousands of camera sidecars to change the few a person placed."""
    from utils.config import _config
    from db.master_db import MasterDB
    root = _config.get("master", "MasterBiotaDatabase", fallback="").strip().strip('"')
    if not root:
        QMessageBox.warning(main_window, "Sync placed GPS",
                            "No master repository is configured.")
        return
    if _config.get("master", "readonly", fallback="yes").strip().lower() != "no":
        QMessageBox.warning(main_window, "Sync placed GPS",
                            "This machine's master is read-only, so the index cannot "
                            "be updated here.")
        return
    db = MasterDB(root)
    candidates = db.pictures_missing_place_or_provenance(from_date, to_date)
    if not candidates:
        QMessageBox.information(
            main_window, "Sync placed GPS",
            "Every placed coordinate already has its name and movable flag in the "
            "master index. Nothing to sync.")
        return

    n_place = sum(1 for c in candidates if c.get("need_place"))
    n_prov = sum(1 for c in candidates if c.get("need_prov"))
    if QMessageBox.question(
            main_window, "Sync placed GPS into the index",
            f"<b>{len(candidates):,} pictures</b> carry a coordinate whose details "
            f"aren't fully in the master index yet:<br>"
            f"&nbsp;&nbsp;• {n_place:,} are missing their place name (cards show raw "
            f"coordinates)<br>"
            f"&nbsp;&nbsp;• {n_prov:,} are missing the flag that lets the map move "
            f"them<br><br>"
            f"This reads each picture's sidecar and copies what it already holds into "
            f"the index — it invents nothing, and never touches a genuine camera fix. "
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
        return

    dlg = QProgressDialog("Reading sidecars…", "Cancel", 0, len(candidates), main_window)
    dlg.setWindowTitle("Sync placed GPS")
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setValue(0)

    thread = _SyncThread(root, candidates, main_window)

    def on_progress(done, total):
        dlg.setMaximum(total)
        dlg.setValue(done)
        dlg.setLabelText(f"Reading sidecars… {done} / {total}")

    def on_done(res: dict):
        dlg.close()
        QMessageBox.information(
            main_window, "Sync placed GPS",
            f"Synced the master index from the sidecars:<br>"
            f"&nbsp;&nbsp;• {res['names']:,} place names filled in<br>"
            f"&nbsp;&nbsp;• {res['provenance']:,} marked movable (MANUAL)<br><br>"
            f"Reopen an album or day to see the names on the cards.")

    def on_failed(msg: str):
        dlg.close()
        QMessageBox.critical(main_window, "Sync placed GPS", f"Sync failed:\n{msg}")

    thread.progress.connect(on_progress)
    thread.done.connect(on_done)
    thread.failed.connect(on_failed)
    dlg.canceled.connect(thread.stop)
    # Strong ref so the thread/dialog aren't GC'd mid-run.
    main_window._place_sync_thread = thread
    main_window._place_sync_dlg = dlg
    thread.start()


def open_place_sync_for_active_album(main_window):
    """Albums ▸ Sync placed GPS into the index — scoped to the active album's dates."""
    from utils.config import _config
    from utils.virttrip_config import read_virttrip_ini
    root = _config.get("virttrip", "virttripbiotadatabase", fallback="").strip()
    if not root:
        QMessageBox.warning(main_window, "Sync placed GPS", "No album is active.")
        return
    info = read_virttrip_ini(root) or {}
    fdate = (info.get("fromdate") or "").strip()
    tdate = (info.get("todate") or "").strip()
    if not (fdate and tdate):
        QMessageBox.warning(main_window, "Sync placed GPS",
                            "This album has no date range set, so its pictures can't "
                            "be located in the master.")
        return
    name = info.get("trip_name") or "this album"
    open_place_sync(main_window, fdate, tdate, scope_label=name)
