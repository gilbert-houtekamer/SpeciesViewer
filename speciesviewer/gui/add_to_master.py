# add_to_master.py: "Add Pictures to Master" — point at one folder, ingest it
# through an ephemeral micro-trip, review/edit it in a DEDICATED window, and
# (next step) commit it to the master.
#
# The micro-trip is a self-contained entity: the review window reads the clean
# scratch data (just the folder's freshly-recognised pictures), NOT the live
# trip — so there's no bleed from an existing dirty day. The active trip is
# never touched. The picture files are only read/edited in place; nothing moves
# until the commit + optional delete-originals step.

import os
import shutil
import tempfile
import logging
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QCheckBox,
    QPlainTextEdit, QFileDialog, QLabel, QApplication, QMessageBox, QWidget,
    QListWidget, QListWidgetItem, QListView, QStackedWidget,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import (
    QFont, QShortcut, QKeySequence, QTextCursor, QTextDocument, QIcon, QImage,
)

from utils.config import _config, persist_config
from gui.image_viewer import ImagePreviewDialog

logger = logging.getLogger("speciesviewer")


class MicroTripReviewDialog(ImagePreviewDialog):
    """The dedicated micro-trip review window. Reuses the Species Image Preview
    card grid (species/GPS/rating editing) but is pointed at the scratch repo so
    it shows ONLY the folder's clean, freshly-recognised pictures, and carries
    the add-to-master chrome (title + a commit bar)."""

    # The trip_root here is a throwaway scratch with no blogs — never relax the
    # never-trash-a-JPG rule from this window.
    _allow_trip_jpg_delete = False

    def __init__(self, parent, micro_trip, report):
        self._micro_trip = micro_trip
        self._report = report or {}
        # A species fix here writes the real XMP, but the scratch is ephemeral and
        # belongs to no live trip — don't trigger the trip's "re-run Read &
        # Summarize" (it targets the active trip, not the scratch). Re-Scan instead.
        self._suppress_rerun = True
        super().__init__(parent,
                         image_dir=micro_trip.input_dir,
                         trip_root_dir=micro_trip.scratch_repo,
                         mode="all",
                         start_species=True)   # focus on the pictures that will be committed
        folder = Path(micro_trip.input_dir).name
        self.setWindowTitle(f"Add to Master — {folder}")
        self._add_commit_bar(self._report)

    def _color_delete_trash_root(self):
        # The trip_root here is the throwaway scratch (discarded on close), so a
        # blue/purple cull must NOT trash there. Send it beside the real pictures
        # so it's recoverable, like a normal trip cull.
        from pathlib import Path as _P
        return _P(self._micro_trip.input_dir) / ".trash"

    def _add_commit_bar(self, report):
        bar = QHBoxLayout()
        self._commit_info = QLabel()
        self._commit_info.setStyleSheet("color: #444;")
        self.delete_originals_cb = QCheckBox("Delete originals after commit")
        self.delete_originals_cb.setToolTip("After committing, delete the folder's "
                                            "files that are confirmed present in the master.")
        self.commit_btn = QPushButton("Commit to Master")
        self.commit_btn.clicked.connect(self._commit)
        bar.addWidget(self._commit_info)
        bar.addStretch(1)
        bar.addWidget(self.delete_originals_cb)
        bar.addWidget(self.commit_btn)
        self.layout().addLayout(bar)
        self._refresh_commit_bar_counts()

    def _refresh_commit_bar_counts(self):
        """Keep the commit line honest as species are named on the cards here — the
        initial scan predates those edits, so a fixed label would read '0 with a
        species' forever."""
        n = self._report.get("pictures", 0)
        try:
            # A landscape's topic leaf ("2025") sits in the species-name slot after
            # ingest, so it appears in species_keys too — subtract the blog-extra
            # set so it is counted once, as a landscape.
            extras = set(self._collection.blog_extra_keys())
            ident = len(set(self._collection.species_keys()) - extras)
            land = len(extras)
        except Exception:
            ident = self._report.get("identified", 0)
            land = self._report.get("landscapes", 0)
        untag = max(n - ident - land, 0)
        land_txt = f" · {land} landscape{'' if land == 1 else 's'}" if land else ""
        self._commit_info.setText(
            f"{n} pictures · {ident} with a species{land_txt} · {untag} untagged "
            f"(set a species or mark as landscape; untagged are not added)")
        # Enabled as long as there are pictures: you may still be about to tag them.
        # Commit re-reads species fresh and adds only the tagged ones.
        self.commit_btn.setEnabled(n > 0)

    def refresh_after_species_change(self, updated_upns):
        # Repaint the affected cards (base class) AND update the commit line so the
        # "with a species" count reflects what was just named.
        super().refresh_after_species_change(updated_upns)
        self._refresh_commit_bar_counts()

    def _commit(self):
        """Run the proven master pipeline (sv2→sv3→sv5→sv6→sv7) scoped to this
        one folder. Re-reads the real files (with any species/GPS edits the user
        just made), copies them into the master, and updates the website."""
        import tempfile
        import configparser
        from utils.batch_config import generate_svconfig_ini
        import batch.sv2ReadDirExif as sv2
        import batch.sv3WritePictures as sv3
        import batch.sv5BuildWebsite as sv5
        import batch.sv6Freeze as sv6
        import batch.sv7WriteCommit as sv7
        from PyQt6.QtWidgets import QProgressDialog

        folder = self._micro_trip.input_dir
        recurse = getattr(self._micro_trip, "recurse", False)
        # Re-read the pool so species just set on the cards here (not only in
        # Lightroom) are counted — the initial scan predates those edits.
        try:
            self._micro_trip.ingest()
            self._report = self._micro_trip.summary()
        except Exception as e:
            logger.info(f"add_to_master: pre-commit re-summary failed: {e}")
        ident = self._report.get("identified", 0)
        land = self._report.get("landscapes", 0)
        if ident == 0 and land == 0:
            QMessageBox.information(
                self, "Commit to Master",
                "No picture has a species or landscape marker yet. Set a species on a "
                "card here (or tag it in Lightroom), or mark it as a landscape, then "
                "commit — untagged pictures are not added.")
            return
        parts = []
        if ident:
            parts.append(f"{ident} identified picture{'' if ident == 1 else 's'}")
        if land:
            parts.append(f"{land} landscape{'' if land == 1 else 's'}")
        what = " and ".join(parts)
        if QMessageBox.question(
                self, "Commit to Master",
                f"Add {what} in\n{folder}\n"
                f"to the master and update the website?\n\n"
                f"Landscapes are filed under topics/landscape/<year>/. Untagged "
                f"pictures are not added. This writes to the master (undoable via "
                f"the master .trash).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return

        # Write to a temp svconfig so the user's svconfig.ini is left alone.
        tmp_cfg = os.path.join(tempfile.gettempdir(), "sv_addmaster_svconfig.ini")
        generate_svconfig_ini(svconfig_path=tmp_cfg, master_mode=True,
                              single_dir=folder, recurse=recurse)
        cfg = configparser.ConfigParser()
        cfg.read(tmp_cfg)

        steps = [
            ("Reading & recognising species", sv2.main),
            ("Summarising pictures", sv3.main),
            ("Building master website", sv5.main),
            ("Freezing", sv6.main),
            ("Committing", sv7.main),
        ]
        prog = QProgressDialog("Committing to master…", None, 0, len(steps), self)
        prog.setWindowTitle("Add to Master")
        prog.setWindowModality(Qt.WindowModality.ApplicationModal)
        prog.setCancelButton(None)
        prog.setMinimumDuration(0)
        prog.show()
        self.commit_btn.setEnabled(False)
        try:
            for i, (label, fn) in enumerate(steps):
                prog.setLabelText(f"{label}…")
                prog.setValue(i)
                QApplication.processEvents()
                fn(None, cfg)
            prog.setValue(len(steps))
            # The batch commit rewrote the master CSVs directly (outside the DB
            # journal), so the SQLite cache the album + master viewer read is now
            # stale — the new pictures are on disk but the album's date-range query
            # still returns the old rows. Rebuild the cache and repaint the master-
            # derived overviews so they show without a restart.
            self._refresh_master_views()
            deleted_msg = ""
            if self.delete_originals_cb.isChecked():
                deleted_msg = self._delete_originals(folder)
            QMessageBox.information(
                self, "Add to Master",
                f"Added {what} to the master and updated the website.{deleted_msg}")
            self.commit_btn.setText("Committed ✓")
            # If this folder is a registered Day, mark it done automatically
            # (it stays in the Days list; nothing is deleted). No-op otherwise.
            try:
                from utils import days_registry
                if days_registry.find_day(folder):
                    days_registry.set_status(folder, days_registry.STATUS_DONE)
                    panel = getattr(self.main_window, "days_panel", None)
                    if panel is not None:
                        panel.rebuild()
            except Exception as e:
                logger.info(f"add_to_master: could not mark day done: {e}")
        except Exception as e:
            logger.warning(f"add_to_master: commit failed: {e}")
            QMessageBox.warning(self, "Add to Master", f"Commit failed:\n{e}")
            self.commit_btn.setEnabled(True)
        finally:
            prog.close()

    def _refresh_master_views(self):
        """Rebuild the master SQLite cache from the just-written CSVs, then repaint
        the album + trip overviews so newly-added pictures appear at once (an album
        is a live date-range query over the master — a new picture on a covered day
        shows up automatically once the cache is fresh)."""
        try:
            from db.master_db import MasterDB
            MasterDB(self._micro_trip.master_root).ensure_fresh()
        except Exception as e:
            logger.warning(f"add_to_master: master DB refresh failed: {e}")
        mw = self.main_window
        for mod, fn_name in (("gui.virttrip_overview", "populate_virttrip_overview"),
                             ("gui.trip_overview", "populate_trip_overview")):
            try:
                import importlib
                getattr(importlib.import_module(mod), fn_name)(mw)
            except Exception as e:
                logger.info(f"add_to_master: overview repaint ({fn_name}): {e}")

    def _delete_originals(self, folder):
        """Delete the folder's files now confirmed present in the master (reuses
        the master-authoritative trip-delete scan — only byte-confirmed files are
        removed). Returns a message fragment for the result dialog."""
        import engine.trip_delete_scan as tds
        try:
            plan = tds.scan_trip(folder, self._micro_trip.master_root, "")
            if not plan.get("ok"):
                return f"\nOriginals kept — could not verify against master: {plan.get('error')}"
            nf, npx, errs = tds.execute_scan_delete(plan)
            msg = f"\nDeleted {npx} original picture(s) ({nf} files) now safe in the master."
            held = plan["totals"].get("held_files", 0) + plan["totals"].get("keep_files", 0)
            if held:
                msg += (f" Kept {held} file(s) the master doesn't hold an exact copy of "
                        f"(see log).")
                self._log_kept_files(plan)
            if errs:
                msg += f" {len(errs)} could not be deleted (see log)."
            return msg
        except Exception as e:
            logger.warning(f"add_to_master: delete originals failed: {e}")
            return f"\nOriginals kept — delete failed: {e}"

    def _log_kept_files(self, plan):
        """List every file the scan kept (the master has no exact copy) so the
        user can see what — and why. 'held' = the picture is in the master but
        not this exact file/rendition; 'keep' = the picture isn't in the master
        at all. Both are left on disk untouched."""
        why = {"held": "picture in master, but not this exact file",
               "keep": "picture not in master"}
        for verdict in ("held", "keep"):
            for d in plan.get("days", []):
                for it in d.get(verdict, []):
                    logger.info(f"add_to_master: kept {os.path.basename(it['path'])}"
                                f" — {why[verdict]}  ({it['path']})")


class AddToMasterDialog(QDialog):
    _DROP_IDLE = ("⬇  Drag pictures here to build a pool\n"
                  "(RAW + JPG + sidecars — drop a whole set at once), then Scan → Review → Commit")
    _DROP_ACTIVE = "⬇  Release to add these files to the pool"
    _DROP_STYLE_IDLE = ("border: 2px dashed #bbb; border-radius: 6px; color: #777; "
                        "padding: 8px; background: #fafafa;")
    _DROP_STYLE_ACTIVE = ("border: 2px dashed #4a90d9; border-radius: 6px; color: #2b6cb0; "
                          "padding: 8px; background: #eaf3fb;")

    def __init__(self, main_window, fixed_dir=None):
        super().__init__(main_window)
        self.main_window = main_window
        self.resize(780, 520)
        self._mt = None                 # the live micro-trip (scratch on disk)
        self._report = None
        self._review_dlg = None
        self._fixed_dir = None
        self._drop_pool = None          # temp dir collecting dragged-in files
        self.setAcceptDrops(True)
        self._build_ui()
        self._apply_mode(fixed_dir)

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QVBoxLayout(self)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)

        # Drag-and-drop pool: drop a loose selection (RAW + JPG + sidecars) straight
        # onto here instead of assembling a folder. The pool is a VISIBLE tray — one
        # thumbnail per UPN with a file count — that you build up before scanning.
        # Files are copied into a temp pool that feeds the same scan/review/commit
        # path as Browse; nothing is interpreted until you press Scan pool.
        self.pool_stack = QStackedWidget()
        self.pool_hint = QLabel(self._DROP_IDLE)
        self.pool_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pool_hint.setStyleSheet(self._DROP_STYLE_IDLE)
        self.pool_hint.setWordWrap(True)
        self.pool_list = QListWidget()
        self.pool_list.setViewMode(QListView.ViewMode.IconMode)
        self.pool_list.setIconSize(QSize(110, 110))
        self.pool_list.setGridSize(QSize(150, 150))
        self.pool_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.pool_list.setMovement(QListView.Movement.Static)
        self.pool_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.pool_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pool_list.setWordWrap(True)
        self.pool_list.setAcceptDrops(False)
        self.pool_list.setStyleSheet("QListWidget { border: 2px dashed #bbb; "
                                     "border-radius: 6px; background: #fafafa; }")
        self.pool_stack.addWidget(self.pool_hint)   # page 0 — empty
        self.pool_stack.addWidget(self.pool_list)    # page 1 — has files
        lay.addWidget(self.pool_stack, 1)

        from gui.image_loader import AsyncImageLoader
        self._loader = AsyncImageLoader(self)

        row = QHBoxLayout()
        self.dir_edit = QLineEdit()
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._browse)
        row.addWidget(self.dir_edit)
        row.addWidget(self.browse_btn)
        lay.addLayout(row)

        self.recurse_cb = QCheckBox("Include nested folders")
        self.recurse_cb.setChecked(_config.get("addmaster", "recurse", fallback="0") == "1")
        self.recurse_cb.toggled.connect(self._save_recurse)
        lay.addWidget(self.recurse_cb)

        btns = QHBoxLayout()
        self.scan_btn = QPushButton("Scan folder")
        self.scan_btn.clicked.connect(self._scan)
        self.review_btn = QPushButton("Review && edit…")
        self.review_btn.setToolTip("Open the micro-trip's pictures in a dedicated "
                                   "window to fix species, GPS and descriptions.")
        self.review_btn.clicked.connect(self._review)
        self.review_btn.setEnabled(False)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.clicked.connect(self._discard)
        self.discard_btn.setEnabled(False)
        btns.addWidget(self.scan_btn)
        btns.addWidget(self.review_btn)
        btns.addWidget(self.discard_btn)
        btns.addStretch(1)
        lay.addLayout(btns)

        self.out = QPlainTextEdit(readOnly=True)
        self.out.setFont(QFont("Consolas", 10))
        self.out.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(self.out)

        # Let file drops fall through to the dialog's handler from anywhere, rather
        # than a child (folder field / report box) swallowing them as pasted text.
        self.dir_edit.setAcceptDrops(False)
        self.out.setAcceptDrops(False)

        lay.addWidget(self._build_find_bar())

        self.hint = QLabel("Next: after editing, commit to the master (coming next).")
        self.hint.setStyleSheet("color: gray;")
        lay.addWidget(self.hint)

        close = QPushButton("Close")
        close.clicked.connect(self.close)
        lay.addWidget(close)

        # Ctrl+F (and F3 / Shift+F3) search the report text.
        QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self._open_find)
        QShortcut(QKeySequence.StandardKey.FindNext, self).activated.connect(self._find_next)
        QShortcut(QKeySequence.StandardKey.FindPrevious, self).activated.connect(self._find_prev)

    # ── mode / session ────────────────────────────────────────────────────────
    def _apply_mode(self, fixed_dir):
        """Configure for one of the two no-trip ingest contexts. The same dialog
        (and the same micro-trip engine) serves both; only the folder field and
        the wording differ. Re-applied on every open so the shared instance can
        switch between them.

        Master mode (fixed_dir=None): editable folder + Browse — add a loose
        folder, e.g. what's left in a trip after it was moved to the master.
        Days mode (fixed_dir set): the path is locked to that day's folder."""
        self._fixed_dir = fixed_dir or None
        if self._fixed_dir:
            self.setWindowTitle(f"Add to Master — {Path(self._fixed_dir).name}")
            self.intro.setText(
                "Add this day's pictures to the master.\n"
                "Pictures need a species (tag them in Lightroom first); untagged "
                "ones are listed but not added.")
            self.dir_edit.setText(self._fixed_dir)
            self.dir_edit.setReadOnly(True)
            self.dir_edit.setStyleSheet("color: gray;")
            self.browse_btn.setVisible(False)
            self.pool_stack.setVisible(False)
            self.scan_btn.setText("Scan folder")
            self.pool_stack.setMaximumHeight(0)
            self.out.setMaximumHeight(16777215)
        else:
            self.setWindowTitle("Add Pictures to Master")
            self.intro.setText(
                "Add loose pictures to the master — drag a few files onto the box "
                "below, or Browse to a folder. Handy for a trip left-over or a few "
                "hand-picked renders.\n"
                "Pictures need a species; set it here in Review (or tag in Lightroom "
                "first). Untagged ones are listed but not added.")
            self.dir_edit.setReadOnly(False)
            self.dir_edit.setStyleSheet("")
            self.browse_btn.setVisible(True)
            self.pool_stack.setVisible(True)
            self.scan_btn.setText("Scan folder")
            self._neutral_pool_layout()
            self.dir_edit.setText(_config.get("addmaster", "lastdir", fallback=""))

    def open_session(self, fixed_dir=None):
        """Show the dialog configured for Master (fixed_dir=None) or Days
        (fixed_dir = the day folder) use. A fresh target invalidates any prior
        scratch; Days mode scans immediately so the user lands on the summary."""
        self._close_review()
        self._cleanup_scratch()
        self._cleanup_drop_pool()
        self._apply_mode(fixed_dir)
        self._reset_scan_state()
        self.show()
        self.raise_()
        self.activateWindow()
        if self._fixed_dir:
            try:
                self._scan()
            except Exception as e:
                logger.info(f"add_to_master: initial scan failed: {e}")

    # ── drag-and-drop pool ─────────────────────────────────────────────────────
    def _drops_allowed(self) -> bool:
        # Only in Master mode: a Days session is locked to that day's folder, so a
        # loose drop there would be misleading.
        return self._fixed_dir is None

    def _dropped_paths(self, mime) -> list[str]:
        """Local file paths from a drop. A dropped folder contributes its files
        (recursively); non-local URLs are ignored."""
        paths = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_dir():
                paths.extend(str(f) for f in p.rglob("*") if f.is_file())
            elif p.is_file():
                paths.append(str(p))
        return paths

    def _highlight_pool(self, active: bool):
        page = self.pool_stack.currentWidget()
        if page is self.pool_hint:
            self.pool_hint.setText(self._DROP_ACTIVE if active else self._DROP_IDLE)
            self.pool_hint.setStyleSheet(self._DROP_STYLE_ACTIVE if active
                                         else self._DROP_STYLE_IDLE)
        else:
            border = "#4a90d9" if active else "#bbb"
            bg = "#eaf3fb" if active else "#fafafa"
            self.pool_list.setStyleSheet(
                f"QListWidget {{ border: 2px dashed {border}; border-radius: 6px; "
                f"background: {bg}; }}")

    def dragEnterEvent(self, event):
        if self._drops_allowed() and event.mimeData().hasUrls():
            event.acceptProposedAction()
            # Give the tray the room while dragging (the report box shrinks away),
            # so the drop target is the obvious, dominant thing on screen.
            self._show_pool_state(scanned=False)
            self._highlight_pool(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._drops_allowed() and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._highlight_pool(False)
        # If nothing has been pooled yet, don't leave the tray blown up over an
        # empty hint — settle back to the balanced default.
        if not self._pool_has_files():
            self._neutral_pool_layout()

    def dropEvent(self, event):
        self._highlight_pool(False)
        if not self._drops_allowed():
            return
        srcs = self._dropped_paths(event.mimeData())
        if not srcs:
            return
        pool = self._ensure_drop_pool()
        copied = 0
        for s in srcs:
            try:
                # Re-dropping a file just refreshes it — no duplicate complaints.
                shutil.copy2(s, os.path.join(pool, os.path.basename(s)))
                copied += 1
            except OSError as e:
                logger.warning(f"add_to_master: could not pool {s}: {e}")
        # Building the pool does NOT interpret it — that waits for Scan pool. A fresh
        # drop invalidates any prior scan, so disable Review/Discard first, then let
        # the tray rebuild set the pool summary + the "Scan pool" affordance.
        self.dir_edit.setText(pool)
        self._reset_scan_state()
        self._rebuild_pool_tray()
        self._show_pool_state(scanned=False)

    # ── the pool tray (one card per UPN) ───────────────────────────────────────
    def _rebuild_pool_tray(self):
        """Redraw the tray from the pool folder: one card per UPN (the picture),
        its file count, and a thumbnail loaded off-thread. The whole file set of a
        UPN — RAW, render, sidecars — folds onto a single card."""
        from engine.handling import deriveUniquePictureName
        self.pool_list.clear()
        self._loader.cancel_all()
        pool = self._drop_pool
        files = []
        if pool and os.path.isdir(pool):
            files = [f for f in sorted(os.listdir(pool))
                     if os.path.isfile(os.path.join(pool, f))]
        if not files:
            self.pool_stack.setCurrentWidget(self.pool_hint)
            self.scan_btn.setText("Scan folder")
            return

        groups: dict[str, list[str]] = {}
        for name in files:
            groups.setdefault(deriveUniquePictureName(name), []).append(name)

        for upn, members in sorted(groups.items()):
            short = self._short_upn_label(upn)
            item = QListWidgetItem(f"{short}\n{len(members)} file"
                                   f"{'s' if len(members) != 1 else ''}")
            item.setToolTip(upn + "\n" + "\n".join(sorted(members)))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.pool_list.addItem(item)
            self._load_thumb(item, [os.path.join(pool, m) for m in members])

        self.pool_stack.setCurrentWidget(self.pool_list)
        n_pool = len(files)
        self.scan_btn.setText("Scan pool")
        self.out.setPlainText(
            f"Pool holds {len(groups)} picture(s) in {n_pool} file(s). "
            f'Press "Scan pool" to read species, date and rating.')

    @staticmethod
    def _short_upn_label(upn: str) -> str:
        # UPNs are long ("2023 feb gran canaria-ds5_5772"); the distinctive part is
        # the frame token after the last space/dash ("ds5_5772"). Fall back to the
        # tail if there's no separator.
        base = os.path.basename(upn)
        cut = max(base.rfind(" "), base.rfind("-"))
        token = base[cut + 1:] if cut >= 0 else base
        return token[-14:] if len(token) > 14 else token

    def _load_thumb(self, item: QListWidgetItem, member_paths: list[str]):
        """Load one thumbnail for the UPN card off-thread. Prefers a JPG (instant);
        otherwise extracts a RAW/HEIC preview via the shared exiftool path."""
        from pathlib import Path as _P
        jpgs = [p for p in member_paths if _P(p).suffix.lower() in (".jpg", ".jpeg")]
        src = jpgs[0] if jpgs else next(
            (p for p in member_paths
             if _P(p).suffix.lower() not in (".dop", ".xmp")), None)
        if not src:
            return
        cache_dir = _P(tempfile.gettempdir()) / "sv_droppool_prev"

        def producer():
            from gui.preview_provider import cached_jpg_for
            jpg = cached_jpg_for(_P(src), cache_dir)
            return QImage(str(jpg)) if jpg else QImage()

        def on_ready(px, _item=item):
            if not px.isNull():
                _item.setIcon(QIcon(px))
        self._loader.request(producer, on_ready, target_w=220)

    def _ensure_drop_pool(self) -> str:
        if not self._drop_pool or not os.path.isdir(self._drop_pool):
            self._drop_pool = tempfile.mkdtemp(prefix="sv_droppool_")
        return self._drop_pool

    def _cleanup_drop_pool(self):
        self._loader.cancel_all()
        self.pool_list.clear()
        self.pool_stack.setCurrentWidget(self.pool_hint)
        if self._drop_pool and os.path.isdir(self._drop_pool):
            try:
                shutil.rmtree(self._drop_pool, ignore_errors=True)
            except Exception as e:
                logger.info(f"add_to_master: drop-pool cleanup: {e}")
        self._drop_pool = None

    def _pool_has_files(self) -> bool:
        return bool(self._drop_pool and os.path.isdir(self._drop_pool)
                    and any(os.scandir(self._drop_pool)))

    def _neutral_pool_layout(self):
        """The balanced default (no drag in progress, pool empty): a modest tray and
        a normal report box."""
        self.pool_stack.setMaximumHeight(120)
        self.pool_stack.setMinimumHeight(0)
        self.out.setMaximumHeight(16777215)
        self.out.setMinimumHeight(0)

    def _show_pool_state(self, scanned: bool):
        """Two working layouts (Master mode only):
        - building the pool  → tray dominant, report shrinks to a strip;
        - after Scan pool     → report dominant, tray shrinks to a couple of rows."""
        if self._fixed_dir is not None:
            return
        if scanned:
            self.pool_stack.setMaximumHeight(190)
            self.pool_stack.setMinimumHeight(0)
            self.out.setMaximumHeight(16777215)
            self.out.setMinimumHeight(180)
        else:
            self.pool_stack.setMaximumHeight(16777215)
            self.pool_stack.setMinimumHeight(200)
            self.out.setMaximumHeight(60)
            self.out.setMinimumHeight(0)

    # ── Find-in-report bar (Ctrl+F) ───────────────────────────────────────────
    def _build_find_bar(self) -> QWidget:
        self.find_row = QWidget()
        fr = QHBoxLayout(self.find_row)
        fr.setContentsMargins(0, 0, 0, 0)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find in report…  (Enter = next, Esc = close)")
        self.find_input.returnPressed.connect(self._find_next)
        self.find_input.textChanged.connect(self._find_incremental)
        prev_btn = QPushButton("▲"); prev_btn.setFixedWidth(28)
        next_btn = QPushButton("▼"); next_btn.setFixedWidth(28)
        _nav_pad = "QPushButton { padding: 4px 0px; }"
        prev_btn.setStyleSheet(_nav_pad); next_btn.setStyleSheet(_nav_pad)
        prev_btn.setToolTip("Previous match"); next_btn.setToolTip("Next match")
        prev_btn.clicked.connect(self._find_prev)
        next_btn.clicked.connect(self._find_next)
        self.find_status = QLabel("")
        self.find_status.setStyleSheet("color: #888;")
        for w in (self.find_input, prev_btn, next_btn, self.find_status):
            fr.addWidget(w)
        fr.addStretch(1)
        # Esc closes the find bar only (scoped to the box so it doesn't reject the dialog).
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.find_input)
        esc.setContext(Qt.ShortcutContext.WidgetShortcut)
        esc.activated.connect(self._close_find)
        self.find_row.setVisible(False)
        return self.find_row

    def _open_find(self):
        self.find_row.setVisible(True)
        self.find_input.setFocus()
        self.find_input.selectAll()

    def _close_find(self):
        self.find_row.setVisible(False)
        self.find_status.clear()
        self.out.setFocus()

    def _find_incremental(self, _text):
        # Keep the current match selected as the query grows: search from the
        # start of the current selection.
        cursor = self.out.textCursor()
        cursor.setPosition(cursor.selectionStart())
        self.out.setTextCursor(cursor)
        self._do_find(backward=False)

    def _find_next(self):
        self._do_find(backward=False)

    def _find_prev(self):
        self._do_find(backward=True)

    def _do_find(self, backward: bool):
        text = self.find_input.text()
        if not text:
            self.find_status.clear()
            return
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if self.out.find(text, flags):
            self.find_status.clear()
            return
        # Wrap around to the other end and try once more.
        cursor = self.out.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End if backward
                            else QTextCursor.MoveOperation.Start)
        self.out.setTextCursor(cursor)
        self.find_status.setText("" if self.out.find(text, flags) else "no match")

    def _save_recurse(self, checked: bool):
        if not _config.has_section("addmaster"):
            _config.add_section("addmaster")
        _config.set("addmaster", "recurse", "1" if checked else "0")
        try:
            persist_config()
        except Exception as e:
            logger.info(f"add_to_master: could not persist recurse: {e}")

    def _browse(self):
        start = (self.dir_edit.text().strip()
                 or _config.get("project", "picturedirectory", fallback=""))
        chosen = QFileDialog.getExistingDirectory(self, "Choose a picture folder", start)
        if chosen:
            self.dir_edit.setText(chosen)

    @staticmethod
    def _clean(s):
        return (s or "").replace('"', "").replace("'", "").strip()

    # ── scan (ingest into a fresh scratch) ────────────────────────────────────
    def _scan(self):
        import engine.micro_trip as micro_trip
        input_dir = self._clean(self.dir_edit.text())
        master = self._clean(_config.get("master", "masterbiotadatabase", fallback=""))
        web = self._clean(_config.get("master", "masterbiotawebroot", fallback="/nature/")) or "/nature/"
        if not input_dir or not os.path.isdir(input_dir):
            self.out.setPlainText("Pick a valid folder first.")
            return
        if not master:
            self.out.setPlainText("No master database is configured.")
            return

        # Only Master mode remembers the folder; a locked Day path must not
        # overwrite the Master "last folder" the user browses to. The drop-pool is a
        # throwaway temp dir — never remember it (it won't exist next launch).
        if not self._fixed_dir and input_dir != self._drop_pool:
            if not _config.has_section("addmaster"):
                _config.add_section("addmaster")
            _config.set("addmaster", "lastdir", input_dir)
            try:
                persist_config()
            except Exception as e:
                logger.info(f"add_to_master: could not persist lastdir: {e}")

        # Drop any previous scratch (and its review window) before scanning again.
        self._close_review()
        self._cleanup_scratch()

        self.scan_btn.setEnabled(False)
        self.out.setPlainText("Scanning… (reading EXIF and recognising species)")
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            mt = micro_trip.MicroTrip(input_dir, master, web_root=web,
                                      recurse=self.recurse_cb.isChecked())
            mt.setup()
            mt.ingest()
            report = mt.summary()
            self._mt = mt
            self._report = report
            self.out.setPlainText(micro_trip.format_summary(report))
            # Open Review whenever there are pictures at all — naming the unnamed ones
            # is exactly what Review is for; gating it on "already identified" locked
            # you out of the one case you need it. Commit still only adds the tagged.
            has_pics = report.get("ok", False) and report.get("pictures", 0) > 0
            self.review_btn.setEnabled(has_pics)
            self.discard_btn.setEnabled(report.get("ok", False))
            # Scanning the pool = interpretation done: hand the room to the report.
            if input_dir == self._drop_pool:
                self._show_pool_state(scanned=True)
        except Exception as e:
            logger.warning(f"add_to_master: scan failed: {e}")
            self.out.setPlainText(f"Scan failed: {e}")
            self._cleanup_scratch()
        finally:
            QApplication.restoreOverrideCursor()
            self.scan_btn.setEnabled(True)

    # ── review (dedicated window on the clean scratch data) ───────────────────
    def _review(self):
        if not self._mt or not self._mt.scratch_repo:
            return
        self._close_review()
        try:
            self._review_dlg = MicroTripReviewDialog(self.main_window, self._mt, self._report or {})
            self._review_dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            self._review_dlg.show()
            self._review_dlg.raise_()
            self._review_dlg.activateWindow()
        except Exception as e:
            logger.warning(f"add_to_master: could not open review window: {e}")
            QMessageBox.warning(self, "Add Pictures to Master",
                                f"Could not open the review window:\n{e}")

    # ── discard / cleanup ─────────────────────────────────────────────────────
    def _discard(self):
        self._close_review()
        self._cleanup_scratch()
        self._cleanup_drop_pool()
        self.review_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)
        self.out.appendPlainText("\nDiscarded — scratch and drop-pool removed.")

    def _close_review(self):
        if self._review_dlg is not None:
            try:
                self._review_dlg.close()
            except Exception:
                pass
            self._review_dlg = None

    def _cleanup_scratch(self):
        if self._mt is not None:
            try:
                self._mt.teardown()
            except Exception as e:
                logger.info(f"add_to_master: scratch cleanup: {e}")
            self._mt = None
            self._report = None

    def _reset_scan_state(self):
        """Clean slate for a (re)open: the scratch from a previous session is
        gone, so the old scan results are stale and Review/Discard are dead.
        Keep the remembered directory; require a fresh Scan."""
        self.review_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)
        self.out.setPlainText('Click "Scan folder" to read this folder.')

    def closeEvent(self, event):
        # Closing ends the session: close the review window and remove the
        # scratch (XMP edits already live on the real files), then leave the
        # dialog clean so the next open isn't showing stale results.
        self._close_review()
        self._cleanup_scratch()
        self._cleanup_drop_pool()
        self._reset_scan_state()
        super().closeEvent(event)

    def showEvent(self, event):
        # Reopened (the instance is reused) with no live scratch -> stale state.
        # Reset so the slow scan is simply redone, keeping the remembered folder.
        super().showEvent(event)
        if self._mt is None:
            self._reset_scan_state()
