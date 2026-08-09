"""Manage publication of the ACTIVE trip or album — list it on the site's trip
index, or remove it from there.

One place for both directions, showing the current state so the choice is
obvious. Trip and album get the same dialog because it is the same decision:
only the ini that names the repository, the batch modules and the wording
differ. Each door stays its own — Trips ▸ Manage Trip Publication acts on the
active trip, Albums ▸ Manage Album Publication on the active album; neither is a
door to the other.

Advertising is separate from publishing: Create/Build already put the pages LIVE
at their /trips/<slug>/ URL. This only controls whether the trip index LINKS to
them. Removing a listing for something you cannot make active any more (a failed
trip whose repository is gone) lives in Master ▸ Manage Site Publication.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDialogButtonBox,
    QFrame,
)

from utils.config import _config

logger = logging.getLogger("speciesviewer")

_ADVERTISE, _UNADVERTISE = "advertise", "unadvertise"


class PublishDialog(QDialog):
    """Base for the two publication dialogs. Subclasses set the class attributes
    below; nothing else differs between trip and album."""

    KIND        = "album"       # "album" | "trip" — picks the batch modes
    NOUN        = "Album"       # user-facing word
    SECTION     = "virttrip"    # config section holding the active repository
    DB_KEY      = "virttripbiotadatabase"
    WEB_KEY     = "virttripbiotawebroot"
    PANEL_ATTR  = "virttrip_panel"
    NO_ACTIVE   = "No album is selected. Open an album in the Album tab first."

    def __init__(self, parent):
        super().__init__(parent)
        self.logger = parent.logger
        self.passed_parent = parent
        self.setWindowTitle(f"Manage {self.NOUN} Publication")
        self.setMinimumWidth(560)

        root = (_config.get(self.SECTION, self.DB_KEY, fallback="")
                or "").replace('"', "").replace("'", "").strip()
        self._root = root
        data = self._read_ini(root) if root else {}
        self._trip_name = (data or {}).get("trip_name",
                                           Path(root).name if root else "")
        web = (_config.get(self.SECTION, self.WEB_KEY, fallback="")
               or "").replace('"', "").replace("'", "").strip()

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        if not root or not Path(root).is_dir():
            layout.addWidget(QLabel(self.NO_ACTIVE))
            box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            box.rejected.connect(self.reject)
            box.accepted.connect(self.reject)
            layout.addWidget(box)
            self._ok = False
            return
        self._ok = True

        layout.addWidget(QLabel(
            f"{self.NOUN} <b>{self._trip_name or Path(root).name}</b> is published at "
            f"<b>{web or '(no web root set)'}</b>."))

        # Current advertised state, read from the authoritative web copy.
        self._status = self._current_status()
        self._status_lbl = QLabel(self._status_text())
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        noun = self.NOUN.lower()
        explain = QLabel(
            f"Listing adds this {noun} to the site's trip index (the page that links "
            f"all trips). Removing takes it off that index — the {noun}'s own pages "
            f"stay online at the URL above, they are just no longer linked.")
        explain.setWordWrap(True)
        explain.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(explain)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # Progress line — BatchModuleRunner writes its status text here.
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btns = QHBoxLayout()
        btns.addStretch()
        self._list_btn = QPushButton("List on the site")
        self._list_btn.clicked.connect(self._advertise)
        self._remove_btn = QPushButton("Remove from the site")
        self._remove_btn.clicked.connect(self._unadvertise)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btns.addWidget(self._list_btn)
        btns.addWidget(self._remove_btn)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        self._apply_status_to_buttons()

    # ── per-kind hooks ────────────────────────────────────────────────────────
    def _read_ini(self, root):
        """The repository's own ini, as a dict (needs `trip_name`)."""
        raise NotImplementedError

    def _modules(self):
        """(list_module_main, unlist_module_main) — imported lazily, because
        pulling the batch modules in at import time slows the GUI start."""
        raise NotImplementedError

    # ── status ────────────────────────────────────────────────────────────────
    def _current_status(self):
        """True advertised / False not / None unknown (offline)."""
        from engine.publish_index import advertised_names
        base_url = _config.get("websettings", "base_url", fallback="")
        master_web = _config.get("master", "masterbiotawebroot", fallback="/nature/")
        names = advertised_names(base_url, master_web)
        if names is None:
            return None
        return self._trip_name in names

    def _status_text(self):
        if self._status is True:
            return "It <b>is</b> currently listed on the trip index."
        if self._status is False:
            return "It is <b>not</b> listed on the trip index."
        return ("Could not reach the site to check whether it is listed — both "
                "actions are available, but verify the result afterwards.")

    def _apply_status_to_buttons(self):
        # Guide, don't forbid: when the state is known, make the meaningful action
        # the obvious one; when unknown (offline), leave both enabled.
        if self._status is True:
            self._list_btn.setEnabled(False)
            self._list_btn.setToolTip("Already listed.")
        elif self._status is False:
            self._remove_btn.setEnabled(False)
            self._remove_btn.setToolTip("Not listed, so nothing to remove.")

    # ── actions ───────────────────────────────────────────────────────────────
    def _advertise(self):
        self._run(_ADVERTISE)

    def _unadvertise(self):
        self._run(_UNADVERTISE)

    def _spec(self, module_fn, before_msg, after_msg):
        from gui.menu import MenuSpec
        return MenuSpec(
            callback=None,
            batch_parms={
                "statusname":    "",
                "module_called": module_fn,
                "before_msg":    before_msg,
                "after_msg":     after_msg,
                # False = run against the active TRIP repository (the default
                # svconfig), True = against the active album.
                "virttrip_mode": self.KIND == "album",
            },
        )

    def _run(self, which):
        from gui.batch import BatchModuleRunner

        list_main, unlist_main = self._modules()
        noun = self.NOUN.lower()
        if which == _ADVERTISE:
            spec = self._spec(list_main,
                              f"Listing the {noun} on the trip index…",
                              f"✅ {self.NOUN} listed on the trip index.")
        else:
            spec = self._spec(unlist_main,
                              f"Removing the {noun} from the trip index…",
                              f"✅ {self.NOUN} removed from the trip index.")
        runner = BatchModuleRunner(self, self.passed_parent)
        new_status = which == _ADVERTISE  # advertise → listed, unadvertise → not
        runner.run(spec, on_success=lambda: self._refresh_after(new_status))

    def _refresh_after(self, new_status):
        # Trust the action we just completed rather than re-reading the live site:
        # right after an upload the web copy is often still cached and would report
        # the old state, leaving the buttons out of step with what just happened.
        # The repository's own web pages are untouched either way.
        self._status = new_status
        self._status_lbl.setText(self._status_text())
        self._list_btn.setEnabled(True)
        self._remove_btn.setEnabled(True)
        self._list_btn.setToolTip("")
        self._remove_btn.setToolTip("")
        self._apply_status_to_buttons()
        # The action rewrote the local master trip_admin.csv the side panel reads
        # for its 📢 badge — rebuild it so the icon/tooltip update without a restart.
        panel = getattr(self.passed_parent, self.PANEL_ATTR, None)
        if panel is not None:
            try:
                panel.rebuild()
            except Exception:
                pass


