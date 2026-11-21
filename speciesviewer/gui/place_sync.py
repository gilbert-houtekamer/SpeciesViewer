"""Check the Master ▸ "Hand-Placed GPS Not in the List" — reflect the place name and the
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
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

logger = logging.getLogger("speciesviewer")

_PLACE_KEYS = ("Location", "City", "Municipality", "State", "Country")

# The last full read (Check the Master runs one): Copy… reuses it instead of reading
# every sidecar a second time, if it is recent. Cleared after a write.
_FRESH_SECONDS = 15 * 60
_last_plan: dict = {}


def _recent_plan(master_root):
    import time
    if (_last_plan.get("root") == master_root
            and time.time() - _last_plan.get("ts", 0) < _FRESH_SECONDS):
        return _last_plan.get("plan")
    return None


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


def sync_candidates(db) -> list[dict]:
    """Master-wide: the pictures whose sidecar MIGHT hold something the index lacks.
    A missing place name is worth a look for every picture with a coordinate; a
    missing movable flag only for a picture without its own camera fix — cameras
    never write MANUAL, and those are the tens of thousands that made the old
    question count "8,599 missing the flag" (backlog 1.17)."""
    cands = db.pictures_missing_place_or_provenance()
    camera = db.upns_with_camera_fix([c["upn"] for c in cands if c.get("need_prov")])
    out = []
    for c in cands:
        need_prov = c.get("need_prov") and c["upn"] not in camera
        if c.get("need_place") or need_prov:
            out.append({"upn": c["upn"], "need_place": bool(c.get("need_place")),
                        "need_prov": bool(need_prov)})
    return out


def plan_sync(master_root, candidates=None, *, progress_cb=None, should_stop=None) -> dict:
    """READ ONLY: what a sync would copy from the sidecars into the index —
    {loc_updates, prov_updates, scanned}. Opens its own MasterDB (SQLite
    connections are thread-affine; this runs on a worker thread)."""
    from db.master_db import MasterDB
    from gui.geolocate import read_place_and_provenance_batch, GPS_MANUAL
    full = candidates is None
    db = MasterDB(master_root)
    try:
        if candidates is None:
            candidates = sync_candidates(db)
        need_place = {c["upn"] for c in candidates if c.get("need_place")}
        need_prov = {c["upn"] for c in candidates if c.get("need_prov")}
        path_by_upn = _sidecar_path_by_upn(db, [c["upn"] for c in candidates])
    finally:
        db.close()
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
    plan = {"loc_updates": loc_updates, "prov_updates": prov_updates,
            "scanned": len(candidates)}
    if full and not (should_stop and should_stop()):
        import time
        _last_plan.clear()
        _last_plan.update(root=master_root, plan=plan, ts=time.time())
    return plan


def write_sync(master_root, plan) -> dict:
    """Write a plan_sync result into the index: {names, provenance}."""
    from db.master_db import MasterDB
    _last_plan.clear()                       # the list changes: the read is stale
    db = MasterDB(master_root)
    try:
        names = db.apply_location_fields(plan["loc_updates"])
        provenance = db.apply_provenance(plan["prov_updates"])
    finally:
        db.close()
    return {"names": names, "provenance": provenance}


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
            res = plan_sync(
                self._root, self._candidates,
                progress_cb=lambda d, t: self.progress.emit(d, t),
                should_stop=lambda: self._stop)
        except Exception as e:
            logger.exception("place sync failed")
            self.failed.emit(str(e))
            return
        self.done.emit(res)


def open_place_sync(main_window):
    """Check the Master ▸ Hand-placed GPS not in the list ▸ Copy… — master-wide.
    Reads the sidecars first (nothing written), then asks with the real numbers:
    how many place names and movable flags WILL change."""
    from utils.config import _config
    from db.master_db import MasterDB
    title = "Hand-Placed GPS Not in the List"
    root = _config.get("master", "MasterBiotaDatabase", fallback="").strip().strip('"')
    if not root:
        QMessageBox.warning(main_window, title, "No master repository is configured.")
        return
    if _config.get("master", "readonly", fallback="yes").strip().lower() != "no":
        QMessageBox.warning(main_window, title,
                            "This computer's master is read-only, so the list cannot "
                            "be updated here.")
        return
    recent = _recent_plan(root)
    if recent is not None:                   # Check the Master just read them
        _ask_and_write(main_window, title, root, recent)
        return
    db = MasterDB(root)
    try:
        candidates = sync_candidates(db)
    finally:
        db.close()
    if not candidates:
        QMessageBox.information(main_window, title,
                                "Every placed coordinate already has its name and "
                                "movable flag in the master's list. Nothing to copy.")
        return

    dlg = QProgressDialog("Reading sidecars…", "Cancel", 0, len(candidates), main_window)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setValue(0)
    thread = _SyncThread(root, candidates, main_window)

    def on_progress(done, total):
        dlg.setMaximum(total)
        dlg.setValue(done)
        dlg.setLabelText(f"Reading sidecars… {done} / {total}")

    def on_done(plan: dict):
        dlg.close()
        _ask_and_write(main_window, title, root, plan)

    def on_failed(msg: str):
        dlg.close()
        QMessageBox.critical(main_window, title, f"Reading the sidecars failed:\n{msg}")

    thread.progress.connect(on_progress)
    thread.done.connect(on_done)
    thread.failed.connect(on_failed)
    dlg.canceled.connect(thread.stop)
    # Strong ref so the thread/dialog aren't GC'd mid-run.
    main_window._place_sync_thread = thread
    main_window._place_sync_dlg = dlg
    thread.start()
    # Modal for the caller (Check the Master re-checks its line afterwards).
    while thread.isRunning():
        QApplication.processEvents()
        thread.wait(50)
    QApplication.processEvents()


def _ask_and_write(main_window, title, root, plan: dict):
    """Show what the read found, ask, then write."""
    n_names, n_flags = len(plan["loc_updates"]), len(plan["prov_updates"])
    if not (n_names or n_flags):
        QMessageBox.information(
            main_window, title,
            f"Read {plan['scanned']:,} sidecars: none holds a place name or a "
            f"movable flag the master's list lacks. Nothing to copy.")
        return
    if QMessageBox.question(
            main_window, title,
            f"The sidecars hold what the master's list lacks:<br>"
            f"&nbsp;&nbsp;• <b>{n_names:,}</b> place names (cards show raw "
            f"coordinates until then)<br>"
            f"&nbsp;&nbsp;• <b>{n_flags:,}</b> movable flags for hand-placed "
            f"pictures (the map cannot move them until then)<br><br>"
            f"Copy them into the list? It copies what the sidecars already hold "
            f"— it invents nothing, and never touches a camera fix.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
        return
    try:
        res = write_sync(root, plan)
    except Exception as e:
        QMessageBox.critical(main_window, title, f"Copying failed:\n{e}")
        return
    QMessageBox.information(
        main_window, title,
        f"Copied into the master's list:<br>"
        f"&nbsp;&nbsp;• {res['names']:,} place names<br>"
        f"&nbsp;&nbsp;• {res['provenance']:,} movable flags<br><br>"
        f"Reopen an album or day to see the names on the cards.")
