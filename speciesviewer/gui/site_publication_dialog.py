"""Master ▸ Manage Site Publication — everything currently listed on the site's
trip index, with the ability to take one entry off it.

The normal way to list/unlist is Trips ▸ Manage Trip Publication or Albums ▸
Manage Album Publication, which act on the item you have open. This dialog is
the escape hatch for what those cannot reach: a listing whose repository is gone
or was never finished — a failed trip you had a hard time getting rid of. It
therefore works purely from trip_admin.csv, never from a repository on disk, and
shows trips AND albums, because both write their row into that one file.

Removal only takes the listing away: the entry's own pages and pictures stay
online at their /trips/<slug>/ URL, they are just no longer linked.
"""

import csv
import io
import logging
import os

from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QMovie
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from utils.config import _config

logger = logging.getLogger("speciesviewer")


def filter_trip_admin(text, drop_names):
    """Return `text` (a trip_admin.csv) with the rows for `drop_names` removed
    and every other line kept verbatim. The trip_name column is found by header
    (it is NOT the first column), and each line is csv-parsed so a ';' inside a
    quoted message field is not mistaken for a column break."""
    drop = set(drop_names)
    lines = text.splitlines()
    if not lines:
        return ""
    header = lines[0]
    try:
        name_idx = next(csv.reader([header], delimiter=";")).index("trip_name")
    except Exception:
        name_idx = 0
    kept = [header]
    for ln in lines[1:]:
        if not ln.strip():
            continue
        try:
            fields = next(csv.reader([ln], delimiter=";"))
            name = fields[name_idx].strip() if name_idx < len(fields) else ""
        except Exception:
            name = ""
        if name not in drop:
            kept.append(ln)
    return "\n".join(kept) + "\n"


