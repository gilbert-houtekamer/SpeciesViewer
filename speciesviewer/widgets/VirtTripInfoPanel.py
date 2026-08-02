from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QMenu
from PyQt6.QtGui import QFont, QCursor
from PyQt6.QtCore import Qt
from pathlib import Path
import csv
import os
from utils.virttrip_config import (
    read_virttrip_ini, blog_review_state, BLOG_DRAFT, read_excluded_days,
)
from utils.config import _config, persist_config, get_virttrips_root
from utils.gui_config import get_all_gui_usage, record_gui_usage
from utils.sort_modes import sort_items, DEFAULT_MODE
from utils.open_path import open_path

_PATH_ROLE = Qt.ItemDataRole.UserRole          # trip_dir Path object
_BASE_ROLE = Qt.ItemDataRole.UserRole + 1      # base label text without ▶


def _advertised_trip_names() -> set:
    """The trip_names advertised per THIS machine's master admin/trip_admin.csv — a
    cheap, local read (one small CSV). The authoritative check is against the WEB copy
    (Manage Album Publication does that); this is what the local master last recorded.
    Empty set on any problem."""
    try:
        mroot = (_config.get("master", "masterbiotadatabase", fallback="")
                 or "").replace('"', "").replace("'", "").strip()
        p = Path(mroot) / "admin" / "trip_admin.csv"
        if not p.is_file():
            return set()
        with p.open(encoding="utf-8", newline="") as f:
            return {(row.get("trip_name") or "").strip()
                    for row in csv.DictReader(f, delimiter=";")
                    if (row.get("trip_name") or "").strip()}
    except Exception:
        return set()


