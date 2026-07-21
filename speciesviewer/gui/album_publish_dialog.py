"""Albums ▸ Manage Album Publication — list the active album on the trip index,
or remove it from there.

One place for both directions, showing the current state so the choice is
obvious. It is deliberately NOT reachable through a Trip door — an album is its
own thing (the equivalent trip actions, "List Trip on the Site" + "Manage Trip
Publication", live under Trip).

Advertising is separate from publishing: Create/Build already put the album LIVE
at its /trips/<slug>/ URL. This only controls whether the trip index LINKS to it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDialogButtonBox,
    QFrame,
)

from utils.config import _config
from utils.virttrip_config import read_virttrip_ini

logger = logging.getLogger("speciesviewer")

_ADVERTISE, _UNADVERTISE = "advertise", "unadvertise"


class AlbumPublishDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.logger = parent.logger
        self.passed_parent = parent
        self.setWindowTitle("Manage Album Publication")
        self.setMinimumWidth(560)

        root = (_config.get("virttrip", "virttripbiotadatabase", fallback="")
                or "").replace('"', "").replace("'", "").strip()
        self._root = root
        data = read_virttrip_ini(root) if root else {}
        self._trip_name = (data or {}).get("trip_name",
                                           Path(root).name if root else "")
        web = (_config.get("virttrip", "virttripbiotawebroot", fallback="")
               or "").replace('"', "").replace("'", "").strip()

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        if not root or not Path(root).is_dir():
            layout.addWidget(QLabel(
                "No album is selected. Open an album in the Album tab first."))
            box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            box.rejected.connect(self.reject)
            box.accepted.connect(self.reject)
            layout.addWidget(box)
            self._ok = False
            return
        self._ok = True

        layout.addWidget(QLabel(
            f"Album <b>{self._trip_name or Path(root).name}</b> is published at "
            f"<b>{web or '(no web root set)'}</b>."))

        # Current advertised state, read from the authoritative web copy.
        self._status = self._current_status()
        self._status_lbl = QLabel(self._status_text())
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)

        explain = QLabel(
            "Listing adds this album to the site's trip index (the page that links "
            "all trips). Removing takes it off that index — the album's own pages "
            "stay online at the URL above, they are just no longer linked.")
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

    # ── status ────────────────────────────────────────────────────────────────
    def _current_status(self):
        """True advertised / False not / None unknown (offline)."""
        from engine.album_publish import advertised_names
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

    def _run(self, which):
        import batch.svv5AddToMaster
        import batch.svv8UnadvertiseAlbum
        from gui.batch import BatchModuleRunner
        from gui.virttrip_process import _virttrip_spec

        if which == _ADVERTISE:
            spec = _virttrip_spec(
                batch.svv5AddToMaster.main,
                "Listing the album on the trip index…",
                "✅ Album listed on the trip index.")
        else:
            spec = _virttrip_spec(
                batch.svv8UnadvertiseAlbum.main,
                "Removing the album from the trip index…",
                "✅ Album removed from the trip index.")
        runner = BatchModuleRunner(self, self.passed_parent)
        new_status = which == _ADVERTISE  # advertise → listed, unadvertise → not
        runner.run(spec, on_success=lambda: self._refresh_after(new_status))

    def _refresh_after(self, new_status):
        # Trust the action we just completed rather than re-reading the live site:
        # right after an upload the web copy is often still cached and would report
        # the old state, leaving the buttons out of step with what just happened.
        # The album's own web pages are untouched either way.
        self._status = new_status
        self._status_lbl.setText(self._status_text())
        self._list_btn.setEnabled(True)
        self._remove_btn.setEnabled(True)
        self._list_btn.setToolTip("")
        self._remove_btn.setToolTip("")
        self._apply_status_to_buttons()