class AlbumPublishDialog(PublishDialog):
    KIND       = "album"
    NOUN       = "Album"
    SECTION    = "virttrip"
    DB_KEY     = "virttripbiotadatabase"
    WEB_KEY    = "virttripbiotawebroot"
    PANEL_ATTR = "virttrip_panel"
    NO_ACTIVE  = "No album is selected. Open an album in the Album tab first."

    def _read_ini(self, root):
        from utils.virttrip_config import read_virttrip_ini
        return read_virttrip_ini(root)

    def _modules(self):
        import batch.svv5AddToMaster
        import batch.svv8UnadvertiseAlbum
        return batch.svv5AddToMaster.main, batch.svv8UnadvertiseAlbum.main


class TripPublishDialog(PublishDialog):
    KIND       = "trip"
    NOUN       = "Trip"
    SECTION    = "trip"
    DB_KEY     = "tripbiotadatabase"
    WEB_KEY    = "tripbiotawebroot"
    PANEL_ATTR = "trip_panel"
    NO_ACTIVE  = "No trip is selected. Open a trip in the Trip tab first."

    def _read_ini(self, root):
        from utils.trip_config import read_trip_ini
        return read_trip_ini(root)

    def _modules(self):
        import batch.svr4UpdateTrips
        import batch.svrUnadvertiseTrip
        return batch.svr4UpdateTrips.main, batch.svrUnadvertiseTrip.main
