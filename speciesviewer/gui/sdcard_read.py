"""Read SD-cards dialog — the window that copies a memory card into a trip,
the day folders or the unfiled inbox, then runs tracklog geotagging and
place-name lookup on what arrived.

ONE JOB: drive one card read from the GUI. The copying itself lives in
batch/sv0ReadSDCard.py; this module only scans, previews, starts the worker
threads and reports.

INVARIANT: the GUI thread never walks the card or copies a file — scan, copy
and tracklog passes run on QThread workers and report back through signals.

Moved out of gui/trip_process.py (backlog 5.1); behaviour unchanged.
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QTabWidget, QWidget, QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMessageBox, QFrame, QApplication, QScrollArea)
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal
import logging
import re
import os, time
from datetime import datetime
from collections import Counter
from pathlib import Path

from utils.config import _config
from widgets.small_widgets import HTMLLabel
from widgets.trip_widgets import LabeledTextFieldQt, DirectoryFieldQt
from utils.gui_config import update_cache_gui_section, get_cache_gui_section
from gui.trip_overview import populate_trip_overview
from utils.raw_extensions import RAW, DXO_RAW, PICTURE, SELF_DESCRIBING
from utils.day_folders import strip_to_project_root
from utils.day_folders import strip_to_project_root


def _default_year(project_root: str) -> str:
    """Year for the default filename prefix: the leading year of the project-root
    folder name (e.g. '2026 Zeeland' -> '2026'), else the current year."""
    name = Path(project_root).name if project_root else ""
    m = re.match(r"(\d{4})", name)
    return m.group(1) if m else str(datetime.now().year)


def computer_offset_on(dates, local=None):
    """This computer's UTC offset ON the given days ("YYYY-MM-DD"), summer and
    winter time included: the zone most of the days have (a card read across
    the change goes with most of its days). None without a usable date.
    `local(datetime) -> timedelta` is for tests."""
    from collections import Counter
    from datetime import timezone
    local = local or (lambda d: d.astimezone().utcoffset())
    seen = Counter()
    for s in dates:
        try:
            d = datetime.strptime(str(s)[:10], "%Y-%m-%d").replace(hour=12)
        except ValueError:
            continue                  # "unknown date" and the like
        seen[local(d)] += 1
    return timezone(seen.most_common(1)[0][0]) if seen else None


def _clock(start: float, end: float) -> str:
    """'14:02 → 14:12' — wall-clock start and end of a step."""
    f = lambda t: datetime.fromtimestamp(t).strftime("%H:%M")
    return f"{f(start)} → {f(end)}"


class _SDCardCopyWorker(QObject):
    """Runs copy_card on a worker thread so a multi-GB card read keeps the UI
    responsive. Progress/finished are queued back to the GUI thread via signals."""
    progress = pyqtSignal(int, int, str)   # done, total, filename
    finished = pyqtSignal(dict, str)       # summary, errorstring ("" == clean)

    def __init__(self, source, dest, prefix="", bucket_by_date=False, tz_offset_hours=0.0,
                 write_sidecars=True, include_dates=None, trip_stamp=None,
                 include_names=None, newer_wins=False, on_day_copied=None):
        super().__init__()
        self._on_day_copied = on_day_copied     # the AI (gui.card_ai), or None
        self._newer_wins = newer_wins
        self._source = source
        self._dest = dest
        self._prefix = prefix
        self._bucket_by_date = bucket_by_date
        self._tz_offset_hours = tz_offset_hours
        self._write_sidecars = write_sidecars
        self._include_dates = include_dates
        self._trip_stamp = trip_stamp
        self._include_names = include_names
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            from batch.sv0ReadSDCard import copy_card
            summary = copy_card(
                self._source, self._dest, prefix=self._prefix,
                bucket_by_date=self._bucket_by_date, tz_offset_hours=self._tz_offset_hours,
                write_sidecars=self._write_sidecars, include_dates=self._include_dates,
                trip_stamp=self._trip_stamp,
                include_names=self._include_names,
                newer_wins=self._newer_wins,
                on_day_copied=self._on_day_copied,
                progress=lambda done, total, name: self.progress.emit(done, total, name),
                should_cancel=lambda: self._cancel,
            )
            # A pre-flight abort (not enough free space) comes back as a normal
            # return with summary["error"] set — route it down the error path so
            # the dialog shows it in red and no follow-on phase runs.
            self.finished.emit(summary, summary.get("error", ""))
        except Exception as e:
            self.finished.emit({}, str(e))


class _SDCardScanWorker(QObject):
    """Groups the card's pictures by capture day (no copy) on a worker thread so
    the dialog stays responsive while the card is walked / dates are read."""
    finished = pyqtSignal(list, int, dict, str)   # days [(date, count)], total, ignored, error

    def __init__(self, source, tz_offset_hours=0.0, preview=None):
        super().__init__()
        self._source = source
        self._tz_offset_hours = tz_offset_hours
        # Trip mode: {"dest", "prefix", "include_names"} — also work out what a
        # read would copy and which metadata is newer, here, off the GUI thread.
        self._preview = preview
        self.preview = {}

    def run(self):
        try:
            from batch.sv0ReadSDCard import scan_days, preview_read
            by_day = {} if self._preview else None
            days, total, ignored = scan_days(self._source,
                                             tz_offset_hours=self._tz_offset_hours,
                                             files_by_day=by_day)
            if self._preview:
                try:
                    self.preview = preview_read(by_day, **self._preview)
                except Exception:
                    self.preview = {}      # an indicator, never a reason to fail
            self.finished.emit(days, total, ignored, "")
        except Exception as e:
            self.finished.emit([], 0, {}, str(e))


class _TracklogAutoWorker(QObject):
    """Runs engine.tracklog_geotag.auto_geotag_entries on a worker thread so
    the Read dialog's silent per-day tracklog pass never blocks the GUI
    thread — parsing tracklogs and writing GPS/altitude via exiftool across
    several days can take real time, and this pass has no progress dialog to
    pump the event loop the way the geocode step's does. Mirrors
    _SDCardCopyWorker/_SDCardScanWorker."""
    finished = pyqtSignal(dict)

    def __init__(self, entries_by_day, files_by_key, root):
        super().__init__()
        self._entries_by_day = entries_by_day
        self._files_by_key = files_by_key
        self._root = root

    def run(self):
        from engine.tracklog_geotag import auto_geotag_entries
        try:
            summary = auto_geotag_entries(
                self._entries_by_day, self._files_by_key, root=self._root)
        except Exception as e:
            # Non-fatal by design — the Read itself must still complete and
            # move on to geocoding/Done, same as a per-picture geocode
            # failure already doesn't abort the Read.
            logging.getLogger("speciesviewer").warning(f"Tracklog auto-geotag failed: {e}")
            summary = {"days_matched": 0, "days_no_log": 0, "days_no_offset": 0,
                      "gps_files": 0, "altitude_files": 0, "pictures": 0, "logs_used": []}
        self.finished.emit(summary)


# RAW extensions PhotoLab opens — used to pick a sensible file to hand it when
# opening a freshly read day (a JPG would work too, but the RAW is the picture
# that will get the .dop recipe).
_PL_RAW_EXTS = DXO_RAW  # canonical table — see utils/raw_extensions.py


# Day-table columns. Named because a sixth column shifted every index below it,
# and the old magic numbers were spread across six methods.
(DAY_COL, FILES_COL, COPIED_COL, DELETED_COL, KEPT_COL, TO_COPY_COL, META_COL,
 TRACK_COL, AI_COL) = range(9)


class ReadSDCardDialog(QDialog):
    """Front-stage ingest (M0): copy pictures from a folder (e.g. a mounted
    SD-card) into the project pictures directory. No rename, no date-bucketing,
    no deletion from the card — pictures already copied are skipped so re-runs
    are safe."""
    def __init__(self, parent, menu_spec, days_mode=False, unfiled_mode=False,
                 trip_mode=False):
        super().__init__(parent)
        self.menu_spec = menu_spec
        # Trip mode: opened from Import Trip's pictures step. Everything that
        # decides what a picture is called and where it lands comes from the
        # trip and its SV INDEX backup, and is locked; the days are scanned as
        # soon as the window opens; the day choice stays yours.
        self.trip_mode = trip_mode and not (days_mode or unfiled_mode)
        self._auto_scanned = False
        self.passed_parent = parent
        self.days_mode = days_mode   # Days lane: copy into a YYYY-MM-DD folder + register
        # Unfiled lane: copy into <CurrentPictures>/<year>/<year> unfiled/ and claim
        # NOTHING — no trip, no Days registry entry. Emptying a stack of cards and
        # deciding which trip each day belongs to are separate decisions; this lane
        # exists so the first one asks no questions. See engine/unfiled.py.
        self.unfiled_mode = unfiled_mode
        self.logger = parent.logger
        self.setWindowTitle("Read Card into Unfiled" if unfiled_mode else
                            "Copy Day from Card" if days_mode else "Read Card or Folder")
        # A plain QDialog gets only a close button on Windows; min/max lets you
        # deliberately go full-screen for a big card instead of the dialog
        # having to guess a size that works for every case.
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.resize(700, 320)

        # Two tabs instead of one ever-growing column: "Input" (source, dest,
        # prefix, options — set once, rarely revisited) and "SD card" (the day
        # table, camera-clock warning, live progress and the read result —
        # everything that changes once you press Scan/Read). Splitting them
        # means neither tab alone needs anywhere near the height the combined
        # stack did, and each is wrapped in its own scroll area (see
        # _scrollable) so a card with many days or a long warning still never
        # gets clipped — see _fit_to_content.
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 12, 12)
        self.tabs = QTabWidget()
        outer_layout.addWidget(self.tabs, 1)

        def _scrollable(inner: QWidget) -> QScrollArea:
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.Shape.NoFrame)
            sc.setWidget(inner)
            return sc

        tab1 = QWidget()
        layout = QVBoxLayout(tab1)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)
        self.tabs.addTab(_scrollable(tab1), "Input")
        self._tab_input = tab1
        layout.addWidget(HTMLLabel(
            "<p><b>Read a card into the unfiled inbox</b><br>"
            "Copy pictures from a card into <i>&lt;year&gt; unfiled</i>, sorted into "
            "one folder per day. <b>No trip is chosen and nothing is claimed</b> — "
            "empty card after card now, and file the days under a trip later from "
            "<i>Unfiled pictures</i>. Your pictures on the card are never changed or "
            "deleted, each copy is verified, and pictures already copied are skipped, "
            "so you can re-run this safely. (To remember what was copied, SpeciesViewer "
            "keeps two small files of its own on the card.)</p>"
            if unfiled_mode else
            "<p><b>Read Card or Folder</b><br>"
            "Copy pictures from a folder — typically a mounted SD-card — into the "
            "project pictures directory. The filename prefix is added to each picture "
            "(e.g. <i>2026 Zeeland - IMG_1234.CR3</i>); leave it empty to keep names as-is. "
            "Your pictures on the card are never changed or deleted, each copy is "
            "verified, and pictures already copied are skipped, so you can re-run this "
            "safely. (To remember what was copied, SpeciesViewer keeps two small files "
            "of its own on the card.)</p>"
        ))

        # ── Source & options (framed, so it reads as one block separate from the
        # day selection below) ────────────────────────────────────────────────
        self.input_frame = QFrame()
        self.input_frame.setFrameShape(QFrame.Shape.StyledPanel)
        input_layout = QVBoxLayout(self.input_frame)
        input_layout.setContentsMargins(12, 12, 12, 12)
        input_layout.setSpacing(10)

        # Card source + prefix are remembered between runs (usually the same card
        # and the same trip), so the dialog reopens pre-filled.
        saved_source = get_cache_gui_section("sdcard_ingest", "source")
        saved_prefix = get_cache_gui_section("sdcard_ingest", "prefix")

        self.source_field = DirectoryFieldQt("Card / source folder", saved_source)
        input_layout.addWidget(self.source_field)

        # WP8: ASK THE TRIP where its pictures live. [project] picturedirectory
        # drifts every time another lane (Unfiled, another trip) is visited, and
        # stripping its trailing day is only correct while it still points inside
        # this trip. On 2026-08-17 it had drifted to D:\CurrentPictures\2026\
        # 2026-08-14 — a day directly under the YEAR — so stripping the date gave
        # the year folder and a whole card landed next to "2026 Goeree" instead of
        # inside it, with a green tick and no complaint. Same root cause as the
        # Trip Overview fix earlier that day (ed5ccb4), which covered mainwindow's
        # eight handlers but not this dialog.
        # trip.ini's picture_root is the authority; the old derivation stays as the
        # fallback for pre-WP8 trips and for days/unfiled mode, which have no trip.
        self.trip_picture_root = ""
        if not (days_mode or unfiled_mode):
            from utils.trip_identity import resolve_trip_picture_root
            trip_root = _config.get("trip", "tripbiotadatabase", fallback="")
            if trip_root:
                self.trip_picture_root = resolve_trip_picture_root(
                    trip_root, kind="trip", active_picturedirectory="") or ""

        dest_default = self.trip_picture_root or strip_to_project_root(
            _config.get("project", "picturedirectory", fallback=""))
        if days_mode:
            dest_default = _config.get("days", "location", fallback="") or dest_default
        if unfiled_mode:
            # Derived, not chosen: the whole point of this lane is that there is no
            # target to get wrong. Defaults to the CURRENT year and is re-pointed at
            # the year the card actually holds once "Scan days" has read the dates.
            from engine.unfiled import unfiled_root, currentpictures_root
            base = currentpictures_root(dest_default)
            dest_default = unfiled_root(base=base) if base else dest_default
        self.dest_field = DirectoryFieldQt(
            "Unfiled inbox (a YYYY-MM-DD folder is created here)" if unfiled_mode else
            "Days location (a YYYY-MM-DD folder is created here)" if days_mode else
            "Trip pictures folder",
            dest_default)
        input_layout.addWidget(self.dest_field)

        # Guard: the ✓ only says "this folder exists", which is exactly how a whole
        # card ended up NEXT TO the trip instead of inside it. When a trip is active,
        # say plainly whether the target is that trip's picture root.
        from gui import theme as _th
        # What this card has already been read into. Reading a card twice is
        # harmless (dedup is by content) but was silent, and with a dozen cards
        # going in in random order "have I done this one?" is the question that
        # actually gets asked.
        self.card_note = QLabel("")
        self.card_note.setWordWrap(True)
        self.card_note.setStyleSheet(f"color: {_th.INK_SOFT}; font-size: 9pt;")
        self.card_note.setVisible(False)
        input_layout.addWidget(self.card_note)
        self.source_field.textChanged(lambda *_: self._describe_card())

        self.dest_note = QLabel("")
        self.dest_note.setWordWrap(True)
        self.dest_note.setStyleSheet(f"color: {_th.WARN}; font-size: 9pt;")
        self.dest_note.setVisible(False)
        input_layout.addWidget(self.dest_note)
        if self.trip_picture_root:
            self.dest_field.textChanged(self._check_dest_is_trip)
            self._check_dest_is_trip()

        # Prefix default, in order: the OPEN TRIP's own prefix from trip.ini, then
        # whatever the last run used, then the year.
        #
        # trip.ini comes first because it is the only copy that survives this
        # machine. The prefix used to live solely in sv_gui_cache.json, so a
        # restore on a new laptop had nothing to go on — and re-reading the cards
        # with a slightly different prefix orphans every sidecar, which looks
        # exactly like a mass deletion. It also fixes a smaller everyday bug:
        # switching trips used to offer the PREVIOUS trip's prefix.
        trip_prefix = ""
        self._trip_recorded_prefix = ""
        self._trip_recorded_tz = ""
        if not (days_mode or unfiled_mode):
            trip_root_for_ini = _config.get("trip", "tripbiotadatabase", fallback="").strip()
            if trip_root_for_ini:
                from utils.trip_config import read_trip_ini
                trip_prefix = ((read_trip_ini(trip_root_for_ini) or {})
                               .get("picture_prefix", "") or "").strip()
                self._trip_recorded_prefix = trip_prefix
                self._trip_recorded_tz = ((read_trip_ini(trip_root_for_ini) or {})
                                          .get("tz_offset", "") or "").strip()
        self.prefix_field = LabeledTextFieldQt(
            "Filename prefix",
            trip_prefix or saved_prefix or _default_year(dest_default), inline=True
        )
        input_layout.addWidget(self.prefix_field)
        # Says what the files will be called, and flags only the two things
        # worth flagging: a character the filesystem will refuse, and a prefix
        # that differs from the rest of the trip. The second is informational on
        # purpose — a sub-trip gets its own prefix deliberately (so it is
        # findable in Explorer, and so a body that wraps its counter at 9999
        # still produces unique names), so this must never block.
        self.prefix_note = QLabel("")
        self.prefix_note.setWordWrap(True)
        self.prefix_note.setStyleSheet(f"color: {_th.INK_SOFT}; font-size: 9pt;")
        input_layout.addWidget(self.prefix_note)
        # LabeledTextFieldQt has no textChanged of its own — connect to the
        # QLineEdit it wraps, the way the rest of this dialog reaches .field.
        self.prefix_field.field.textChanged.connect(lambda *_: self._check_prefix())
        self._check_prefix()

        # Date bucketing: copy into dest/<YYYY-MM-DD>/ using EXIF capture time,
        # shifted by the timezone offset BEFORE the date is taken so near-midnight
        # shots land in the intended day.
        date_row = QHBoxLayout()
        self.bucket_checkbox = QCheckBox("Sort into date subfolders (YYYY-MM-DD)")
        # Remembered between runs; defaults ON (date-per-day is the normal layout).
        self.bucket_checkbox.setChecked(get_cache_gui_section("sdcard_ingest", "by_date") != "0")
        if days_mode or unfiled_mode:
            # A Day — and an unfiled day — IS a YYYY-MM-DD folder, so date-bucketing
            # is what makes the folder, not an option.
            self.bucket_checkbox.setChecked(True)
            self.bucket_checkbox.setEnabled(False)
        date_row.addWidget(self.bucket_checkbox)
        date_row.addSpacing(20)
        # Offset default, in order: the OPEN TRIP's own, then the last run's,
        # then zero — the same precedence as the prefix above, and for the same
        # reason: it decides which day folder a picture lands in, so it has to
        # survive this machine.
        self.tz_offset_field = LabeledTextFieldQt(
            "Timezone offset (hours)",
            self._trip_recorded_tz
            or get_cache_gui_section("sdcard_ingest", "tz_offset") or "0", inline=True)
        self.tz_offset_field.field.setMaximumWidth(60)
        _tz_tip = ("Added to the camera's clock only when choosing each photo's date "
                   "folder. Set it when the camera time is on a different timezone than "
                   "where you shot (e.g. left on home time, or not switched to summer "
                   "time). It changes which day a photo is filed under — not the time "
                   "stored in the photo.")
        for _w in (self.tz_offset_field, self.tz_offset_field.label, self.tz_offset_field.field):
            _w.setToolTip(_tz_tip)
        date_row.addWidget(self.tz_offset_field)
        date_row.addStretch()
        input_layout.addLayout(date_row)

        # XMP sidecars: the durable, RAW-immutable carrier for culling metadata
        # the grid reads/edits. Default ON, remembered between runs.
        self.sidecar_checkbox = QCheckBox("Write XMP sidecars for RAWs (date, GPS, EXIF — for culling)")
        self.sidecar_checkbox.setChecked(get_cache_gui_section("sdcard_ingest", "write_sidecars") != "0")
        input_layout.addWidget(self.sidecar_checkbox)

        # Reverse-geocode the copied pictures' GPS → write place names into the
        # sidecars (online; dedup-by-distance + throttle). Default off.
        self.location_checkbox = QCheckBox("Add location names (reverse-geocode GPS — needs internet)")
        self.location_checkbox.setChecked(get_cache_gui_section("sdcard_ingest", "add_location") == "1")
        input_layout.addWidget(self.location_checkbox)

        # Per-day tracklog auto-geotag: silently fills GPS/altitude from
        # <gpslogs_root>/<year>/<yyyymmdd>.log (or .gpx) BEFORE the location-names
        # step above runs, so a picture that gets GPS here also gets a place name
        # in the same Read (Add location names always re-reads GPS fresh off the
        # sidecar, so sequencing alone makes this cascade — see _finish_read).
        # No dialog, no typed offset: the camera's UTC offset is read from the
        # pictures themselves (EXIF OffsetTimeOriginal, carried into the sidecar
        # — engine.geo.camera_offset_from_dates). A day whose pictures don't
        # state one is skipped rather than guessed at.
        from utils.config import get_gpslogs_root
        self._gpslogs_root = get_gpslogs_root()
        self.tracklog_checkbox = QCheckBox("Geotag from each day's tracklog…")
        self.tracklog_checkbox.setChecked(bool(self._gpslogs_root))
        input_layout.addWidget(self.tracklog_checkbox)
        self.bucket_checkbox.toggled.connect(self._update_tracklog_checkbox_enabled)
        self._update_tracklog_checkbox_enabled()

        # The AI looks at each day while the card copies the next one (a card is
        # slow, the AI uses the processor): AI… on a day then opens at once.
        # Only with the AI models installed, and only into day folders.
        self.ai_checkbox = QCheckBox("Let the AI look at the pictures while copying "
                                     "(AI… then opens at once)")
        self.ai_checkbox.setChecked(get_cache_gui_section("sdcard_ingest", "ai") != "0")
        # Asked as a flag, never with isVisible(): the box sits on the Input tab,
        # and Read switches to the SD card tab first — a widget on a hidden tab
        # says "not visible", which once kept the AI from ever starting.
        try:
            from engine import ai_models
            self._ai_available = ai_models.available()
        except Exception:
            self._ai_available = False
        self.ai_checkbox.setVisible(self._ai_available)
        input_layout.addWidget(self.ai_checkbox)
        # … and, when the read is done, open the pictures with AI… on top, ready
        # for Accept… — nothing is written until you press it. Off unless ticked.
        self.ai_open_checkbox = QCheckBox(
            "When the read is done, open the pictures with AI… ready to accept")
        self.ai_open_checkbox.setChecked(
            get_cache_gui_section("sdcard_ingest", "ai_open") == "1")
        self.ai_open_checkbox.setToolTip(
            "The pictures open in the culling viewer with the AI's suggestion on them "
            "(names, and a bin on what could go). Nothing is written until you press "
            "Accept… in AI….")
        self.ai_open_checkbox.setVisible(self._ai_available)
        input_layout.addWidget(self.ai_open_checkbox)
        self.bucket_checkbox.toggled.connect(self._update_ai_checkbox_enabled)
        self.ai_checkbox.toggled.connect(self._update_ai_checkbox_enabled)
        self._update_ai_checkbox_enabled()

        # Re-apply the culling on a restore. A card holds every frame ever shot,
        # including the ones deleted during the trip, so a plain re-read undoes
        # a fortnight of culling. The backup recorded which pictures each day
        # still held; ticked, those are the only ones copied.
        self._survivors = {}
        self.recull_checkbox = QCheckBox("Skip pictures deleted during the trip")
        self.recull_checkbox.setToolTip(
            "Uses this trip's backup to copy only the pictures a day still had "
            "when it was last backed up.\nA day the backup has never seen is "
            "always copied in full.")
        self.recull_checkbox.setChecked(True)
        self.recull_checkbox.setVisible(False)
        input_layout.addWidget(self.recull_checkbox)
        self._refresh_recull()

        layout.addWidget(self.input_frame)
        layout.addStretch(1)

        # ── Tab 2: "SD card" — the day picker, camera-clock warning, live
        # progress, and the eventual read result. Everything Scan/Read change,
        # which is why on_scan/on_read switch to it automatically.
        tab2 = QWidget()
        # The weaver keeps you company where the long copy runs: a collection
        # is woven strand by strand (see docs/illustrations.md).
        card_row = QHBoxLayout(tab2)
        # Same layout as Create Excel / Rebuild all (2026-10-21): 220 px, centred
        # beside the column — it stays centred as the tab grows after a scan.
        card_row.setContentsMargins(20, 20, 20, 20)
        card_row.setSpacing(24)
        from gui.wait_picture import picture_label
        weaver = picture_label("weaver", 220, self.devicePixelRatioF())
        if weaver is not None:
            card_row.addWidget(weaver, alignment=Qt.AlignmentFlag.AlignVCenter)
        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(10)
        card_row.addLayout(card_layout, 1)
        self.tabs.addTab(_scrollable(tab2), "SD card")
        self._tab_card = tab2

        # Live progress, so a long scan/copy is visible at a glance — "running
        # while you fetch a cup of tea". Hidden when idle; the one-line result
        # still lands in the status line below.
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        self.progress_label.setStyleSheet("font-style: italic;")
        self.progress_label.setVisible(False)
        card_layout.addWidget(self.progress_label)

        # ── Day picker: "Scan days" lists each capture-day on the card with its
        # file count so you can untick days you don't want before copying. Its own
        # framed block; hidden until you scan (if you never scan, Read copies every
        # day as before). Greyed after a completed run to show it's done. ───────
        self.days_container = QFrame()
        self.days_container.setFrameShape(QFrame.Shape.StyledPanel)
        days_layout = QVBoxLayout(self.days_container)
        days_layout.setContentsMargins(12, 10, 12, 10)
        days_layout.setSpacing(6)
        days_title = QLabel("Days on the card — tick the ones to copy")
        _tf = days_title.font(); _tf.setBold(True); days_title.setFont(_tf)
        days_layout.addWidget(days_title)
        self.all_days_checkbox = QCheckBox("All days")
        self.all_days_checkbox.setTristate(True)
        self.all_days_checkbox.setChecked(True)
        self.all_days_checkbox.clicked.connect(self._on_all_days_clicked)
        days_layout.addWidget(self.all_days_checkbox)
        self.days_table = QTableWidget(0, 9)
        self.days_table.setHorizontalHeaderLabels(
            ["Day", "Files", "Copied", "Deleted", "Kept", "To copy",
             "Metadata on this source", "Tracklog", "AI"])
        self.days_table.horizontalHeaderItem(AI_COL).setToolTip(
            "The AI looking at each day while the card copies the next one — "
            "AI… on that day then opens at once")
        self.days_table.setColumnHidden(AI_COL, True)   # shown when the AI runs
        for _col, _tip in ((TO_COPY_COL, "Pictures a Read would add to this day: not "
                                         "here yet, not culled on the trip, and not "
                                         "deleted on this computer"),
                           (META_COL, "Sidecars and rendered JPGs on this source "
                                      "compared with the ones here: newer ones are "
                                      "taken (the older copy is kept in "
                                      ".trash/replaced), older ones are left alone")):
            self.days_table.horizontalHeaderItem(_col).setToolTip(_tip)
        # Shown only in trip mode, where the read compares with the trip.
        self.days_table.setColumnHidden(TO_COPY_COL, not self.trip_mode)
        self.days_table.setColumnHidden(META_COL, not self.trip_mode)
        for _col, _tip in ((FILES_COL, "Pictures on the card for this day — every "
                                       "frame shot, including the ones culled since"),
                           (COPIED_COL, "What is on disk for this day — filled "
                                        "from the destination when you Scan, and "
                                        "from the run when you Read. A tick means "
                                        "the day is fully there; \"60 of 104\" "
                                        "means it is not"),
                           (DELETED_COL, "Deleted during the trip — the card still "
                                         "holds them, this trip's backup says they went"),
                           (KEPT_COL, "Still there at the last backup — the ones a "
                                      "re-read copies back")):
            self.days_table.horizontalHeaderItem(_col).setToolTip(_tip)
        self.days_table.verticalHeader().setVisible(False)
        self.days_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.days_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.days_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Counts and the date are narrow — size them to their contents, and let
        # the last column (the tracklog file names) take the rest of the width.
        _hdr = self.days_table.horizontalHeader()
        _hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        _hdr.setStretchLastSection(True)
        self.days_table.itemChanged.connect(self._on_day_item_changed)
        # Vertical Expanding + the stretch below: a taller dialog becomes more
        # visible days rather than empty space under the block. How far it may
        # grow is bounded in _size_days_table (never past the rows it has).
        self.days_table.setSizePolicy(QSizePolicy.Policy.Preferred,
                                      QSizePolicy.Policy.Expanding)
        days_layout.addWidget(self.days_table, 1)
        # Camera-clock check, filled by _check_camera_clock after a scan. Hidden
        # unless there is something to say — a note that is always there stops
        # being read.
        self.clock_note = QLabel("")
        self.clock_note.setWordWrap(True)
        self.clock_note.setTextFormat(Qt.TextFormat.RichText)
        self.clock_note.setStyleSheet(
            f"color: {_th.WARN}; font-size: 9pt; padding: 6px 8px;"
            f"border: 1px solid {_th.WARN}; border-radius: 4px;")
        self.clock_note.setVisible(False)
        days_layout.addWidget(self.clock_note)
        self.days_container.setVisible(False)
        # Most of a manual enlargement goes to the day table (stretch 4 against
        # the trailing stretch 1 below), which is what a big screen is for. Not
        # all of it: the trailing stretch must keep a share, or once the table
        # hits its row-count ceiling the leftover would have nowhere to go and
        # would open a gap inside the block instead.
        card_layout.addWidget(self.days_container, 4)

        # Extra vertical space (when the dialog is enlarged) collects here, so the
        # blocks stay compact at the top and the buttons stay at the bottom rather
        # than the widgets spreading apart.
        card_layout.addStretch(1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        card_layout.addWidget(self.status_label)
        # The AI's own line: it finishes at its own pace, before or after the
        # copy's Done — on the status line it was overwritten by that Done.
        self.ai_status_label = QLabel("")
        self.ai_status_label.setWordWrap(True)
        self.ai_status_label.setVisible(False)
        card_layout.addWidget(self.ai_status_label)

        # "What to do next" note after a read. Colours come from the theme: the
        # hard-coded pale-blue card was invisible on the dark theme — a white box
        # with near-white text, which read as an empty panel.
        from gui import theme
        self.pl_hint = QLabel("")
        self.pl_hint.setWordWrap(True)
        self.pl_hint.setVisible(False)
        self.pl_hint.setStyleSheet(
            f"background:{theme.ACCENT_SOFT}; border:1px solid {theme.ACCENT_SOFT_BORDER};"
            f"color:{theme.ACCENT_INK_SOFT}; border-radius:4px;"
            "padding:6px; font-size:11px;")
        card_layout.addWidget(self.pl_hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.scan_btn = QPushButton("Scan days")
        self.read_btn = QPushButton("Read")
        self.view_btn = QPushButton("View pictures")
        self.view_btn.setEnabled(False)  # enabled once a read produced files
        # A freshly ingested day has no .dop files, and SpeciesViewer will not
        # invent them (PhotoLab only recognises recipes it authored itself). So
        # the moment after a read is exactly when to say: let PhotoLab write them.
        self.pl_btn = QPushButton("Open day in PhotoLab")
        self.pl_btn.setEnabled(False)
        self.pl_btn.setToolTip(
            "Open this day in DxO PhotoLab so it can create the .dop recipes "
            "(select all with Ctrl-A, then write sidecars)")
        # Fills missing GPS (a day the camera's GPS wasn't enabled) and/or
        # altitude (which the camera never records) for the pictures just
        # copied, matched to a GPX/OM-1-log tracklog by time. Same enablement
        # pattern as view_btn/pl_btn: nothing to act on until a read finishes.
        self.geotag_btn = QPushButton("Geotag from tracklog…")
        self.geotag_btn.setEnabled(False)
        self.geotag_btn.setToolTip(
            "Fill missing GPS/altitude for the pictures just read from a "
            "GPX/GPS tracklog file, matched by time")
        self.close_btn = QPushButton("Close")
        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.read_btn)
        btn_row.addWidget(self.view_btn)
        btn_row.addWidget(self.pl_btn)
        btn_row.addWidget(self.geotag_btn)
        btn_row.addWidget(self.close_btn)
        outer_layout.addLayout(btn_row)
        self._btn_row = btn_row   # used by _fit_to_content to size around it
        # Connected only now that _tab_input/_tab_card/_btn_row all exist —
        # QTabWidget.addTab() above fires currentChanged as soon as the first
        # tab is added, which is long before those exist.
        self.tabs.currentChanged.connect(lambda _i: self._fit_to_content())

        self._last_targets = []     # picture paths from the most recent read
        self._last_dest = ""        # destination root (used as preview cache dir)
        self._thread = None
        self._worker = None
        self._scan_thread = None
        self._scan_worker = None
        self._tracklog_thread = None
        self._tracklog_worker = None
        self._ai = None             # gui.card_ai.CardAiWorker during a read
        self._scanned_source = ""   # source the day table was built from
        self._preview_dlg = None    # the open culling grid (single instance)

        self.scan_btn.clicked.connect(self.on_scan)
        self.read_btn.clicked.connect(self.on_read)
        self.view_btn.clicked.connect(self.on_view)
        self.pl_btn.clicked.connect(self.on_open_in_photolab)
        self.geotag_btn.clicked.connect(self.on_geotag_from_tracklog)
        self.close_btn.clicked.connect(self.reject)
        self._close_connected = True   # True = Close, False = Cancel
        if self.trip_mode:
            self._lock_for_trip()

        # Without this the dialog opens at the hardcoded resize(700, 320)
        # above and stays there until something else (Scan, a tab switch)
        # happens to trigger a resize — the Input tab's fields and checkboxes
        # don't actually fit in 320px, so it opened clipped every time.
        self._fit_to_content()

    def on_open_in_photolab(self):
        """Open the day just read in DxO PhotoLab, so IT authors the .dop files.

        This is the supported direction and the reason the button exists here:
        SpeciesViewer edits a PhotoLab recipe but never creates one, because
        PhotoLab matches recipes by a UUID from its own catalog and treats an
        unknown one as a virtual copy. Until PhotoLab has written sidecars for a
        day, colour and crop live in the .xmp alone.
        """
        from pathlib import Path
        from gui.large_image_view import dxo_executable, handover_around_launch
        from utils.open_path import launch_app_with_file

        # Open a PICTURE, not a folder, and take it from what was actually
        # copied. Two things went wrong with the folder:
        #   * _last_dest is the DAYS LOCATION ("…/2026 Zomer"), the parent of the
        #     dated day folders — not the day itself;
        #   * PhotoLab is single-instance, so handing an already-running PL a
        #     folder path just raised its existing window, still showing the SD
        #     card it had open. Passing a file is the route the large view already
        #     uses successfully, and it makes PL select that picture's folder.
        targets = [t for t in (self._last_targets or []) if Path(t).is_file()]
        if not targets:
            QMessageBox.information(self, "Open in PhotoLab",
                                    "Read a day first — there is nothing to open yet.")
            return
        raws = [t for t in targets if Path(t).suffix.lower() in _PL_RAW_EXTS]
        first = sorted(raws or targets)[0]
        folder = str(Path(first).parent)
        exe = dxo_executable()
        if not Path(exe).exists():
            QMessageBox.warning(
                self, "DxO PhotoLab not found",
                f"Could not find DxO PhotoLab at:\n\n{exe}\n\n"
                "Set the correct path in File ▸ Settings… ▸ Lightroom & PhotoLab "
                "▸ PhotoLab location.")
            return
        # Crop/exposure edits held back while PhotoLab ran go to its .dop now —
        # before it starts, or just after it has re-read the folder (as in the
        # Editor; docs/plans/photolab_sidecar_fight.md).
        handover_around_launch(first)
        if not launch_app_with_file(exe, first):
            QMessageBox.warning(self, "DxO PhotoLab",
                                f"Could not open PhotoLab for:\n{first}")
            return
        self.logger.info(
            f"Opened day in DxO PhotoLab: {folder} (via {Path(first).name})")
        self.status_label.setText(
            f"Opened {Path(folder).name} in PhotoLab — select all (Ctrl-A) and write "
            "sidecars to create the .dop recipes. If PhotoLab was already running it "
            "may still show its previous folder; browse to this day.")

    def _build_tracklog_entries(self):
        """RAW+XMP entry list for tracklog geotag, built from this read's
        targets — shared by the manual "Geotag from tracklog…" button and
        the automatic per-day pass (_auto_geotag_from_tracklogs). A RAW with
        no .xmp sidecar has no XMP-based write target and nothing to
        correlate cheaply, so it's skipped — mirrors
        _add_location_names's target-building exactly.

        Each entry also carries "day" = the destination day-folder's name
        (`Path(target).parent.name`, same convention `_days_copied_count`
        already uses), for the automatic pass to know which day's log file
        to try — None when the file didn't land in a YYYY-MM-DD folder
        (date-subfolder bucketing off). The manual button ignores it.

        Returns (entries, files_by_key)."""
        from utils import days_registry
        targets = [t for t in (self._last_targets or []) if Path(t).is_file()]
        xmps = []
        for t in targets:
            p = Path(t)
            ext = p.suffix.lower()
            # Same rule as the day-folder dialog: a RAW is stamped through its
            # sidecar, a HEIC/PNG in the picture itself (that is where its GPS
            # lives, and where PhotoLab and Lightroom read it).
            if ext in _PL_RAW_EXTS and p.with_suffix(".xmp").exists():
                target = str(p.with_suffix(".xmp"))
            elif ext in SELF_DESCRIBING:
                target = str(p)
            else:
                continue
            day = p.parent.name if days_registry.is_day_folder_name(p.parent.name) else None
            xmps.append((p.stem, target, day))
        if not xmps:
            return [], {}
        from gui.geolocate import batch_datetime_gps
        meta = batch_datetime_gps([x[1] for x in xmps])
        entries, files_by_key = [], {}
        for stem, xmp, day in xmps:
            m = meta.get(os.path.normcase(os.path.normpath(xmp)))
            if not m:
                continue
            entries.append({"key": stem, "date": m["date"], "has_gps": m["has_gps"],
                            "cur_alt": m.get("cur_alt"), "day": day})
            files_by_key[stem] = [xmp]
        return entries, files_by_key

    def on_geotag_from_tracklog(self):
        """Fill missing GPS/altitude for the pictures just read, matched to a
        GPX/OM-1-log tracklog by time."""
        entries, files_by_key = self._build_tracklog_entries()
        if not entries:
            QMessageBox.information(self, "Geotag from Tracklog",
                                    "No RAW+XMP pictures from this read to geotag.")
            return
        from engine.tracklog_geotag import FilesSource
        from gui.tracklog_geotag_dialog import TracklogGeotagDialog
        source = FilesSource(entries, files_by_key, "this read")
        TracklogGeotagDialog(self, source, initial_dir=self.source_field.get_path()).exec()

    def _on_cancel(self):
        self._cancelled = True   # on_finished still fires with the partial summary
        self.status_label.setText("Cancelling…")
        self._stop_worker()
        self.read_btn.setEnabled(True)
        self._restore_close_btn()

    def _restore_close_btn(self):
        if not self._close_connected:
            self.close_btn.clicked.disconnect(self._on_cancel)
            self.close_btn.clicked.connect(self.reject)
            self.close_btn.setText("Close")
            self._close_connected = True

    def _stop_worker(self):
        # Don't let the dialog (and its QThread) be destroyed while the copy
        # worker is still running — that crashes Qt ("QThread: Destroyed while
        # thread is still running"). Cancel and wait for a clean stop first.
        if self._thread is not None and self._thread.isRunning():
            if self._worker is not None:
                self._worker.cancel()
            self._thread.quit()
            self._thread.wait(5000)
        # The day-scan thread is short-lived but must also finish cleanly before
        # the dialog (and its QThread) is destroyed.
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(5000)
        # Same for the tracklog auto-pass — no cancel() (it's meant to be a
        # short, silent pass), just wait for it to actually finish rather than
        # destroy the thread out from under it.
        if self._tracklog_thread is not None and self._tracklog_thread.isRunning():
            self._tracklog_thread.wait(5000)
        # The AI stops between two pictures; a detector call takes under a second.
        if self._ai is not None and self._ai.isRunning():
            self._ai.stop()
            self._ai.wait(15000)

    # Independent taskbar entry: Windows hides any window with a native
    # "owner" HWND from the taskbar regardless of Qt's WindowType, and this
    # dialog keeps its Qt parent intentionally — see
    # gui.window_state.give_independent_taskbar_entry for why that's patched
    # after the fact instead of restructuring the parent/child relationship.
    # Worth having here specifically because Read SD-cards now runs
    # non-modal (see on_view above), so it's exactly the kind of long-lived
    # window you want to find again while working elsewhere.
    # Where the window's centre was when it last closed (this session), so it
    # opens again where you put it rather than where Windows drops it (top-left,
    # after the taskbar fix's hide/show). None = centre on the main window.
    _last_centre = None

    def _place(self):
        """Put the window's centre where it was last time (or on the main
        window), kept on a visible screen."""
        from PyQt6.QtCore import QPoint
        centre = ReadSDCardDialog._last_centre
        if centre is None:
            host = self.parentWidget().window() if self.parentWidget() else None
            if host is None:
                return
            centre = host.frameGeometry().center()
        frame = self.frameGeometry()
        frame.moveCenter(QPoint(centre))
        screen = (QGuiApplication.screenAt(frame.center()) or self.screen()
                  or QGuiApplication.primaryScreen())
        if screen is not None:
            avail = screen.availableGeometry()
            frame.moveLeft(max(avail.left(), min(frame.left(),
                                                 avail.right() - frame.width() + 1)))
            frame.moveTop(max(avail.top(), min(frame.top(),
                                               avail.bottom() - frame.height() + 1)))
        self.move(frame.topLeft())

    def hideEvent(self, event):
        if not getattr(self, "_placing", False):
            ReadSDCardDialog._last_centre = self.frameGeometry().center()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_taskbar_fix_scheduled", False):
            return
        self._taskbar_fix_scheduled = True
        from PyQt6.QtCore import QTimer
        from gui.window_state import give_independent_taskbar_entry
        self._place()

        def taskbar_then_place():
            # The taskbar fix hides and re-shows the window, and Windows may
            # move it while doing so — place it again afterwards.
            self._placing = True
            try:
                give_independent_taskbar_entry(self)
            finally:
                self._placing = False
            self._place()
        QTimer.singleShot(0, taskbar_then_place)
        # Trip mode scans at once: which days a card or dump holds is the first
        # thing you need to see, and the choice is yours to make from there.
        if self.trip_mode and not self._auto_scanned:
            self._auto_scanned = True
            src = self.source_field.get_path()
            if src and os.path.isdir(src):
                QTimer.singleShot(0, self.on_scan)

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    def reject(self):
        # The Close button → reject() does not fire closeEvent, and with exec()
        # the dialog is dropped right after, so wait here too.
        self._stop_worker()
        super().reject()

    def _tz_offset(self):
        try:
            return float(self.tz_offset_field.get_text() or "0")
        except ValueError:
            return 0.0

    @staticmethod
    def _fmt_offset(tz) -> str:
        """A datetime.timezone as 'UTC+02:00' — the form the camera menu uses."""
        mins = round(tz.utcoffset(None).total_seconds() / 60)
        sign = "+" if mins >= 0 else "-"
        mins = abs(mins)
        return f"UTC{sign}{mins // 60:02d}:{mins % 60:02d}"

    def _card_camera_offset(self, source):
        """The UTC offset the camera was SET to, sampled from the card.

        One exiftool call over a handful of files — the offset is a single camera
        setting, so a sample answers it. None when the files don't record one (a
        pre-EXIF-2.31 camera) or when they disagree (two bodies on different
        zones), in which case there is nothing to compare and we stay quiet."""
        import json
        from engine.geo import camera_offset_from_dates
        from engine.exif_writer import run_exiftool_files
        try:
            files = []
            for p in Path(source).rglob("*"):
                if p.is_file() and p.suffix.lower() in _PL_RAW_EXTS | {".jpg", ".jpeg"}:
                    files.append(str(p))
                    if len(files) >= 8:
                        break
            if not files:
                return None
            out = run_exiftool_files(
                ["-j", "-DateTimeOriginal", "-OffsetTimeOriginal"], files).stdout
            rows = json.loads(out) if out.strip() else []
            return camera_offset_from_dates(
                [f"{r.get('DateTimeOriginal', '')}{r.get('OffsetTimeOriginal', '')}"
                 for r in rows])
        except Exception as e:                 # a check must never break the scan
            self.logger.debug(f"camera clock check skipped: {e}")
            return None

    def _check_camera_clock(self, days):
        """Compare the camera's own time zone with a second opinion, and say so.

        The camera's zone is a manual setting that is easy to forget after
        travelling — but forgetting it does NOT spoil geotagging, because
        changing a zone moves the clock and the recorded offset together, so UTC
        survives (measured on 2026 Goeree: 2 m). What it does spoil is the LOCAL
        time written into every picture, and which day-folder a shot near
        midnight lands in. So this reports rather than blocks.

        Second opinion, in order: the day's own tracklog header (the phone, which
        updates its zone by itself), else this computer's zone ON THE PICTURES'
        DATES (computer_offset_on: summer time in August even when the card is
        read in November — it once set -1 h for that). The laptop is the weaker
        source — it says where you live, not where you were — so its wording is
        hedged."""
        import datetime as _dt
        from engine.tracklog_geotag import find_day_tracklog
        self.clock_note.setVisible(False)
        source = self.source_field.get_path()
        if not source or not days:
            return
        cam = self._card_camera_offset(source)
        if cam is None:
            return

        true_tz, from_log = None, False
        if self._gpslogs_root:
            from engine.geo import om1_log_utc_offset
            seen = set()
            for date_str, _count in days:
                log_path = find_day_tracklog(date_str, self._gpslogs_root)
                tz = om1_log_utc_offset(log_path) if log_path else None
                if tz is not None:
                    seen.add(tz.utcoffset(None))
            if len(seen) == 1:
                true_tz, from_log = _dt.timezone(seen.pop()), True
        if true_tz is None:
            true_tz = computer_offset_on([d for d, _n in days])

        if true_tz is None:
            return

        # Set the field WHENEVER both offsets are known, including when they
        # agree — the delta is then 0. Returning early on agreement would leave
        # the value remembered from the last card in place, so the day after
        # correcting the camera's zone the folders would still be shifted by the
        # old correction. The note stays quiet; only the value is kept honest.
        delta_h = (true_tz.utcoffset(None) - cam.utcoffset(None)).total_seconds() / 3600
        self.tz_offset_field.field.setText(f"{delta_h:g}")
        if not delta_h:
            return
        who = ("this day's tracklog says the local zone was"
               if from_log else "this computer's zone on those dates is")
        self.clock_note.setText(
            f"<b>⚠ The camera's clock is set to another time zone.</b><br>"
            f"The pictures say the camera was on <b>{self._fmt_offset(cam)}</b>, "
            f"but {who} <b>{self._fmt_offset(true_tz)}</b> — a difference of "
            f"{delta_h:+.0f} hour(s).<br>"
            f"Positions are unaffected: geotagging uses the camera's own UTC, "
            f"which stays correct when only the zone is stale. What is off by "
            f"{delta_h:+.0f} h is the local time recorded in each picture. "
            f"Timezone offset below has been set to <b>{delta_h:g}</b> so the "
            f"day folders still follow real local time.")
        self.clock_note.setVisible(True)
        self.logger.info(
            f"Camera clock: pictures say {self._fmt_offset(cam)}, "
            f"{'tracklog' if from_log else 'this computer'} says "
            f"{self._fmt_offset(true_tz)} — timezone offset set to {delta_h:g} h.")

    # An ALLOW-list, not a ban-list. Letters and digits (any language — isalnum
    # accepts "Curaçao" and "Zuid-Afrika" alike), plus space, hyphen and
    # underscore, which is everything the real prefixes have ever used.
    #
    # A ban-list would have to guess at what hurts. The period is the clearest
    # reason not to guess: it is perfectly legal in a filename, so no filesystem
    # would object, and it would quietly break every piece of code that reads an
    # extension or a stem — which in this program is most of them. The other
    # quiet ones are the delimiters the CSVs and the web layer use.
    _PREFIX_EXTRA_OK = " -_"

    def _check_prefix(self):
        """Preview the resulting filename; refuse anything but letters, digits,
        space, hyphen and underscore, and note a prefix that differs from the
        one this trip already uses."""
        from batch.sv0ReadSDCard import target_name
        from gui import theme as _th
        raw = self.prefix_field.get_text() or ""
        example = target_name("IMG_1234.CR3", raw)
        bad = sorted({c for c in raw
                      if not (c.isalnum() or c in self._PREFIX_EXTRA_OK)})
        if bad:
            shown = " ".join(repr(c) if c.isspace() else c for c in bad)
            hint = ("  A period would break every part of the program that reads "
                    "a file extension." if "." in bad else "")
            self.prefix_note.setText(
                f"⚠ {shown} cannot be used in a filename prefix — letters, "
                f"digits, spaces, - and _ only.{hint}")
            self.prefix_note.setStyleSheet(f"color: {_th.WARN}; font-size: 9pt;")
            self.prefix_note.setVisible(True)
            self._set_read_enabled(False)
            return
        note = f"Files will be named:  {example}"
        known = (self._trip_recorded_prefix or "").strip()
        # Upper/lower case does not make a different prefix: target_name treats
        # "2026 madagascar" and "2026 Madagascar" as the same one.
        if known and raw.strip().rstrip(" -") \
                and raw.strip().rstrip(" -").lower() != known.lower():
            note += (f"    ·  this trip's other days use “{known}” — fine for a "
                     f"sub-trip, just checking it is deliberate.")
        self.prefix_note.setText(note)
        self.prefix_note.setStyleSheet(f"color: {_th.INK_SOFT}; font-size: 9pt;")
        self.prefix_note.setVisible(True)
        self._set_read_enabled(True)

    def _set_read_enabled(self, ok):
        """Read is refused outright on a bad prefix — the point of an allow-list
        is that the name never gets made. Guarded with getattr because
        _check_prefix runs once during construction, before the buttons exist."""
        btn = getattr(self, "read_btn", None)
        if btn is not None:
            btn.setEnabled(ok)

    def _refresh_recull(self):
        """Offer to re-apply the culling, when this trip has a backup to read it
        from. Hidden otherwise — there is nothing to promise."""
        self._survivors = {}
        self._survivors_from_cache = False
        box = getattr(self, "recull_checkbox", None)
        if box is None:
            return
        try:
            from gui.work_backup_dialog import backup_location
            from engine import work_restore as wr
            dest = backup_location()
            trip_root = _config.get("trip", "tripbiotadatabase", fallback="").strip()
            if dest and trip_root and self.trip_picture_root:
                self._survivors = wr.survivor_filter(
                    dest, trip_root=trip_root,
                    picture_root_name=Path(self.trip_picture_root).name)
            # The SV INDEX card is usually NOT in the machine right now — with
            # one reader, inserting the SD-card takes it out. The restore screen
            # remembers the list while the card IS readable, and that copy is
            # what makes the filter work at read time.
            if not self._survivors and trip_root:
                self._survivors = wr.load_survivor_cache(trip_root)
                self._survivors_from_cache = bool(self._survivors)
        except Exception:
            self._survivors = {}
        n = len(self._survivors)
        box.setVisible(bool(n))
        if n:
            kept = sum(len(v) for v in self._survivors.values())
            box.setText(f"Skip pictures deleted during the trip — copy only the "
                        f"{kept} picture(s) kept across {n} day(s)")
        self._refresh_cull_columns()

    def _describe_card(self):
        """Say what the inserted card has already been read into.

        A different TRIP is the one worth colouring: on a restore that is the
        card that would file a Brazil day into Goeree. Reading the same card
        into the same trip again is normal and merely informative — it is how a
        card being topped up mid-trip is meant to work.
        """
        from gui import theme as _th
        src = self.source_field.get_path()
        if not src or not Path(src).is_dir():
            self.card_note.setVisible(False)
            return
        try:
            from batch.sv0ReadSDCard import card_history
            hist = card_history(src)
        except Exception:
            hist = []
        if not hist:
            self.card_note.setVisible(False)
            return
        last = hist[0]
        when = (last.get("when") or "")[:16].replace("T", " ")
        trip = last.get("trip") or last.get("trip_id") or "another trip"
        n = last.get("copied", 0)
        mine = (self._trip_stamp() or {}).get("trip_id")
        theirs = last.get("trip_id")
        if mine and theirs and mine != theirs:
            self.card_note.setText(
                f"⚠ This card was last read into a DIFFERENT trip — “{trip}” on "
                f"{when} ({n} picture(s)). Reading it here files those pictures "
                f"into the open trip instead.")
            self.card_note.setStyleSheet(f"color: {_th.WARN}; font-size: 9pt;")
        else:
            self.card_note.setText(
                f"This card was last read into “{trip}” on {when} "
                f"({n} picture(s) copied). Reading it again copies anything not "
                f"already on disk — including pictures deleted since.")
            self.card_note.setStyleSheet(f"color: {_th.INK_SOFT}; font-size: 9pt;")
        self.card_note.setVisible(True)

    def _trip_stamp(self):
        """Identity written onto the card with a read, so it can say later which
        trip it went into. None outside a trip (Days/Unfiled have no identity to
        claim, and stamping one would be a lie)."""
        if not self.trip_picture_root:
            return None
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="").strip()
        if not trip_root:
            return None
        try:
            from utils.trip_config import read_trip_ini
            data = read_trip_ini(trip_root) or {}
        except Exception:
            data = {}
        return {"trip_id": data.get("trip_id") or Path(trip_root).name,
                "trip": Path(self.trip_picture_root).name}

    def _check_dest_is_trip(self):
        """Warn when the card would land outside the active trip's picture root.

        A green ✓ only means the folder exists. It said nothing when the target
        was the YEAR folder rather than the trip inside it, so 352 pictures were
        filed one level too high without a word. This names the mismatch before
        Read, and does not block it — reading into another folder on purpose is
        legitimate (a fresh trip, a scratch import)."""
        want = Path(self.trip_picture_root)
        try:
            got = Path(self.dest_field.get_path())
            same = os.path.normcase(os.path.normpath(str(got))) == \
                   os.path.normcase(os.path.normpath(str(want)))
        except (OSError, ValueError):
            same = False
        if same:
            self.dest_note.setVisible(False)
            return
        self.dest_note.setText(
            f"⚠ This is not the pictures folder of the open trip "
            f"({want.name}). Pictures will be filed in the folder above, "
            f"not in the trip. Expected: {want}")
        self.dest_note.setVisible(True)

    def _update_ai_checkbox_enabled(self):
        bucketed = self.bucket_checkbox.isChecked()
        self.ai_checkbox.setEnabled(bucketed)
        self.ai_open_checkbox.setEnabled(bucketed and self.ai_checkbox.isChecked())
        self.ai_checkbox.setToolTip(
            "Each day is measured as soon as it is copied (sharpness, the animals, "
            "a closer look at each pose) while the card copies the next day. "
            "Nothing is written to your pictures; the names come when you open AI…"
            if bucketed else
            "Needs \"Sort into date subfolders\" ticked — the AI looks at a day at a time")

    def _update_tracklog_checkbox_enabled(self):
        """Auto-geotag needs each entry grouped by its destination day-folder
        (see _build_tracklog_entries) — meaningless without date-subfolder
        bucketing, so disable rather than silently no-op without it."""
        bucketed = self.bucket_checkbox.isChecked()
        enabled = bool(self._gpslogs_root) and bucketed
        self.tracklog_checkbox.setEnabled(enabled)
        if not self._gpslogs_root:
            self.tracklog_checkbox.setToolTip(
                "Configure a GPS logs folder in Settings ▸ Repositories first")
        elif not bucketed:
            self.tracklog_checkbox.setToolTip(
                "Needs \"Sort into date subfolders\" ticked — each day's tracklog "
                "match depends on which day-folder a picture landed in")
        else:
            self.tracklog_checkbox.setToolTip("")

    def _set_progress(self, text):
        """Live progress in the green block (shown only while something runs)."""
        self.progress_label.setText(text)
        self.progress_label.setVisible(bool(text))

    def _log_phase(self, text):
        """Announce a long phase in the LOG as well as on the dialog.

        Copying a card runs for minutes with nothing written to the log between
        "Found N pictures" and "Created N sidecars", which reads as a hang. The
        dialog's own progress line is not enough — the log is where the user
        looks to see whether anything is happening."""
        self._set_progress(text)
        try:
            self.logger.info(text)
        except Exception:
            pass

    def _lock_for_trip(self):
        """Trip mode: the destination, the name prefix and the date folders come
        from the trip, and cannot be changed here. The prefix is the one the SV
        INDEX backup recorded off the files themselves — the restored sidecars
        carry it, so the pictures must too, letter for letter."""
        from engine import work_restore as wr
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="").strip()
        backup_prefix = wr.load_backup_prefix(trip_root) if trip_root else ""
        if backup_prefix:
            self.prefix_field.field.setText(backup_prefix)
        if self.trip_picture_root:
            self.dest_field.field.setText(self.trip_picture_root)
        self.dest_field.field.setReadOnly(True)
        self.dest_field.browse_btn.setEnabled(False)
        self.prefix_field.field.setReadOnly(True)
        self.bucket_checkbox.setChecked(True)
        self.bucket_checkbox.setEnabled(False)
        tip = "Set by the trip — opened from Import Trip."
        for w in (self.dest_field.field, self.prefix_field.field, self.bucket_checkbox):
            w.setToolTip(tip)
        name = Path(self.trip_picture_root).name if self.trip_picture_root else ""
        if name:
            self.setWindowTitle(f"Read Card or Folder — for {name}")

    def on_scan(self):
        """Group the card by capture-day and fill the day table — no copying."""
        source = self.source_field.get_path()
        if not source or not os.path.isdir(source):
            self.source_field.show_hint("Please select a valid source folder.")
            return
        self.tabs.setCurrentIndex(1)   # the day table/progress live there
        self.scan_btn.setEnabled(False)
        self.read_btn.setEnabled(False)
        self._log_phase("Scanning the card for capture days — grouping by file "
                        "date, exact dates are read when you Read…")
        self._scanned_source = os.path.normpath(source)
        self._scan_thread = QThread()
        preview = None
        if self.trip_mode:
            preview = {"dest": self.dest_field.get_path(),
                       "prefix": self.prefix_field.get_text() or "",
                       "include_names": (self._survivors or None)
                       if getattr(self, "recull_checkbox", None) is None
                       or self.recull_checkbox.isChecked() else None}
        self._scan_worker = _SDCardScanWorker(source, self._tz_offset(), preview)
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.finished.connect(self.on_scan_finished)
        self._scan_thread.start()

    def on_scan_finished(self, days, total, ignored, error):
        self._scan_thread.quit()
        self._scan_thread.wait()
        self.scan_btn.setEnabled(True)
        self.read_btn.setEnabled(True)
        self._set_progress("")
        if error:
            self.logger.warning(f"❌ Day scan failed: {error}")
            self.status_label.setText(f"❌ {error}")
            return
        self._read_preview = getattr(self._scan_worker, "preview", {}) or {}
        self._populate_days(days)
        self._check_camera_clock(days)
        self._fit_to_content()
        # The unfiled inbox is per YEAR, and until the card is scanned we only know
        # today's year — a card shot last December would otherwise land in the wrong
        # one. Now that the dates are known, re-point at the year they are in.
        if self.unfiled_mode and days:
            from engine.unfiled import unfiled_root, currentpictures_root, year_of_day
            year = year_of_day(days[0][0])
            base = currentpictures_root(self.dest_field.get_path())
            if year and base:
                self.dest_field.field.setText(unfiled_root(year, base=base))
        note = ""
        if ignored:
            from batch.sv0ReadSDCard import (describe_ignored, describe_unreadable,
                                             split_ignored, UNREADABLE_ADVICE)
            pics, rest = split_ignored(ignored)
            if pics:
                note += (f"  ⚠ {describe_unreadable(pics, self._scanned_source)}: "
                         f"{UNREADABLE_ADVICE}")
            if rest:
                note += (f"  ⚠ Not recognised as pictures, so NOT copied: "
                         f"{describe_ignored(rest)}.")
        self.status_label.setText(
            f"Found {total} picture(s) across {len(days)} day(s). "
            f"Tick the days to copy, then Read.{note}")

    def _populate_days(self, days):
        """Fill the day table with one checkable row per (date, count), all ticked."""
        from engine.tracklog_geotag import find_day_tracklogs
        from batch.sv0ReadSDCard import pictures_on_disk_by_day
        # ONE listing of the destination for the whole table, not one per row.
        try:
            on_disk = pictures_on_disk_by_day(self.dest_field.get_path())
        except Exception as e:                  # a bad/absent dest must not kill the scan
            self.logger.info(f"Read SD-cards: could not read the destination "
                             f"folder for already-copied counts: {e}")
            on_disk = {}
        self.days_table.blockSignals(True)
        self.days_table.setRowCount(0)
        for date_str, count in days:
            r = self.days_table.rowCount()
            self.days_table.insertRow(r)
            day_item = QTableWidgetItem(date_str)
            day_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            day_item.setCheckState(Qt.CheckState.Checked)
            self.days_table.setItem(r, DAY_COL, day_item)
            cnt_item = QTableWidgetItem(str(count))
            cnt_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            cnt_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.days_table.setItem(r, FILES_COL, cnt_item)
            self._set_cull_cells(r, date_str, count)
            # Pure "does a log file exist for this day" preview — no offset
            # estimation here (that needs the full picture set and only runs
            # during the actual auto-geotag pass), just visibility into
            # coverage before Read is pressed instead of finding out after.
            # Blank (not "none found") when no GPS logs root is configured at
            # all — that's a settings gap, not a per-day miss.
            track_text = ""
            if self._gpslogs_root:
                logs = find_day_tracklogs(date_str, self._gpslogs_root)
                if not logs:
                    track_text = "— no tracklog found"
                elif len(logs) == 1:
                    track_text = f"✓ {Path(logs[0]).name}"
                else:
                    track_text = f"✓ {Path(logs[0]).name} (+{len(logs) - 1})"
            track_item = QTableWidgetItem(track_text)
            track_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.days_table.setItem(r, TRACK_COL, track_item)
            # What is ALREADY on disk for this day, read from the destination
            # rather than from the last run — the run is forgotten when the
            # dialog closes, the day folders are not. Without this, a card read
            # spread over two sittings shows nothing on the second one.
            have = on_disk.get(date_str, 0)
            if not have:
                text = ""
            elif have >= count:
                text = f"{have} ✓"
            else:
                # Partial: say so rather than tick it. Fewer on disk than on the
                # card is the interesting case (an interrupted read, or pictures
                # culled since), and a tick would hide it.
                text = f"{have} of {count}"
            disk_item = QTableWidgetItem(text)
            disk_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            disk_item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
            self.days_table.setItem(r, COPIED_COL, disk_item)
            # Nothing left to do for a day already complete — untick it so Read
            # targets what is actually missing. Still tickable by hand.
            if have >= count:
                day_item.setCheckState(Qt.CheckState.Unchecked)
            if self.trip_mode:
                self._set_preview_cells(r, date_str, day_item)
        self.days_table.setColumnHidden(COPIED_COL, not on_disk)
        self._show_cull_columns()
        self.days_table.blockSignals(False)
        self.days_container.setVisible(bool(days))
        self.days_container.setEnabled(True)   # a fresh scan re-enables a greyed block
        self._refresh_all_days_checkbox()
        self._size_days_table()
        self._fit_to_content()

    def _fit_to_content(self):
        """Grow the dialog to fit whatever is currently visible, but stop at a
        comfortable ceiling — each tab's own scroll area (see __init__)
        absorbs whatever doesn't fit, rather than the window growing without
        limit.

        Sized to the CURRENTLY ACTIVE tab only, not both combined — that is
        the whole point of splitting Input from SD card. Called whenever that
        could have changed: after the day table is (re)filled, after the
        camera-clock warning is shown/hidden (on_scan_finished checks the
        clock after populating days, so sizing only at the first point left
        the warning box clipped), after a read finishes, and whenever the
        active tab itself changes (see the tabs.currentChanged connection).

        sizeHint() is not used for the height here: QScrollArea's own
        sizeHint() does not simply track its content (Qt caps it internally),
        so the dialog's height is built by hand instead — the active tab's
        own content widget's real sizeHint() (unaffected by the scroll area
        wrapping it), the tab bar, and the button row below it."""
        active = self._tab_card if self.tabs.currentIndex() == 1 else self._tab_input
        active.layout().activate()
        content_h = active.sizeHint().height()
        chrome_h = (self.tabs.tabBar().sizeHint().height()
                    + self._btn_row.sizeHint().height()
                    + self.layout().spacing() * 2 + 16)
        want_h = max(content_h + chrome_h, self.minimumSizeHint().height())

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            # A warning box AND a finished-read summary AND the day table
            # together can exceed most screens; growing to fit all of it at
            # once made the dialog take over the screen instead of fitting in
            # it. 80% leaves room to see it's a dialog, not the whole app.
            cap = int(screen.availableGeometry().height() * 0.8)
            want_h = min(want_h, max(cap, chrome_h + 150))
        want_w = max(self.width(), self.minimumSizeHint().width())
        # Width: the day table's columns, all of them readable. minimumSizeHint
        # does not include them (a table is happy to scroll sideways), so the
        # window opened at 700 px with "To copy" and "Metadata" cut off and a
        # horizontal scroll bar (2026-11-13). _resize_within_screen caps it.
        table = getattr(self, "days_table", None)
        if self.tabs.currentIndex() == 1 and table is not None and table.rowCount():
            hh = table.horizontalHeader()
            need = sum(max(hh.sectionSizeHint(c), table.sizeHintForColumn(c))
                       for c in range(table.columnCount()) if not table.isColumnHidden(c))
            need += table.verticalScrollBar().sizeHint().width() + 2 * table.frameWidth() + 4
            extra = need - table.width()
            if extra > 0:
                want_w = max(want_w, self.width() + extra)
        self._resize_within_screen(want_w, want_h)

    def _resize_within_screen(self, want_w, want_h):
        """Resize, but never ask the window manager for something it will refuse.

        Qt asks, Windows clamps to the work area, and Qt then logs
        "QWindowsWindow::setGeometry: Unable to set geometry ..." — a warning
        that never reaches the GUI, so it is pure terminal noise for the user
        while looking like a real fault in the log. The request is what is wrong,
        not the answer: the window's FRAME (title bar and borders) counts against
        the work area, and a dialog already sitting near an edge has less room
        than the screen is wide.

        So: clamp the size to what the work area can actually hold, then, if
        growing has pushed the frame off an edge, move it back on-screen. Only
        ever moved when it would otherwise be partly invisible.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:                       # headless / no screen — nothing to clamp to
            self.resize(want_w, want_h)
            return
        avail = screen.availableGeometry()
        frame = self.frameGeometry()
        # Window decorations are outside width()/height() but inside the work area.
        chrome_w = max(frame.width() - self.width(), 0)
        chrome_h = max(frame.height() - self.height(), 0)
        centre = frame.center()
        self.resize(min(want_w, avail.width() - chrome_w),
                    min(want_h, avail.height() - chrome_h))

        # Grow or shrink around the CENTRE, not the top-left corner: a dialog
        # that opened centred stays centred after "Scan days" changes its size
        # (backlog 1.3), and one the person moved stays where they put it.
        frame = self.frameGeometry()
        if self.isVisible():
            frame.moveCenter(centre)
            self.move(frame.topLeft())
            frame = self.frameGeometry()
        if avail.contains(frame):
            return
        frame.moveLeft(max(avail.left(), min(frame.left(),
                                             avail.right() - frame.width() + 1)))
        frame.moveTop(max(avail.top(), min(frame.top(),
                                           avail.bottom() - frame.height() + 1)))
        self.move(frame.topLeft())

    def _kept_on_day(self, date_str):
        """How many pictures this day still held at its last backup, or None when
        the backup has never seen the day (which is not the same as "none left")."""
        names = self._survivors.get(date_str)
        return len(names) if names else None

    def _set_preview_cells(self, row, date_str, day_item):
        """Trip mode: what a Read would copy, and how the metadata on this
        source compares with the trip's. A day with nothing to copy and nothing
        newer starts unticked — whatever the raw counts say."""
        p = (getattr(self, "_read_preview", {}) or {}).get(date_str)
        if p is None:
            texts = ("", "")
        else:
            bits = []
            if p["meta_newer"]:
                bits.append(f"{p['meta_newer']} newer than here — taken")
            if p["meta_older"]:
                bits.append(f"{p['meta_older']} older — left alone")
            to_copy = str(p["to_copy"])
            if p.get("deleted_here"):
                to_copy += f"  ({p['deleted_here']} deleted here stay deleted)"
            texts = (to_copy, " · ".join(bits) or "same as here")
            day_item.setCheckState(Qt.CheckState.Checked if (p["to_copy"] or p["meta_newer"])
                                   else Qt.CheckState.Unchecked)
        for col, text in ((TO_COPY_COL, texts[0]), (META_COL, texts[1])):
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if col == TO_COPY_COL:
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.days_table.setItem(row, col, item)

    def _set_cull_cells(self, row, date_str, count):
        """Fill the Deleted/Kept pair for one day.

        The card keeps every frame ever shot, so "Files" is the shooting count,
        not what a re-read brings back — 202 on the card against 21 still wanted.
        A day this trip's backup knows says exactly how many survived the
        culling; a day it has never seen leaves both cells blank rather than
        claiming nothing was deleted.
        """
        kept = self._kept_on_day(date_str)
        for col, value in ((DELETED_COL, None if kept is None else max(count - kept, 0)),
                           (KEPT_COL, kept)):
            item = QTableWidgetItem("" if value is None else str(value))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.days_table.setItem(row, col, item)

    def _fill_copied_column(self, per_day):
        """Show, per day, what is on disk after the read.

        The number is copied + skipped, NOT this run's copies. "Skipped" here
        means the picture was already at the destination, verified by content —
        so it is present, and a day that was fully read an hour ago is just as
        safely on disk as one read a minute ago. Reporting the literal this-run
        figure would show a re-read as "0", which reads as failure at exactly
        the moment you are checking that nothing was missed.

        Failures are never folded into that tick: a day with even one failed
        copy says so, because that is the row worth going back to.
        """
        table = getattr(self, "days_table", None)
        if table is None or not per_day:
            return
        table.blockSignals(True)
        try:
            for r in range(table.rowCount()):
                day_item = table.item(r, DAY_COL)
                if day_item is None:
                    continue
                tally = per_day.get(day_item.text())
                if tally:
                    present = tally.get("copied", 0) + tally.get("skipped", 0)
                    failed = tally.get("failed", 0)
                    text = f"{present} ✓" if present else ""
                    if failed:
                        text = (f"{text} · {failed} failed" if text
                                else f"{failed} failed")
                else:
                    # A day this run did not touch. Leave whatever the scan put
                    # there — a day already complete from an earlier sitting
                    # reads "104 ✓", and blanking it here would erase the very
                    # answer this column exists to give.
                    continue
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(r, COPIED_COL, item)
            table.setColumnHidden(COPIED_COL, False)
        finally:
            table.blockSignals(False)
        self._size_days_table()

    def _show_cull_columns(self):
        """Show Deleted/Kept only when the backup covers a day on the card — two
        permanently empty columns are noise on a normal mid-trip card read."""
        known = any(self._kept_on_day(self.days_table.item(r, DAY_COL).text()) is not None
                    for r in range(self.days_table.rowCount())
                    if self.days_table.item(r, DAY_COL) is not None)
        self.days_table.setColumnHidden(DELETED_COL, not known)
        self.days_table.setColumnHidden(KEPT_COL, not known)

    def _refresh_cull_columns(self):
        """Re-read the Deleted/Kept pair from the current survivor list — a backup
        taken (or a card inserted) while this dialog is open changes the answer."""
        table = getattr(self, "days_table", None)
        if table is None or table.rowCount() == 0:
            return
        table.blockSignals(True)
        for r in range(table.rowCount()):
            day_item, cnt_item = table.item(r, DAY_COL), table.item(r, FILES_COL)
            if day_item is None or cnt_item is None:
                continue
            try:
                count = int(cnt_item.text())
            except ValueError:
                continue
            self._set_cull_cells(r, day_item.text(), count)
        self._show_cull_columns()
        table.blockSignals(False)

    def _size_days_table(self):
        """Give the day table a floor and a ceiling rather than one fixed height,
        so enlarging the dialog shows more days.

        Floor — the first 8 days, or all of them when there are fewer: the height
        the dialog opens at, unchanged from when the table could not grow. A card
        with 24 days on it still opens compact instead of filling the screen.

        Ceiling — exactly the rows it has: dragging the dialog taller reveals real
        days and then stops, so the table never becomes a tall empty box below the
        last day. Between the two, the layout decides (see the stretch factors
        where days_table and days_container are added).

        Heights are measured (resizeRowsToContents), not estimated — styled rows
        can be much taller than the default section size, which previously
        clipped the table."""
        rows = self.days_table.rowCount()
        if rows == 0:
            return
        self.days_table.resizeRowsToContents()
        header = self.days_table.horizontalHeader()
        header_h = max(header.height(), header.sizeHint().height())
        chrome = 2 * self.days_table.frameWidth() + 2
        heights = [self.days_table.rowHeight(i) for i in range(rows)]
        floor_rows = min(rows, 8)
        self.days_table.setMinimumHeight(header_h + sum(heights[:floor_rows]) + chrome)
        self.days_table.setMaximumHeight(header_h + sum(heights) + chrome)

    def _on_all_days_clicked(self):
        """Master checkbox: tick all days unless they're all ticked (then clear)."""
        n = self.days_table.rowCount()
        all_on = n > 0 and all(
            self.days_table.item(r, DAY_COL).checkState() == Qt.CheckState.Checked
            for r in range(n))
        target = Qt.CheckState.Unchecked if all_on else Qt.CheckState.Checked
        self.days_table.blockSignals(True)
        for r in range(n):
            self.days_table.item(r, DAY_COL).setCheckState(target)
        self.days_table.blockSignals(False)
        self._refresh_all_days_checkbox()

    def _on_day_item_changed(self, _item):
        self._refresh_all_days_checkbox()

    def _refresh_all_days_checkbox(self):
        """Reflect the rows in the master checkbox (checked / partial / unchecked)."""
        n = self.days_table.rowCount()
        checked = sum(1 for r in range(n)
                      if self.days_table.item(r, DAY_COL).checkState() == Qt.CheckState.Checked)
        if checked == 0:
            state = Qt.CheckState.Unchecked
        elif checked == n:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self.all_days_checkbox.blockSignals(True)
        self.all_days_checkbox.setCheckState(state)
        self.all_days_checkbox.blockSignals(False)

    def _selected_days(self):
        """Set of ticked 'YYYY-MM-DD' strings, or None when no scan was done
        (→ copy every day, the original behaviour)."""
        n = self.days_table.rowCount()
        if n == 0:
            return None
        return {self.days_table.item(r, DAY_COL).text() for r in range(n)
                if self.days_table.item(r, DAY_COL).checkState() == Qt.CheckState.Checked}

    def _selected_file_count(self):
        """Pictures the ticked days would copy.

        Where the culling is being re-applied that is the day's survivor count,
        not its file count — otherwise the confirm box offers to copy 352
        pictures and 94 arrive.
        """
        recull = (self.recull_checkbox.isVisible()
                  and self.recull_checkbox.isChecked())
        total = 0
        for r in range(self.days_table.rowCount()):
            if self.days_table.item(r, DAY_COL).checkState() != Qt.CheckState.Checked:
                continue
            kept = self._kept_on_day(self.days_table.item(r, DAY_COL).text()) if recull else None
            if kept is not None:
                total += kept
                continue
            try:
                total += int(self.days_table.item(r, FILES_COL).text())
            except ValueError:
                pass
        return total

    def _days_copied_count(self):
        """Distinct YYYY-MM-DD day folders actually present among this run's
        targets — reflects what really happened (copied + already-there), which
        is what a "Copied N over D days" summary should report; the day-picker
        selection alone is None when Scan was skipped ('copy every day')."""
        from utils import days_registry
        days = {Path(t).parent.name for t in self._last_targets
                if days_registry.is_day_folder_name(Path(t).parent.name)}
        return len(days)

    def _elapsed_summary(self):
        """' over N day(s) in T' suffix for the final Read status — the
        click-to-done time the user actually experiences, including any
        geocode step that follows the copy."""
        start = getattr(self, "_read_start_time", None)
        if start is None:
            return ""
        from engine.exifcache import _fmt_duration
        # The clock times too: set beside the AI's they show how much ran at once.
        elapsed = f"{_fmt_duration(time.time() - start)} ({_clock(start, time.time())})"
        days = self._days_copied_count()
        # Its own sentence: it is appended after sentences that end in a period.
        return (f" Time: {elapsed} for {days} day(s)." if days
                else f" Time: {elapsed}.")

    def _done_marker(self):
        """'✅ Done —' for a clean read; a warning marker when the copy phase
        lost pictures. Every phase that can write the final status line goes
        through this, so a Read with failures can never end up looking like a
        success just because the geocode step finished cleanly."""
        failed = getattr(self, "_read_failed", 0)
        if failed:
            return f"⚠️ Done, but {failed} picture(s) FAILED to copy —"
        return "✅ Done —"

    @staticmethod
    def _complete_txt(summary) -> str:
        """The closing sentence: did every picture this read was meant to bring
        arrive? Says it plainly either way; empty when the summary can't tell
        (an aborted run reports itself)."""
        expected = summary.get("expected")
        present = summary.get("present")
        if expected is None or present is None or summary.get("error"):
            return ""
        if expected <= 0:
            return ""
        if present >= expected:
            return (f"\n✅ All {expected} picture(s) from this card for the chosen "
                    f"days are here: each new copy checked, each one already here "
                    f"identical to the card.")
        return (f"\n⚠️ Only {present} of the {expected} picture(s) from this card for "
                f"the chosen days are here — keep the card and read it again.")

    def _log_read_done(self, text):
        """Closing log line for a finished Read, at the level the outcome
        deserves — see _done_marker."""
        if getattr(self, "_read_failed", 0):
            self.logger.warning(f"⚠️ Read complete WITH FAILURES. {text}")
        else:
            self.logger.info(f"✅ Read complete. {text}")

    def on_read(self):
        # status_label/progress_label live on the "SD card" tab (everything
        # Read reports lands there, success or failure), so switch to it up
        # front — otherwise a validation error below would set text on a tab
        # that isn't showing.
        self.tabs.setCurrentIndex(1)
        # Backstop for the disabled button: a prefix that failed the allow-list
        # must never reach target_name, whatever route got here.
        raw_prefix = self.prefix_field.get_text() or ""
        if any(not (c.isalnum() or c in self._PREFIX_EXTRA_OK) for c in raw_prefix):
            self.status_label.setText(
                "❌ The filename prefix contains a character that is not allowed — "
                "letters, digits, spaces, - and _ only.")
            return
        source = self.source_field.get_path()
        dest = self.dest_field.get_path()
        if not source or not os.path.isdir(source):
            self.source_field.show_hint("Please select a valid source folder.")
            return
        if self.unfiled_mode and dest and not os.path.isdir(dest):
            # The inbox for a year does not exist until the first card of that year
            # is read. Making it here is the whole "asks no questions" promise —
            # the path is derived, so there is nothing for the user to confirm.
            try:
                os.makedirs(dest, exist_ok=True)
                self.logger.info(f"Created the unfiled inbox: {dest}")
            except OSError as e:
                self.dest_field.show_hint(f"Could not create {dest}: {e}")
                return
        if not dest or not os.path.isdir(dest):
            self.dest_field.show_hint("Please select a valid project pictures folder.")
            return

        # The project dir tracks the active trip, so it can change unexpectedly
        # when the trip switched — warn before reading a card into a different
        # folder than last time (they can easily drift out of sync otherwise).
        # Not in the unfiled lane: there the destination is DERIVED from the date,
        # so "it differs from last time" is normal (a new year, another card) and
        # the prompt would be noise on exactly the flow that must ask nothing.
        last_dest = "" if self.unfiled_mode else get_cache_gui_section("sdcard_ingest", "last_dest")
        if last_dest and os.path.normpath(last_dest) != os.path.normpath(dest):
            if QMessageBox.question(
                    self, "Project directory changed",
                    f"Last time you read a card into:\n{last_dest}\n\n"
                    f"Now you're about to read into:\n{dest}\n\n"
                    f"These differ — read into the new folder?"
                    ) != QMessageBox.StandardButton.Yes:
                return
        update_cache_gui_section("sdcard_ingest", "last_dest", dest)

        prefix = self.prefix_field.get_text()
        bucket_by_date = self.bucket_checkbox.isChecked()
        write_sidecars = self.sidecar_checkbox.isChecked()
        try:
            tz_offset = float(self.tz_offset_field.get_text() or "0")
        except ValueError:
            self.tz_offset_field.field.setText("0")
            tz_offset = 0.0

        # Remember the inputs for next time (usually the same card / trip / layout).
        update_cache_gui_section("sdcard_ingest", "source", source)
        update_cache_gui_section("sdcard_ingest", "prefix", prefix)
        update_cache_gui_section("sdcard_ingest", "by_date", "1" if bucket_by_date else "0")
        update_cache_gui_section("sdcard_ingest", "tz_offset", str(tz_offset))
        update_cache_gui_section("sdcard_ingest", "write_sidecars", "1" if write_sidecars else "0")
        update_cache_gui_section("sdcard_ingest", "add_location",
                                 "1" if self.location_checkbox.isChecked() else "0")
        if self._ai_available:
            update_cache_gui_section("sdcard_ingest", "ai",
                                     "1" if self.ai_checkbox.isChecked() else "0")
            update_cache_gui_section("sdcard_ingest", "ai_open",
                                     "1" if self.ai_open_checkbox.isChecked() else "0")

        # Day selection (None = no scan done → copy every day). Guard against a
        # stale scan from a different source folder, and against ticking nothing.
        include_dates = self._selected_days()
        if include_dates is not None and self._scanned_source \
                and self._scanned_source != os.path.normpath(source):
            QMessageBox.information(
                self, "Re-scan needed",
                "The day list was scanned for a different source folder.\n"
                "Please press “Scan days” again before reading.")
            return
        if include_dates is not None and not include_dates:
            QMessageBox.information(self, "No days selected",
                                    "Tick at least one day to copy, or press “Scan days”.")
            return

        # Confirm what will happen — destination + filename prefix + scope.
        if include_dates is None:
            scope = "all pictures on the card"
        else:
            scope = f"{self._selected_file_count()} picture(s) from {len(include_dates)} day(s)"
        confirm = (f"Copy {scope} to:\n\n    {dest}\n\n"
                   f'Filenames prefixed with "{prefix}".' if prefix
                   else f"Copy {scope} to:\n\n    {dest}\n\nFilenames kept as-is (no prefix).")
        if bucket_by_date:
            confirm += "\nSorted into YYYY-MM-DD subfolders."
        if QMessageBox.question(self, "Read Card or Folder", confirm + "\n\nProceed?") \
                != QMessageBox.StandardButton.Yes:
            return

        self._last_dest = dest
        self._read_start_time = time.time()   # click-to-done timing for the final summary
        if self.days_mode:
            from utils.config import set_setting
            set_setting("days", "location", dest)   # remember the Days location
        self._remember_trip_read_settings(prefix, tz_offset, dest)
        self._include_dates = include_dates
        self._cancelled = False
        self._read_failed = 0
        self.read_btn.setEnabled(False)
        self.view_btn.setEnabled(False)
        self.status_label.setText("")
        self._log_phase("Copying pictures from the card — each copy is verified, "
                        "so this is the slow part…")
        if self._close_connected:
            self.close_btn.clicked.disconnect(self.reject)
            self.close_btn.clicked.connect(self._on_cancel)
            self.close_btn.setText("Cancel")
            self._close_connected = False
        days_note = "all days" if include_dates is None else f"{len(include_dates)} selected day(s)"
        self.logger.debug(
            f"Reading SD-card from {source} → {dest} (prefix: {prefix or '(none)'}, "
            f"by-date: {bucket_by_date}, tz-offset: {tz_offset}h, sidecars: {write_sidecars}, "
            f"days: {days_note})"
        )

        self._thread = QThread()
        # Re-read the filter now rather than trusting what was computed when the
        # dialog opened: a backup may have been taken in between.
        self._refresh_recull()
        use_filter = (self._survivors
                      if (self.recull_checkbox.isVisible()
                          and self.recull_checkbox.isChecked()) else None)
        self._start_ai(bucket_by_date)
        self._worker = _SDCardCopyWorker(source, dest, prefix, bucket_by_date, tz_offset,
                                         write_sidecars, include_dates=include_dates,
                                         trip_stamp=self._trip_stamp(),
                                         include_names=use_filter,
                                         newer_wins=self.trip_mode,
                                         on_day_copied=self._ai.add if self._ai else None)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.on_progress)
        self._worker.finished.connect(self.on_finished)
        self._thread.start()

    # ── the AI during the read (gui.card_ai) ─────────────────────────────────
    def _start_ai(self, bucket_by_date: bool):
        """Start the AI worker when it is ticked; the copy hands it each day."""
        self._ai = None
        self._read_done = False
        self._open_ai_pending = False
        if not (bucket_by_date and self._ai_available
                and self.ai_checkbox.isChecked()):
            return
        self._open_ai_pending = self.ai_open_checkbox.isChecked()
        from gui.card_ai import CardAiWorker
        master = _config.get("master", "masterbiotadatabase", fallback="").strip().strip('"')
        aggressive = _config.get("ai", "group_aggressively", fallback="no") == "yes"
        self._ai = CardAiWorker(master, aggressive, self)
        self._ai.state.connect(self._on_ai_state)
        self._ai.all_done.connect(self._on_ai_done)
        self.days_table.setColumnHidden(AI_COL, False)
        self.ai_status_label.setText("⏳ The AI looks at the pictures as soon as they are copied "
                                     "(a long day in parts of about 250)…")
        self.ai_status_label.setVisible(True)
        self._ai.start()
        self._ai.setPriority(QThread.Priority.LowPriority)   # the copy comes first

    def _on_ai_state(self, day: str, text: str):
        table = getattr(self, "days_table", None)
        if table is None:
            return
        for r in range(table.rowCount()):
            item = table.item(r, DAY_COL)
            if item is not None and item.text() == day:
                cell = QTableWidgetItem(text)
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
                table.setItem(r, AI_COL, cell)
                return

    def _on_ai_done(self, total: dict):
        if self._ai is not None:
            self._ai.wait()
        self._ai = None
        if not total.get("days"):
            self._maybe_open_ai()
            return
        mins, secs = divmod(int(total.get("seconds", 0)), 60)
        took = f"{mins} min {secs} s" if mins else f"{secs} s"
        if total.get("started"):
            took += f" ({_clock(total['started'], total['started'] + total['seconds'])})"
        line = (f"The AI looked at {total['pictures']} pictures of {total['days']} "
                f"day(s) — {total['animals']} with an animal — in {took}"
                + (" (stopped)." if total.get("stopped") else
                   ". AI… on those days opens at once."))
        self.logger.info(line)
        self.ai_status_label.setText(("⚠️ " if total.get("failed") else "✅ ") + line
                                     + (f" {total['failed']} day(s) could not be looked "
                                        f"at — see the log." if total.get("failed") else ""))
        self.ai_status_label.setVisible(True)
        if total.get("stopped"):
            self._open_ai_pending = False
        self._maybe_open_ai()

    def _maybe_open_ai(self):
        """"Open the pictures with AI…" ticked: once BOTH the read (with its
        tracklog and place names — the country decides the names) and the AI
        are done, open the culling viewer with AI… on top (it measures in
        seconds: the AI already looked). Nothing is written until Accept…."""
        if not (getattr(self, "_open_ai_pending", False)
                and getattr(self, "_read_done", False) and self._ai is None):
            return
        self._open_ai_pending = False
        if getattr(self, "_cancelled", False) or not self._last_targets:
            return
        self.on_view()
        grid = self._preview_dlg
        if grid is not None:
            self.logger.info("Card read: opening AI… on the pictures.")
            grid._on_ai()

    def _remember_trip_read_settings(self, prefix, tz_offset, dest):
        """Record the filename prefix AND the timezone offset in the open trip's
        trip.ini — the two settings that decide what a picture is called and
        which day it lands in, and so the two a rebuild cannot do without.

        Only when the card is actually landing in THAT trip's picture folder —
        a read into the Days lane or a scratch folder says nothing about the
        trip's naming and must not overwrite it.

        Worth the trouble because trip.ini travels: svr6ExportTrip already ships
        it, so the trip export starts carrying the prefix with no change to the
        export at all. Best-effort — a trip without a trip.ini is not an error
        here, and failing to record it must never stop a card read.
        """
        prefix = (prefix or "").strip()
        if not self.trip_picture_root:
            return
        try:
            same = os.path.normcase(os.path.normpath(str(dest))) ==                    os.path.normcase(os.path.normpath(str(self.trip_picture_root)))
        except (OSError, ValueError):
            return
        if not same:
            return
        trip_root = _config.get("trip", "tripbiotadatabase", fallback="").strip()
        if not trip_root:
            return
        try:
            from utils.trip_config import read_trip_ini, set_trip_flag
            data = read_trip_ini(trip_root) or {}
            wrote = []
            if prefix and (data.get("picture_prefix", "") or "").strip() != prefix:
                if set_trip_flag(trip_root, "picture_prefix", prefix):
                    wrote.append(f"prefix '{prefix}'")
            tz = str(tz_offset).strip()
            if tz and (data.get("tz_offset", "") or "").strip() != tz:
                if set_trip_flag(trip_root, "tz_offset", tz):
                    wrote.append(f"timezone offset {tz}h")
            if wrote:
                self.logger.info(
                    f"Recorded this trip's {' and '.join(wrote)} — they travel "
                    f"with the trip backup, so a restore on another machine reads "
                    f"the cards the same way and files them into the same days.")
        except Exception as e:      # never block a card read over bookkeeping
            self.logger.warning(f"Could not record the trip's read settings: {e}")

    def on_progress(self, done, total, name):
        self._set_progress(f"Copying {done}/{total}: {name}")

    def on_finished(self, summary, error):
        self._thread.quit()
        self._thread.wait()
        if self._ai is not None:
            if error or getattr(self, "_cancelled", False):
                self._ai.stop()
            else:
                self._ai.no_more()          # it finishes the days already copied
        self.read_btn.setEnabled(True)
        self._restore_close_btn()
        if error:
            self.logger.warning(f"❌ Card read failed: {error}")
            self.status_label.setText(f"❌ {error}")
            self._set_progress("")
            self._fit_to_content()
            return
        # Held on the dialog because the final "Done" line is written by
        # whichever phase finishes last (here, or _add_location_names) — every
        # one of them must show the same marker.
        self._read_failed = summary.get("failed", 0)
        if self._read_failed:
            names = summary.get("failed_names", [])
            listed = ", ".join(names[:5]) + (" …" if len(names) > 5 else "")
            self.logger.warning(
                f"⚠️ {self._read_failed} picture(s) could NOT be copied: {listed} — "
                f"re-run the Read; pictures already copied are skipped.")
        renamed = summary.get("renamed", 0)
        renamed_txt = f" ({renamed} renamed)" if renamed else ""
        deselected = summary.get("deselected", 0)
        deselected_txt = f", {deselected} day-deselected" if deselected else ""
        culled = summary.get("culled", 0)
        culled_txt = (f", {culled} not copied (deleted during the trip)"
                      if culled else "")
        copied = summary.get("copied", 0)
        # Shared tail — what was kept is reported the same whether the run finished
        # or was aborted (the files already copied are verified and kept).
        # Carried sidecars are reported SEPARATELY from generated ones: they are
        # the culling work (species, stars, colours) coming across from a source
        # folder that had already been worked on, and rolling them into one
        # "N sidecars" number would hide exactly the thing worth confirming.
        self._last_per_day = summary.get("per_day") or {}
        self._fill_copied_column(self._last_per_day)
        # Show every day folder the copy created. The trip overview only
        # re-reads its days when SV regains focus — mid-read that caught the
        # first new day and never the rest (only a restart showed them). The
        # unfiled inbox was built when this dialog OPENED, before any copy.
        if copied and self.unfiled_mode:
            from gui.unfiled_overview import populate_unfiled_overview
            panel = getattr(self.passed_parent, "unfiled_panel", None)
            if panel is not None:
                panel.rebuild()
            populate_unfiled_overview(self.passed_parent)
        elif copied and not self.days_mode:
            populate_trip_overview(self.passed_parent)
        carried = summary.get("sidecars_copied", 0)
        carried_txt = (f" {carried} sidecar(s) came along with their pictures "
                       f"(species, ratings, colours)." if carried else "")
        made = summary.get("sidecars", 0)
        made_txt = f" {made} sidecar(s) created." if made else ""
        # A RAW with no sidecar has nowhere to keep a species, a star or a crop,
        # so a read that leaves some behind (the sidecar phase is the last one and
        # is skipped on cancel) must SAY so here, not only in the log — that is
        # how 678 of one day's RAWs came home bare and unnoticed (2026-09-21).
        bare_raws = summary.get("sidecars_missing", 0)
        bare_txt = (f" ⚠️ {bare_raws} RAW(s) have NO sidecar — species, ratings and "
                    f"crops cannot be stored for them. Read the card again to "
                    f"finish the job." if bare_raws else "")
        tail = (f"skipped {summary.get('skipped', 0)}{deselected_txt}{culled_txt}, "
                f"failed {summary.get('failed', 0)} "
                f"of {summary.get('total', 0)} pictures."
                f"{made_txt}{carried_txt}{bare_txt}")
        cancelled = getattr(self, "_cancelled", False)
        # The copy is only the first phase: when "Add location names" is ticked, a
        # reverse-geocode run follows and keeps writing for another minute. Saying
        # "Done" here made the dialog claim success while the log was still
        # printing "master: set place names on N picture(s)" — so the copy result
        # is reported WITHOUT the Done marker, and _add_location_names writes the
        # real "Done" once everything has finished.
        geocode_follows = (not cancelled and self.location_checkbox.isChecked()
                           and bool(summary.get("targets")))
        # Same reasoning as geocode_follows below, plus: the auto-geotag pass
        # writes into the same sidecars the manual "Geotag from tracklog…"
        # button would — computed here (not just where it's acted on further
        # down) so the button's enablement can account for it too.
        tracklog_follows = (not cancelled and self.tracklog_checkbox.isChecked()
                            and self.tracklog_checkbox.isEnabled()
                            and bool(summary.get("targets")))
        self._last_targets = summary.get("targets", [])
        if cancelled:
            # "Done" + a green check would read as success — flag the abort, but
            # still show that the partial copy was kept.
            msg = f"Aborted after {copied}{renamed_txt}, {tail}"
            self.logger.warning(f"⏹ Card read aborted. {msg}")
            self.status_label.setText(f"⚠️ {msg}")
        else:
            msg = f"Copied {copied}{renamed_txt}, {tail}{self._complete_txt(summary)}"
            if geocode_follows:
                # Not "done" yet — the geocode phase below is still to come, and a
                # premature ✅/"complete" here read as success while the log kept
                # printing for another minute. _add_location_names owns the real
                # "Done" line once the whole run (copy + geocode) has finished.
                self.logger.info(f"Card copied. {msg} Looking up location names…")
                self.status_label.setText(f"⏳ {msg} Now looking up location names…")
            else:
                # No geocode step follows, so this IS the final click-to-done state.
                done_msg = f"{msg}{self._elapsed_summary()}"
                self._log_read_done(done_msg)
                self.status_label.setText(f"{self._done_marker()} {done_msg}")
        self._read_summary_msg = msg
        self._set_progress("")
        self.view_btn.setEnabled(bool(self._last_targets))
        self.pl_btn.setEnabled(bool(self._last_targets) and bool(self._last_dest))
        # Geotag-from-tracklog writes GPS/altitude into the same sidecars the
        # geocode step (and now the automatic tracklog pass) is about to fill —
        # enabling it here would let two runs race on the same .xmp files. Hold
        # off until everything that follows has actually finished; see _finish_read.
        self.geotag_btn.setEnabled(
            bool(self._last_targets) and not geocode_follows and not tracklog_follows)
        if self._last_targets and not cancelled:
            self.pl_hint.setText(
                "Next: open the day in <b>PhotoLab</b>, press <b>Ctrl-A</b> and "
                "<b>write sidecars</b>. That creates the .dop recipes — "
                "SpeciesViewer edits those but never creates them, so colours and "
                "crops you set before that live in the .xmp only. "
                "Best done with PhotoLab closed while you cull.")
            self.pl_hint.setVisible(True)
        self._fit_to_content()

        # On an abort, don't auto-run the network geocode or grey the day list —
        # leave it ready for a re-read to finish the rest.
        if not cancelled:
            if tracklog_follows:
                self._start_tracklog_auto_pass(geocode_follows)
            else:
                self._finish_read(geocode_follows)

    def _start_tracklog_auto_pass(self, geocode_follows):
        """Kick off the silent per-day auto-geotag pass on its own QThread
        (_TracklogAutoWorker) BEFORE finishing the read. This must run
        before the location-names step: "Add location names" always
        re-reads each picture's GPS fresh off its sidecar (never a value
        captured earlier in the Read), so a picture that gets GPS written
        here picks up a place name in the same run too — sequencing alone
        makes that cascade, no extra plumbing needed."""
        entries, files_by_key = self._build_tracklog_entries()
        entries_by_day: dict = {}
        for e in entries:
            if e.get("day"):
                entries_by_day.setdefault(e["day"], []).append(e)
        if not entries_by_day:
            self._finish_read(geocode_follows)
            return
        self.status_label.setText(
            self.status_label.text() + "  ⏳ Now geotagging from each day's tracklog…")
        QApplication.processEvents()   # paint that line before the worker starts
        self._tracklog_thread = QThread()
        self._tracklog_worker = _TracklogAutoWorker(entries_by_day, files_by_key, self._gpslogs_root)
        self._tracklog_worker.moveToThread(self._tracklog_thread)
        self._tracklog_thread.started.connect(self._tracklog_worker.run)
        self._tracklog_worker.finished.connect(
            lambda summary: self._on_tracklog_auto_finished(summary, geocode_follows))
        self._tracklog_thread.start()

    def _on_tracklog_auto_finished(self, summary, geocode_follows):
        self._tracklog_thread.quit()
        self._tracklog_thread.wait()
        matched = summary.get("days_matched", 0)
        no_log = summary.get("days_no_log", 0)
        no_offset = summary.get("days_no_offset", 0)
        complete = summary.get("days_complete", 0)
        logs = ", ".join(summary.get("logs_used", []))
        parts = []
        if matched:
            parts.append(f"{matched} day(s) matched ({logs})" if logs else f"{matched} day(s) matched")
        if complete:
            parts.append(f"{complete} day(s) already had GPS and altitude")
        if no_log:
            parts.append(f"{no_log} day(s) had no matching log")
        if no_offset:
            parts.append(f"{no_offset} day(s) skipped — their pictures don't record "
                         f"the camera's UTC offset")
        detail = "; ".join(parts) if parts else "nothing to do"
        self.logger.info(
            f"Tracklog auto-geotag: wrote GPS to {summary.get('gps_files', 0)}, "
            f"altitude to {summary.get('altitude_files', 0)} picture(s). {detail}.")
        self._finish_read(geocode_follows)

    def _finish_read(self, geocode_follows):
        """Whatever must happen once the read — and any auto-geotag pass
        that ran first — is truly done: the location-names step (if
        ticked), greying the day selection, and Days-lane registration.
        Split out of on_finished so both the immediate path (no auto-geotag)
        and _on_tracklog_auto_finished can reach it."""
        if geocode_follows:
            QApplication.processEvents()   # paint the "looking up…" line first
            self._add_location_names()
        self.geotag_btn.setEnabled(bool(self._last_targets))
        self._mark_run_done()
        if self.days_mode:
            self._register_ingested_days()
        self._read_done = True
        self._maybe_open_ai()

    def _register_ingested_days(self):
        """After a Days-lane card copy, register each YYYY-MM-DD folder the copy
        created (derived from the copied file paths) so it appears in the Days
        list; select the newest as the active day."""
        from utils import days_registry
        folders = []
        for t in self._last_targets:
            parent = Path(t).parent
            if days_registry.is_day_folder_name(parent.name):
                key = str(parent)
                if key not in folders:
                    folders.append(key)
        for folder in folders:
            days_registry.add_day(folder)
        panel = getattr(self.passed_parent, "days_panel", None)
        if folders:
            self.passed_parent.active_day_folder = sorted(folders)[-1]
        if panel is not None:
            panel.rebuild()
        if folders:
            self.status_label.setText(
                self.status_label.text() + f"  ·  {len(folders)} day(s) added to the Days list.")

    def _mark_run_done(self):
        """Mark the days that just landed and leave the block USABLE.

        This used to grey the whole day selection as a "done" cue, which made
        reading a card in batches impossible: after three days you could not
        tick three more without re-scanning first. Reading a big card over
        several sittings is normal, so that cue cost more than it told you —
        and the Copied column now says "104 ✓" per day, which is a better cue
        than a greyed panel because it says WHICH days and how many.

        Completed days are unticked so the next Read naturally targets what is
        left. A day with any failure stays ticked: it is exactly the one to try
        again.
        """
        table = getattr(self, "days_table", None)
        if table is None:
            return
        per_day = getattr(self, "_last_per_day", None) or {}
        table.blockSignals(True)
        try:
            for r in range(table.rowCount()):
                day_item = table.item(r, DAY_COL)
                if day_item is None:
                    continue
                tally = per_day.get(day_item.text()) or {}
                done = (tally.get("copied", 0) + tally.get("skipped", 0)) > 0
                if done and not tally.get("failed", 0):
                    day_item.setCheckState(Qt.CheckState.Unchecked)
        finally:
            table.blockSignals(False)
        self._refresh_all_days_checkbox()

    def _add_location_names(self):
        """Reverse-geocode the just-copied RAWs and write place names into their
        XMP sidecars (real write, fill-empties-only)."""
        from gui.geolocate import run_geolocate_batch
        raw_exts = RAW  # canonical table — see utils/raw_extensions.py
        targets = []
        for t in self._last_targets:
            p = Path(t)
            if p.suffix.lower() not in raw_exts:
                continue
            xmp = p.with_suffix(".xmp")
            if xmp.exists():
                targets.append((p.stem, str(xmp), [str(xmp)]))  # GPS + write both in the sidecar
        # Skip what already HAS a place name. copy_card reports every destination
        # file, skipped ones included, so a re-read of the same card handed the
        # whole set here — and while the write is fill-empties-only (nothing was
        # overwritten), it still paid a Nominatim round for pictures that were
        # named on the first read. One batched exiftool read decides it.
        already = 0
        if targets:
            from gui.geolocate import read_location_fields_batch
            have = read_location_fields_batch([t[1] for t in targets])
            fresh = []
            for label, gsrc, files in targets:
                cur = have.get(os.path.normcase(os.path.normpath(gsrc)), {})
                if cur.get("Location") or cur.get("City"):
                    already += 1
                    continue
                fresh.append((label, gsrc, files))
            targets = fresh
        base = getattr(self, "_read_summary_msg", "")
        if already:
            self.logger.info(f"Location names: {already} picture(s) already had one — "
                             f"looking up {len(targets)}.")
        if not targets:
            # Nothing left to geocode (no RAW sidecars, or every one already had a
            # place name) — the run IS finished, so the status must not stay on the
            # "looking up…" line set by on_finished.
            tail = (f" All {already} picture(s) already had place names."
                    if already else "")
            done = f"{base}{tail}{self._elapsed_summary()}"
            marker = self._done_marker()
            self.status_label.setText(
                (f"{marker} {done}" if base else f"{marker[:-2]}.{tail}").strip())
            self._log_read_done(done.strip())
            return
        self._log_phase("Looking up location names (one online lookup per new "
                        "place, the rest reuse nearby fixes)…")
        run_geolocate_batch(self, targets, dry_run=False, overwrite=False)
        self._set_progress("")
        done = f"{base} Location names added.{self._elapsed_summary()}" if base \
            else f"Location names added.{self._elapsed_summary()}"
        self.status_label.setText(f"{self._done_marker()} {done}")
        self._log_read_done(done)

    def on_view(self):
        """Open the picture grid over the just-copied files. Uses an explicit
        file list (not folder discovery) so it works regardless of date
        bucketing; RAW cards render via the embedded preview (cached_jpg_for)."""
        # Single instance: a second click while the grid is already up should just
        # raise it, not stack a second (and, with the modal parent, unresponsive)
        # grid on top — which is what made earlier views impossible to close.
        if self._preview_dlg is not None and self._preview_dlg.isVisible():
            self._preview_dlg.raise_()
            self._preview_dlg.activateWindow()
            return
        from gui.image_viewer import ImagePreviewDialog
        from gui.sidecar_provider import SidecarCollection
        from gui.preview_provider import preview_cache_dir_for
        # What the grid can actually put on a card — ASK the collection rather
        # than filtering by the much wider PICTURE table. Filtering by PICTURE
        # handed it .heic/.tif files it drops as imageless, so a card read of an
        # iPhone reported "460 copied" and then showed 49 with no explanation.
        card_exts = SidecarCollection.CARD_EXTENSIONS
        pictures = [p for p in self._last_targets
                    if Path(p).suffix.lower() in PICTURE]   # canonical table
        paths = [p for p in pictures if Path(p).suffix.lower() in card_exts]
        # Say what is being left out, by format and count. Silence here is what
        # made the missing pictures look like data loss rather than a viewer limit.
        skipped = Counter(Path(p).suffix.lower().lstrip(".").upper()
                          for p in pictures if Path(p).suffix.lower() not in card_exts)
        note = ""
        if skipped:
            kinds = ", ".join(f"{n} {ext}" for ext, n in sorted(skipped.items()))
            note = (f"  ⚠ Not shown in the culling viewer ({kinds}) — they are "
                    f"copied and will appear after Read Directory; only this "
                    f"grid cannot display them.")
        if not paths:
            self.status_label.setText(
                f"No pictures this viewer can display.{note}"
                if skipped else "No viewable pictures to preview.")
            return
        if note:
            self.status_label.setText(f"Showing {len(paths)} picture(s).{note}")
        # Keep the preview cache OUT of the project tree — inside it, the cache
        # folder shows up as a 'day' in the trip overview and would be scanned by
        # Read Directory. Park it in the per-user app dir instead.
        cache_root = str(preview_cache_dir_for(Path(self._last_dest)))
        # Sidecar-backed culling viewer: reads/edits the XMP sidecars, no pipeline.
        collection = SidecarCollection(paths, cache_root=cache_root)
        # mode="all": a freshly read card is RAW (+ the odd camera JPG), and the
        # default "jpg" mode starts the grid with the JPG-only overlay ON — which
        # on an all-RAW card showed "232 pictures" and an empty grid.
        dlg = ImagePreviewDialog(self.passed_parent, collection=collection, mode="all",
                                 cache_root=cache_root, source_label="SD-card read — culling")
        # If THIS Read SD-cards dialog is itself modal — still true for the one
        # remaining caller that opens it from inside another modal dialog (see
        # work_restore_dialog.py) — a modal window blocks input to every other
        # window EXCEPT one that is itself modal and higher on the modal stack,
        # so without this the grid would appear but couldn't be scrolled or
        # closed. Everywhere else, Read SD-cards is non-modal now, so the grid
        # stays non-modal too and behaves like any other SV window.
        if self.windowModality() != Qt.WindowModality.NonModal:
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
        dlg.finished.connect(lambda *_: setattr(self, "_preview_dlg", None))
        self._preview_dlg = dlg
        dlg.show()