class VirtTripInfoPanel(QWidget):
    def __init__(self, parent, on_virttrip_selected, sort_mode=DEFAULT_MODE):
        super().__init__()
        self.main_window = parent
        self.on_virttrip_selected = on_virttrip_selected
        self._sort_mode = sort_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        font_style = "font-size: 10pt; font-family: Consolas, monospace;"
        self.setStyleSheet(f"{font_style} background-color: #f9f9f9;")

        # Album Root now lives only in the right-hand info rail
        # (widgets/SidebarInfoRail.py, ch.15 layout pass) — this panel used to
        # duplicate it here.
        layout.addWidget(self._header_label("Albums:"))

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(f"{font_style} background-color: white;")
        layout.addWidget(self.tree)

        self.tree.itemClicked.connect(self._handle_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        self._populate_tree()

    def _header_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "font-size: 10pt; font-family: Consolas, monospace; background-color: transparent;")
        lbl.setWordWrap(True)
        return lbl

    def _populate_tree(self):
        """Clear and re-populate the tree from the current virttrips root."""
        self.tree.clear()
        virttrip_root = get_virttrips_root()
        if hasattr(self, "root_label"):
            self.root_label.setText(virttrip_root or "(not configured)")
        root_path = Path(virttrip_root) if virttrip_root else None
        # The trip_names advertised per this machine's master — one small local read,
        # reused for every album row.
        advertised = _advertised_trip_names()
        if root_path and root_path.is_dir():
            usage = get_all_gui_usage()
            candidates = sort_items([p for p in root_path.iterdir() if p.is_dir()],
                                     self._sort_mode, usage)
            for d in candidates:
                if not d.is_dir():
                    continue
                # Skip hidden / housekeeping folders: .trash, and the
                # ".<name>.deleting" staging a delete leaves if a transient lock
                # blocks its final removal — neither is an album.
                if d.name.startswith("."):
                    continue
                trip_data = read_virttrip_ini(str(d))
                if trip_data is None:
                    continue

                # ── status, all cheap + local ────────────────────────────────
                # Blog dot (🟠/🟢/⚪), same convention as the yellow overview panel.
                blog_dir = d / "metadata" / "blog"
                inis = sorted(blog_dir.glob("blog_*.ini")) if blog_dir.is_dir() else []
                # A day unticked in "In Blog" is never published, so it must not keep
                # the album amber. Drop excluded days before counting drafts — the ini
                # name is blog_<YYYY-MM-DD>.ini, matching the excluded-day label.
                excluded = read_excluded_days(str(d))
                inis = [p for p in inis if p.stem[len("blog_"):] not in excluded]
                n_draft = sum(1 for p in inis if blog_review_state(p) == BLOG_DRAFT)
                if not inis:
                    blog_dot, blog_note = "⚪", "no blogs yet"
                elif n_draft:
                    blog_dot = "🟠"
                    blog_note = (f"{n_draft} of {len(inis)} day blog"
                                 f"{'s' if len(inis) != 1 else ''} still a draft to review")
                else:
                    blog_dot, blog_note = "🟢", f"all {len(inis)} day blogs reviewed"

                web_built = (d / "web" / "summaries").is_dir()
                trip_name = (trip_data.get("trip_name") or d.name).strip()
                is_adv    = trip_name in advertised

                # ONE icon, not a christmas tree — it captures the two things that
                # matter (is it public, is the review done); the tooltip has the rest.
                #   ✅ advertised + all blogs reviewed → done & public
                #   📢 advertised but drafts remain    → public, review still pending
                #   🟢 all reviewed, not advertised    → ready to publish
                #   🟠 drafts remain, not advertised   → in progress
                #   ⚪ no blogs yet
                all_reviewed = bool(inis) and n_draft == 0
                if is_adv and all_reviewed:
                    icon = "✅"
                elif is_adv:
                    icon = "📢"
                elif not inis:
                    icon = "⚪"
                else:
                    icon = "🟢" if all_reviewed else "🟠"
                base_text = f"{icon}  {d.name}"

                item = QTreeWidgetItem([base_text])
                item.setData(0, _PATH_ROLE, d)
                item.setData(0, _BASE_ROLE, base_text)

                fromdate  = trip_data.get("fromdate", "")
                todate    = trip_data.get("todate", "")
                title_nl  = trip_data.get("title_nl", "")
                title_en  = trip_data.get("title_en", "")
                message_nl = trip_data.get("message_nl", "")
                message_en = trip_data.get("message_en", "")
                date_range = f"{fromdate} – {todate}" if fromdate else ""

                web_line = "🌐 website built" if web_built else "— no website built"
                if is_adv:
                    adv_line = ("📢 advertised on the trip index "
                                "<span style='color:#888'>(per this machine's master)</span>")
                else:
                    adv_line = "— not advertised on the trip index"
                status_html = (
                    f"<p><b>Status</b><br>{blog_dot} {blog_note}<br>"
                    f"{web_line}<br>{adv_line}</p><hr>")
                tooltip_html = f"""
                    <html><body>
                        {status_html}
                        <p><b>{title_nl}</b></p><p>{message_nl}</p><p></p>
                        <p><b>{title_en}</b></p><p>{message_en}</p>
                        {f'<p><i>{date_range}</i></p>' if date_range else ''}
                    </body></html>
                """
                item.setToolTip(0, tooltip_html)
                self.tree.addTopLevelItem(item)
        else:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(No albums found)"]))

        self._refresh_highlights()

    def rebuild(self, sort_mode=None):
        """Re-enumerate the virttrips root and refresh the tree (e.g. after
        creation). sort_mode=None keeps the panel's current mode."""
        if sort_mode is not None:
            self._sort_mode = sort_mode
        self._populate_tree()

    def _refresh_highlights(self):
        virttrip_active = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        active_path = Path(virttrip_active) if virttrip_active else None
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            d         = item.data(0, _PATH_ROLE)
            base_text = item.data(0, _BASE_ROLE)
            if not d or base_text is None:
                continue
            if active_path and d == active_path:
                item.setText(0, f"▶ {base_text}")
                item.setFont(0, QFont("Consolas", 10, QFont.Weight.Bold))
                item.setForeground(0, Qt.GlobalColor.darkMagenta)
            else:
                item.setText(0, base_text)
                item.setFont(0, QFont("Consolas", 10))
                item.setForeground(0, self.tree.palette().color(self.tree.foregroundRole()))

    def _handle_click(self, item, column):
        trip_dir = item.data(0, _PATH_ROLE)
        if not trip_dir:
            return
        active = _config.get("virttrip", "virttripbiotadatabase", fallback="")
        if str(trip_dir) == active:
            return  # already active
        record_gui_usage(trip_dir)
        if not _config.has_section("virttrip"):
            _config.add_section("virttrip")
        _config.set("virttrip", "virttripbiotadatabase", str(trip_dir))
        _config.set("virttrip", "virttriprootdirectory", str(trip_dir.parent))
        trip_data = read_virttrip_ini(str(trip_dir))
        if trip_data:
            trip_name = trip_data.get("trip_name", "").strip()
            if trip_name:
                # Virtual trips publish under /trips/<name>/ — to a visitor a
                # virttrip is just a trip, and it continues a retired trip at
                # the same /trips/ URL where its blog + pictures already live.
                # (The Create dialog and the Retire flow already use /trips/;
                # this panel-click handler must not revert it to /virttrips/.)
                _config.set("virttrip", "virttripbiotawebroot", f"/trips/{trip_name}")
        persist_config()
        self._refresh_highlights()
        self.on_virttrip_selected(self.main_window)

    def _show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item:
            return
        trip_dir = item.data(0, _PATH_ROLE)
        if not trip_dir:
            return
        menu = QMenu()
        open_action = menu.addAction("Open Album Folder")
        action = menu.exec(QCursor.pos())
        if action == open_action and os.path.isdir(trip_dir):
            open_path(trip_dir)
