"""Right-hand info rail (ch.15 main-window layout pass).

Replaces the old 3rd hsplitter slot, which used to be VirtTripInfoPanel's
album list full-time (that list now lives in the left mode stack instead).
Collapses to a bare strip (just the fold chip — no decorative icons); expanded
it shows the Master Repository + Album Root pointers (mirrors TripInfoPanel's
and VirtTripInfoPanel's own former headers, same underlying config/helper, so
they stay readable regardless of which left-mode is active) plus quick
Trips/Albums/Days counts read from already-loaded data — no new disk/DB
queries.
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QStackedWidget

from utils.config import _config, get_virttrips_root
from utils import days_registry
from gui import theme

_FONT = theme.FONT_MONO
_SECTION_FONT = f"font-size: 9pt; font-family: Consolas, monospace; color: {theme.NEUTRAL_GRAY}; font-weight: bold;"


class SidebarInfoRail(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setStyleSheet(f"{_FONT} background-color: {theme.PANEL_BG};")

        self._stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

        self._stack.addWidget(self._build_collapsed_page())
        self._stack.addWidget(self._build_expanded_page())

        self.set_collapsed(True)

    def _build_collapsed_page(self):
        # Deliberately bare — the fold chip above it is the only affordance.
        # (An earlier draft put two decorative icons here; they didn't mean
        # or do anything, which is exactly the kind of UI this project avoids.)
        return QWidget()

    def _build_expanded_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        layout.addWidget(self._section_label("Stats · this machine"))
        self._trips_stat = QLabel("")
        self._albums_stat = QLabel("")
        self._days_stat = QLabel("")
        for lbl in (self._trips_stat, self._albums_stat, self._days_stat):
            lbl.setStyleSheet(_FONT)
            layout.addWidget(lbl)

        layout.addSpacing(10)
        layout.addWidget(self._section_label("Master Repository"))
        self._repo_label = QLabel("")
        self._repo_label.setWordWrap(True)
        self._repo_label.setStyleSheet(_FONT)
        layout.addWidget(self._repo_label)

        layout.addWidget(self._section_label("Master URL"))
        self._url_label = QLabel("")
        self._url_label.setWordWrap(True)
        self._url_label.setStyleSheet(_FONT)
        layout.addWidget(self._url_label)

        layout.addSpacing(10)
        layout.addWidget(self._section_label("Album Root"))
        self._album_root_label = QLabel("")
        self._album_root_label.setWordWrap(True)
        self._album_root_label.setStyleSheet(_FONT)
        layout.addWidget(self._album_root_label)

        layout.addStretch(1)
        return page

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(_SECTION_FONT)
        return lbl

    def set_collapsed(self, collapsed):
        self._stack.setCurrentIndex(0 if collapsed else 1)
        if not collapsed:
            self.refresh()

    def refresh(self):
        """Re-read the cheap, already-loaded counts. Safe to call any time
        (e.g. after a trip/album/day changes) — no new disk/DB reads, just
        counts each panel's own already-populated tree/registry."""
        mw = self.main_window
        trip_panel = getattr(mw, "trip_panel", None)
        virttrip_panel = getattr(mw, "virttrip_panel", None)
        trips = trip_panel.tree.topLevelItemCount() if trip_panel else 0
        albums = virttrip_panel.tree.topLevelItemCount() if virttrip_panel else 0
        try:
            days = len(days_registry.load_days())
        except Exception:
            days = 0
        self._trips_stat.setText(f"{trips}  Trips")
        self._albums_stat.setText(f"{albums}  Albums")
        self._days_stat.setText(f"{days}  Days")

        self._repo_label.setText(str(_config["master"]["masterbiotadatabase"]))
        self._url_label.setText(f"www.houtekamer.com/{_config['master']['masterbiotawebroot']}")
        self._album_root_label.setText(get_virttrips_root() or "(not configured)")