class SitePublicationDialog(QDialog):
    COLUMNS = [
        ("trip_name", "Trip / Album"),
        ("title_nl",  "Title (NL)"),
        ("_period",   "Period"),
        ("created",   "Listed"),
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Manage Site Publication")
        self.resize(900, 520)
        self.logger = logging.getLogger("speciesviewer")

        self.master_db  = _config.get("master", "masterbiotadatabase", fallback="")
        self.master_web = _config.get("master", "masterbiotawebroot", fallback="/nature/")
        self.base_url   = _config.get("websettings", "base_url", fallback="").rstrip("/")
        # The index template builds each entry's picture/link as
        # parent(BiotaWebRoot)/trip_name, so the Project's web root must be a
        # TRIP web root (e.g. /trips/<x>/ → parent /trips), exactly as a normal
        # publish (svr4UpdateTrips) uses the active trip's web root. Using the
        # master web root (/nature/) would drop the /trips segment and break the
        # links. Fall back to the master web root only if no trip is active.
        self.trip_web = _config.get("trip", "tripbiotawebroot", fallback="").strip() \
            or self.master_web
        self.rows = []
        self._current_admin_text = ""   # the published CSV, verbatim

        layout = QVBoxLayout(self)

        info = QLabel(
            "Everything currently listed on the site's trip index "
            "(<code>tripindex-nl.html</code>) — trips and albums together, since "
            "both write their row into the same <code>trip_admin.csv</code>. "
            "Select one and remove its listing. Its own pages and pictures stay "
            "online; only the link from the index goes.<br>"
            "<b>For the trip or album you have open, use Trips ▸ Manage Trip "
            "Publication or Albums ▸ Manage Album Publication instead</b> — those "
            "can also put it back. This one is for listings you cannot reach that "
            "way any more."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.source_label = QLabel("")
        self.source_label.setStyleSheet("color: #666;")
        layout.addWidget(self.source_label)

        # A leading marker column makes the current selection obvious; one entry
        # is removed at a time, so selection is single-row.
        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS) + 1)
        self.table.setHorizontalHeaderLabels(["✓"] + [h for _, h in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(False)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        from gui import theme as _theme
        self.remove_btn = QPushButton("Remove from the site")
        # Destructive-action red from the theme.
        self.remove_btn.setStyleSheet(
            f"QPushButton {{ color: {_theme.ALERT}; font-weight: bold; }}")
        self.remove_btn.clicked.connect(self.on_remove)
        self.remove_btn.setEnabled(False)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addStretch()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Re-pull the published list from the website.")
        self.refresh_btn.clicked.connect(self._load)
        btn_layout.addWidget(self.refresh_btn)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        # Connect now that every widget the handler touches exists.
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        if not self.master_db or not os.path.isdir(self.master_db):
            self.source_label.setText(f"Master database not found: {self.master_db!r}")
            return
        self._load()

    # ── reading the advertised list ──────────────────────────────────────────
    def _admin_web_url(self):
        if not self.base_url:
            return ""
        return (self.base_url + self.master_web.rstrip("/")
                + "/includes%20web/trip_admin.csv")

    def _read_rows(self):
        """Return (rows, source). The published web copy is authoritative (the
        local master copy on a second machine may hold drafts), so try it first
        and fall back to the local master/admin/trip_admin.csv when offline."""
        url = self._admin_web_url()
        if url:
            try:
                import requests
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                resp.encoding = "utf-8"
                self._current_admin_text = resp.text
                rows = [r for r in csv.DictReader(io.StringIO(resp.text), delimiter=";")
                        if (r.get("trip_name") or "").strip()]
                return rows, f"published index — {url}"
            except Exception as e:
                self.logger.warning(
                    f"Site publication: web read failed ({e}); using local copy.")
        local = os.path.join(self.master_db, "admin", "trip_admin.csv")
        try:
            with open(local, "r", encoding="utf-8", newline="") as f:
                self._current_admin_text = f.read()
            rows = [r for r in csv.DictReader(io.StringIO(self._current_admin_text),
                                              delimiter=";")
                    if (r.get("trip_name") or "").strip()]
            return rows, f"local copy — {local} (not verified against the live site)"
        except Exception as e:
            self.logger.warning(f"Site publication: local read failed: {e}")
            return [], f"could not read trip_admin.csv: {e}"

    def _load(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.rows, source = self._read_rows()
        finally:
            QApplication.restoreOverrideCursor()
        for r in self.rows:
            fr, to = (r.get("fromdate") or "").strip(), (r.get("todate") or "").strip()
            r["_period"] = f"{fr} – {to}" if (fr or to) else ""
        # Match the index ordering: newest first (date prefix dominates).
        self.rows.sort(key=lambda r: (r.get("fromdate", ""), r.get("trip_name", "")),
                       reverse=True)
        self.source_label.setText(f"Source: {source}")
        self._render_table()
        self.remove_btn.setEnabled(False)

    def _render_table(self):
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            marker = QTableWidgetItem("")
            marker.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 0, marker)
            for j, (key, _) in enumerate(self.COLUMNS):
                item = QTableWidgetItem(row.get(key, ""))
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft
                                      | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, j + 1, item)
        self.table.resizeColumnsToContents()
        self.status_label.setText(f"{len(self.rows)} listed on the site.")

    def _selected_row(self):
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        return next(iter(rows)) if len(rows) == 1 else None

    def _selected_trip(self):
        r = self._selected_row()
        return self.rows[r]["trip_name"] if r is not None else None

    def _on_selection_changed(self):
        sel = self._selected_row()
        for i in range(self.table.rowCount()):
            cell = self.table.item(i, 0)
            if cell is not None:
                cell.setText("✓" if i == sel else "")
        self.remove_btn.setEnabled(sel is not None)

    # ── apply (writes the live site) ─────────────────────────────────────────
    def on_remove(self):
        key = self._selected_trip()
        if key is None:
            self.status_label.setText("Select an entry to remove.")
            return
        # The exact line that will disappear (for the confirmation) = the one
        # line present in the published CSV but not in the filtered version.
        before = self._current_admin_text.splitlines()
        after = set(filter_trip_admin(self._current_admin_text, [key]).splitlines())
        removed_line = next((ln for ln in before if ln not in after), "")
        snippet = (removed_line[:300] + "…") if len(removed_line) > 300 else removed_line
        reply = QMessageBox.question(
            self, "Remove from the site",
            f"Remove '{key}' from the published trip index?\n\n"
            f"This updates the LIVE site: it removes this one line from "
            f"trip_admin.csv, rebuilds the tripindex pages, and uploads them. "
            f"Everything else stays. The entry's own pages and pictures are NOT "
            f"deleted — only the listing.\n\n"
            f"Line removed:\n{snippet}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._start_apply(key)

    def _start_apply(self, key):
        """Remove one entry from the master trip_admin.csv (verbatim line drop,
        drafts preserved), regenerate the index, and upload both to the live
        site — on a worker thread (SFTP is slow). Mirrors svr4UpdateTrips so the
        removal sticks the same way a publish does."""
        spinner = QDialog(self)
        spinner.setWindowTitle("Updating published index...")
        spinner.setModal(True)
        spinner.setFixedSize(440, 170)
        sl = QVBoxLayout(spinner)
        msg = QLabel(f"Removing '{key}', rebuilding the index, uploading...")
        msg.setWordWrap(True)
        # full width, text aligned inside — with an addWidget alignment a
        # word-wrapped label kept its own narrow width (2026-10-18)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(msg)
        movie = QMovie("spinner.gif")
        icon = QLabel()
        icon.setMovie(movie)
        sl.addWidget(icon, alignment=Qt.AlignmentFlag.AlignCenter)
        movie.start()

        master_db, master_web, trip_web = self.master_db, self.master_web, self.trip_web
        pwd = _config.get("websettings", "Sftppwd", fallback="").strip("'")
        published_text = self._current_admin_text

        class _ApplyWorker(QObject):
            finished = pyqtSignal(str)   # error string, "" on success

            def __init__(self, name):
                super().__init__()
                self._key = name

            def run(self):
                try:
                    self._do_work()
                    self.finished.emit("")
                except Exception as e:
                    self.finished.emit(str(e))

            def _do_work(self):
                import configparser as _cp
                import engine.projectlib
                wl = logging.getLogger("speciesviewer")

                admin_file = os.path.join(master_db, "admin", "trip_admin.csv")
                # Base the new index on the list the user was just looking at —
                # the LIVE trip_admin.csv (this machine's local copy can be
                # behind another machine's, and the whole index is regenerated
                # from this base, so a stale one would drop entries published
                # elsewhere). Same rule as svr4/svv5; see
                # engine.publish_index.load_admin_base. Only the chosen line goes,
                # every other line is kept verbatim. What you saw is what is
                # published: when the web copy was unreachable the table showed
                # the local file, and that is then the base.
                after = filter_trip_admin(published_text, [self._key])
                if after == published_text:
                    raise RuntimeError(
                        f"'{self._key}' not found in the published trip index — "
                        f"nothing changed.")
                with open(admin_file, "w", encoding="utf-8", newline="") as f:
                    f.write(after)
                wl.info(f"Site publication: removed '{self._key}'; "
                        f"{admin_file} rewritten from the published index.")

                # Regenerate the index from the now-updated master, then upload
                # the index pages + trip_admin.csv (same steps as a publish).
                cfg = _cp.ConfigParser()
                cfg["master"] = {"MasterBiotaDatabase": master_db,
                                 "MasterBiotaWebRoot": master_web}
                # BiotaWebRoot = active trip web root so the index links keep the
                # /trips/ segment (see __init__); output + upload stay on master.
                project = engine.projectlib.Project(master_db, "", trip_web)
                project.initMasterAndTrip(cfg, inittrip=False)
                project.master_biota_web_root = master_web
                trip = project.Trip
                trip.master_repository_web = master_web
                trip.trip_dict = {}
                trip.read_TripAdmin()
                trip.updateTripIndexPage("nl")
                trip.updateTripIndexPage("en")

                sftp = trip.openSFTP(pwd)
                if sftp is None:
                    raise RuntimeError("Could not connect to SFTP — upload skipped.")
                trip.uploadTrip(sftp)
                trip.uploadTripAdmin(sftp, trip.getTripAdminWeb())
                sftp.disconnect()
                wl.info("Site publication: tripindex + trip_admin.csv uploaded.")

        self._apply_thread = QThread()
        self._apply_worker = _ApplyWorker(key)
        self._apply_worker.moveToThread(self._apply_thread)
        self._apply_thread.started.connect(self._apply_worker.run)

        def on_done(err):
            self._apply_thread.quit()
            self._apply_thread.wait()
            spinner.close()
            if err:
                self.logger.warning(f"Site publication: remove failed: {err}")
                self.status_label.setText(f"Remove failed: {err}")
            else:
                self.logger.info(
                    f"Site publication: '{key}' removed and site updated.")
                # Drop the row from the table immediately so it is unmistakably
                # gone — re-fetching the web here can show a cached copy that
                # still lists it. Use Refresh to re-pull the published list.
                self.rows = [r for r in self.rows if r.get("trip_name") != key]
                self._current_admin_text = filter_trip_admin(
                    self._current_admin_text, [key])
                self.table.clearSelection()
                self._render_table()
                self.status_label.setText(
                    f"✓ '{key}' removed from the site; live index updated. "
                    f"(Use Refresh to re-pull the published list.)")

        self._apply_worker.finished.connect(on_done)
        self._apply_thread.start()
        spinner.show()
