"""Shared master card operations — ONE home for the master add/delete/move/pin/
qualifier surface and the context menu that drives it.

Before this module the surface lived on RatingEditorCollection and the menu +
handlers were copied into the Master Species Viewer and the Rating Editor (a
deliberate subset), while the album day viewer had none of it. Three copies drift;
the album's was simply crippled. Now:

  * MasterCardOpsMixin  — the collection-side surface (thin wrappers over MasterDB +
    db.master_mutate). Mixed into RatingEditorCollection and the album's
    FileListPreviewCollection, so any per-UPN, master-backed collection can offer the
    ops. It reads six attributes the host collection owns:
        _master_db, _master_root, _master_read_only,
        _master_keys, _master_upn (upn -> picture row), _master_files (fname -> file row)

  * MasterCardMenuMixin — the viewer-side menu + _do_master_* handlers. Composable:
    _build_master_menu(menu, upn) populates an existing menu and returns
    {QAction: callable}, so a viewer can show it standalone (_master_context_menu) or
    append it to its own card menu (the album). Hosts provide two hooks:
        _master_status(msg)      — surface a one-line status
        _after_master_change()   — repaint after a mutation (rebuild cards)
    and `self._collection` must be a MasterCardOpsMixin.

The `ops` filter lets a viewer expose a subset (the Rating Editor's reference cards
keep their historical add/delete-only menu) without a second copy of the code.
"""

from __future__ import annotations

import logging
from pathlib import Path

from engine.exif_writer import exiftool_write

logger = logging.getLogger("speciesviewer")

# The full set of master card operations, in menu order.
ALL_OPS = ("qualifiers", "add", "pin", "delete_jpg", "move", "delete", "delete_file")


class MasterCardOpsMixin:
    """Collection-side master card operations. Depends on the host collection owning
    _master_db / _master_root / _master_read_only / _master_keys / _master_upn /
    _master_files (see module docstring)."""

    # ── read-only / gating ────────────────────────────────────────────────────
    @property
    def master_read_only(self) -> bool:
        return getattr(self, "_master_read_only", True)

    def _master_file_is_jpg(self, fname: str) -> bool:
        fr = self._master_files.get(fname)
        ext = ((fr or {}).get("WindowsExtension", "").lower()
               or Path(fname).suffix.lstrip(".").lower())
        return ext in ("jpg", "jpeg")

    def master_upn_has_jpg(self, key: str) -> bool:
        if key not in self._master_keys:
            return False
        files = self._master_upn.get(key, {}).get("Files", []) or []
        return any(self._master_file_is_jpg(f) for f in files)

    def master_upn_deletable(self, key: str) -> bool:
        """Deletable only when writes are enabled and the picture has no JPG (the hard
        'never trash a JPG' invariant)."""
        return (key in self._master_keys and not self.master_read_only
                and self._master_db is not None and not self.master_upn_has_jpg(key))

    def master_upn_moveable(self, key: str) -> bool:
        """Movable to another species under the same gate as delete — no JPG (the
        JPG best-pick move isn't implemented)."""
        return self.master_upn_deletable(key)

    def master_nonjpg_files(self, key: str) -> list[str]:
        files = self._master_upn.get(key, {}).get("Files", []) or []
        return [f for f in files if not self._master_file_is_jpg(f)]

    # ── inventory mutations (db.master_mutate) ────────────────────────────────
    def delete_master_picture(self, key: str) -> dict:
        from db.master_mutate import delete_picture
        return delete_picture(self._master_db, self._master_root, key)

    def delete_master_file(self, key: str, fname: str) -> dict:
        from db.master_mutate import delete_file
        return delete_file(self._master_db, self._master_root, key, fname)

    def delete_master_jpg(self, key: str, fname: str) -> dict:
        """Trash ONE JPG (the never-trash-a-JPG carve-out); the picture survives on its
        RAW or its other renders."""
        from db.master_mutate import delete_jpg
        return delete_jpg(self._master_db, self._master_root, key, fname)

    def add_master_files(self, key: str) -> dict:
        """Ingest any untracked files of this UPN sitting in its species folder (e.g. a
        freshly-rendered DxO JPG + its .dop)."""
        from db.master_mutate import add_upn_files
        return add_upn_files(self._master_db, self._master_root, key)

    def master_species_targets(self) -> list[dict]:
        """Every species in the master (species.csv mirror) as a Move-to target:
        [{"sci","nl","en","target_path"}]. Empty when no master DB is attached."""
        if self._master_db is None:
            return []
        return self._master_db.all_species_rows()

    def move_master_picture(self, key: str, target: dict) -> dict:
        from db.master_mutate import move_picture_to_species
        return move_picture_to_species(self._master_db, self._master_root, key, target)

    # ── best-JPG pin ──────────────────────────────────────────────────────────
    def master_jpgs(self, key: str) -> list[dict]:
        """The picture's real JPGs (thumbnails excluded), biggest first, each flagged
        `best` (publishes today) and `pinned` (a human chose it)."""
        if self._master_db is None or key not in self._master_keys:
            return []
        return self._master_db.jpgs_for_upn(key)

    def pin_master_jpg(self, key: str, fname: str) -> bool:
        """Choose which JPG is this picture's public face — or fname="" to hand the
        choice back to the code (biggest wins)."""
        if self._master_db is None:
            return False
        ok = self._master_db.apply_pin_jpg(key, fname)
        if ok:                      # keep the in-memory row the cards render from in step
            row = self._master_upn.get(key)
            if row is not None:
                fresh = self._master_db.picture(key) or {}
                row["BestJPG"] = fresh.get("BestJPG", "")
                row["JPGSize"] = fresh.get("JPGSize", "")
                row["BestJPGPinned"] = fresh.get("BestJPGPinned", "")
        return ok

    # ── qualifiers (sex / age / plumage) ──────────────────────────────────────
    def master_taxon_lineage(self, key: str) -> dict:
        """{'kingdom': …, 'phylum': …} for this master picture's species — so the picker
        offers only the qualifiers that mean something for it."""
        if self._master_db is None:
            return {}
        row = self._master_upn.get(key) or {}
        sci = (row.get("WetSoortNaam") or "").strip()
        return self._master_db.taxon_lineage(sci) if sci else {}

    def master_qualifiers(self, key: str) -> dict:
        """{group index: canonical token} currently on this master picture."""
        from gui.culling_species import split_species_qualifiers
        row = self._master_upn.get(key) or {}
        _name, selected = split_species_qualifiers(row.get("subject", "") or "")
        return selected

    def apply_master_qualifiers(self, key: str, selected: dict) -> bool:
        """Write sex / age / plumage onto a master picture.

        The SPECIES NAME IS NEVER TOUCHED. The subject is split, the qualifier tokens are
        replaced, and the same name is put back — so this cannot silently re-determine a
        picture, which is a different act with different consequences.

        The three derived columns come from specieslib.getBirdInfo, the parser of record,
        rather than a second copy of its vocabulary: what we store must be exactly what a
        full master re-read would produce."""
        from gui.culling_species import (split_species_qualifiers,
                                         build_species_subject, qualifier_fields)
        row = self._master_upn.get(key)
        if not row or self._master_db is None or self._master_root is None:
            return False
        name, _old = split_species_qualifiers(row.get("subject", "") or "")
        if not name:
            # No subject stored (older master rows) — fall back to the species name, so
            # the keyword we write still resolves to the right taxon on a re-read.
            name = (row.get("WetSoortNaam") or "").strip()
        if not name:
            logger.warning(f"master qualifiers: {key} has no species to attach them to.")
            return False
        subject = build_species_subject(name, selected)
        mf, age, sw = qualifier_fields(subject)

        rel = (row.get("TargetPath") or row.get("BestJPGPathName") or "").strip()
        file_updates: dict = {}
        for fname in (row.get("Files") or []):
            frow = self._master_files.get(fname)
            if not frow or not rel:
                continue
            if frow.get("WindowsExtension", "").lower() not in ("xmp", "jpg", "jpeg"):
                continue
            fpath = self._master_root / rel / fname
            if not fpath.exists():
                continue
            try:
                # A list-type XMP tag is REPLACED by assignment, so the sidecar ends up
                # carrying exactly this keyword — the same contract as the culling picker.
                exiftool_write(fpath, [f"-XMP:Subject={subject}"])
            except Exception as e:
                logger.error(f"master qualifiers: exiftool write failed for "
                             f"{fpath.name}: {e}")
                continue
            frow["XMP:Subject"] = subject
            file_updates[fname] = {"XMP:Subject": subject}

        row["subject"] = subject
        row["malefemale"], row["ageintext"], row["summerwinter"] = mf, age, sw
        try:
            return self._master_db.apply_qualifiers(
                key, subject=subject, malefemale=mf, ageintext=age, summerwinter=sw,
                file_updates=file_updates)
        except Exception as e:
            logger.error(f"master qualifiers: DB write failed for {key}: {e}",
                         exc_info=True)
            return False


class MasterCardMenuMixin:
    """Viewer-side master card menu + handlers. `self._collection` must be a
    MasterCardOpsMixin. Hosts provide `_master_status(msg)` and
    `_after_master_change()` (see module docstring)."""

    # ── host hooks (overridable) ──────────────────────────────────────────────
    def _master_status(self, msg: str) -> None:
        status = getattr(self, "_status", None)
        if status is not None:
            status.setText(msg)

    def _after_master_change(self) -> None:
        rebuild = getattr(self, "_rebuild_cards", None)
        if callable(rebuild):
            rebuild()

    # ── menu building ─────────────────────────────────────────────────────────
    def _build_master_menu(self, menu, upn_key: str, *, ops=None) -> dict:
        """Populate `menu` with the master card actions and return {QAction: handler}.

        `ops` limits which actions appear (default: all). Returns {} when the master is
        read-only — the caller decides whether to show a disabled hint. This lets the
        same menu be shown standalone (Master Species Viewer / Rating Editor) or appended
        to a viewer's own card menu (the album)."""
        col = self._collection
        if col is None or col.master_read_only:
            return {}
        want = set(ops) if ops is not None else set(ALL_OPS)
        handlers: dict = {}

        if "qualifiers" in want:
            a = menu.addAction("Sex / age / plumage…")
            handlers[a] = lambda: self._do_master_qualifiers(upn_key)
        if "add" in want:
            a = menu.addAction("Add new renders to the master (scan folder)")
            a.setToolTip(
                "Scan this picture's folder for a render you just made (a new JPG "
                "beside the RAW, + its .dop) and add it to the master under this "
                "picture's name — updating the best-JPG pick. It then appears here.")
            handlers[a] = lambda: self._do_master_add(upn_key)

        # Which JPG is the picture's public face. Only worth asking when there is more
        # than one to choose between — with a single render the code and the human
        # cannot disagree.
        if "pin" in want:
            jpgs = col.master_jpgs(upn_key)
            if len(jpgs) > 1:
                menu.addSeparator()
                sub = menu.addMenu("Use this JPG on the website")
                for j in jpgs:
                    mark = " ✓ shown now" if j["best"] else ""
                    pin = " 📌" if j["pinned"] else ""
                    a = sub.addAction(f"{j['fname']}   ({j['size']}){mark}{pin}")
                    a.setCheckable(True)
                    a.setChecked(bool(j["pinned"]))
                    handlers[a] = (lambda fn=j["fname"]: self._do_pin_jpg(upn_key, fn))
                if any(j["pinned"] for j in jpgs):
                    sub.addSeparator()
                    a = sub.addAction("Let the code choose (biggest wins)")
                    handlers[a] = lambda: self._do_pin_jpg(upn_key, "")
                if "delete_jpg" in want:
                    dsub = menu.addMenu("Delete a JPG (move to trash)")
                    for j in jpgs:
                        a = dsub.addAction(f"{j['fname']}   ({j['size']})")
                        handlers[a] = (lambda fn=j["fname"]:
                                       self._do_master_delete_jpg(upn_key, fn))

        if "move" in want and col.master_upn_moveable(upn_key):
            a = menu.addAction("Move to existing species…")
            handlers[a] = lambda: self._do_master_move(upn_key)

        if "delete" in want and col.master_upn_deletable(upn_key):
            menu.addSeparator()
            a = menu.addAction("Delete picture (move to trash)")
            handlers[a] = lambda: self._do_master_delete(upn_key)
            if "delete_file" in want:
                nonjpg = col.master_nonjpg_files(upn_key)
                if len(nonjpg) > 1:
                    sub = menu.addMenu("Delete single file")
                    for fn in nonjpg:
                        a = sub.addAction(fn)
                        handlers[a] = (lambda f=fn:
                                       self._do_master_delete_file(upn_key, f))
        elif "delete" in want and col.master_upn_has_jpg(upn_key):
            menu.addSeparator()
            info = menu.addAction("Delete / Move blocked — picture has a JPG (protected)")
            info.setEnabled(False)
        return handlers

    def _master_context_menu(self, upn_key: str, *, ops=None) -> None:
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        col = self._collection
        if col is None:
            return
        menu = QMenu(self)
        if col.master_read_only:
            info = menu.addAction("Master is read-only — enable in Repository Settings")
            info.setEnabled(False)
            menu.exec(QCursor.pos())
            return
        handlers = self._build_master_menu(menu, upn_key, ops=ops)
        chosen = menu.exec(QCursor.pos())
        if chosen is not None and chosen in handlers:
            handlers[chosen]()

    # ── handlers ──────────────────────────────────────────────────────────────
    def _do_master_qualifiers(self, upn_key: str):
        from gui.culling_species import (edit_master_qualifiers,
                                         qualifier_suffix_from_subject)
        col = self._collection
        if not edit_master_qualifiers(self, col, upn_key):
            return
        row = col._master_upn.get(upn_key) or {}
        suffix = qualifier_suffix_from_subject(row.get("subject", "")) or " (none)"
        self._master_status(
            f"Set to{suffix}. The page shows one best photo per form, so this can change "
            f"what is published — press ↻ Web to republish.")
        self._after_master_change()

    def _do_pin_jpg(self, upn_key: str, fname: str):
        from PyQt6.QtWidgets import QMessageBox
        if not self._collection.pin_master_jpg(upn_key, fname):
            QMessageBox.warning(self, "Choose JPG", "Could not set the picture's JPG.")
            return
        if fname:
            self._master_status(
                f"{fname} is now this picture's website JPG. "
                f"Regenerate the page to publish it.")
        else:
            self._master_status(
                "Back to the code's choice (biggest JPG wins). "
                "Regenerate the page to publish it.")
        self._after_master_change()

    def _do_master_delete_jpg(self, upn_key: str, fname: str):
        from PyQt6.QtWidgets import QMessageBox
        from db.master_mutate import MasterMutateError
        jpgs = self._collection.master_jpgs(upn_key)
        last = len([j for j in jpgs if j["fname"] != fname]) == 0
        extra = ("\n\nThis is the picture's LAST JPG — it will have no website "
                 "picture until you render a new one." if last else "")
        if QMessageBox.question(
                self, "Delete JPG",
                f"Move {fname} to the master trash?\n\nThe picture keeps its RAW and "
                f"its other renders. Restorable from .trash until purged.{extra}"
                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self._collection.delete_master_jpg(upn_key, fname)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Delete blocked", str(e))
            return
        self._master_status(
            f"Deleted {fname} — {len(res['trashed'])} file(s) moved to .trash. "
            f"Regenerate the page to publish the change.")
        self._after_master_change()

    def _do_master_delete(self, upn_key: str):
        from PyQt6.QtWidgets import QMessageBox
        from db.master_mutate import MasterMutateError
        files = self._collection.master_nonjpg_files(upn_key)
        if QMessageBox.question(
                self, "Delete master picture",
                f"Move {len(files)} file(s) to the master trash and remove this "
                f"picture from the collection?\n\nFiles can be restored from "
                f".trash until purged.") != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self._collection.delete_master_picture(upn_key)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Delete blocked", str(e))
            return
        self._master_status(
            f"Deleted — {len(res['trashed'])} file(s) moved to .trash; "
            f"species counts updated.")
        self._after_master_change()

    def _do_master_delete_file(self, upn_key: str, fname: str):
        from PyQt6.QtWidgets import QMessageBox
        from db.master_mutate import MasterMutateError
        if QMessageBox.question(
                self, "Delete file",
                f"Move {fname} to the master trash? The picture keeps its other "
                f"files.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self._collection.delete_master_file(upn_key, fname)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Delete blocked", str(e))
            return
        self._master_status(f"Deleted {fname} (moved to .trash).")
        self._after_master_change()

    def _do_master_move(self, upn_key: str):
        from PyQt6.QtWidgets import QMessageBox
        from db.master_mutate import MasterMutateError
        targets = self._collection.master_species_targets()
        if not targets:
            QMessageBox.information(self, "Move to species",
                                    "No master species list is available.")
            return
        target = self._ask_master_species(upn_key, targets)
        if not target:
            return
        # Confirm before relocating real master files on disk.
        n_files = len(self._collection.master_nonjpg_files(upn_key))
        tname = target.get("nl") or target.get("sci")
        src = getattr(self, "_current_display", "") or "its current species"
        if QMessageBox.question(
                self, "Move to species",
                f"Move this picture from “{src}” to “{tname}”?\n\n"
                f"{n_files} file(s) will be relocated into the {tname} folder, the "
                f"species keyword rewritten, and the master database updated. "
                f"This is reversible (files are snapshotted).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self._collection.move_master_picture(upn_key, target)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Move blocked", str(e))
            return
        except Exception as e:
            logger.error(f"Master move {upn_key} failed: {e}", exc_info=True)
            QMessageBox.warning(self, "Move failed", str(e))
            return
        self._master_status(
            f"Moved to {res['target']} — {len(res['moved'])} file(s) relocated; "
            f"species counts updated.")
        self._after_master_change()

    def _do_master_add(self, upn_key: str):
        from PyQt6.QtWidgets import QMessageBox
        from db.master_mutate import MasterMutateError
        try:
            res = self._collection.add_master_files(upn_key)
        except MasterMutateError as e:
            QMessageBox.warning(self, "Add files", str(e))
            return
        if not res["added"]:
            self._master_status(
                "No new files found for this picture in its species folder.")
            return
        parts = []
        if res["jpg"]:
            parts.append(f"{res['jpg']} JPG" + ("s" if res["jpg"] != 1 else ""))
        if res["meta"]:
            parts.append(f"{res['meta']} metafile" + ("s" if res["meta"] != 1 else ""))
        # A new render raises the score: a full-size JPG lifts the size multiplier and
        # the .dop adds the processed bonus. Recompute the Rate so the picture rises to
        # where it belongs (and can win the species best-pick), instead of staying at
        # its RAW-only value.
        new_rate = self._recompute_master_rate(upn_key)
        note = f" (Rate → {new_rate})" if new_rate is not None else ""
        self._master_status(f"Added {' and '.join(parts)} to the picture.{note}")
        self._after_master_change()

    def _recompute_master_rate(self, upn_key: str):
        """Recompute this master picture's Rate from its CURRENT files and persist it
        (pictures.Rate), so a just-added render is reflected at once — the same score
        the pipeline would give it on a full re-read. Returns the new Rate, or None when
        no master DB is bound. The star rating (XMP:Rating) is untouched; Rate is the
        DERIVED score computed from the stars + files."""
        col = self._collection
        db = getattr(col, "_master_db", None)
        if db is None:
            return None
        from gui.rating_editor import _compute_rate
        picture = db.picture(upn_key)
        if picture is None:
            return None
        files_dict = db.files_for_upns([upn_key])
        new_rate = _compute_rate(picture, files_dict)
        db.apply_rating(upn_key, rate=new_rate)
        row = getattr(col, "_master_upn", {}).get(upn_key)   # keep the card in step
        if row is not None:
            row["Rate"] = str(new_rate)
        return new_rate

    def _ask_master_species(self, upn_key: str, targets: list[dict]) -> dict | None:
        """Searchable picker over the whole master species list (species.csv) — the
        master's Move targets are any species ever seen, not just those with pictures.
        Returns the chosen {"sci","nl","en","target_path"} row, or None."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QDialogButtonBox, QLabel,
                                     QComboBox, QMessageBox, QCompleter)
        from PyQt6.QtCore import Qt
        name_to_row: dict[str, dict] = {}
        names: list[str] = []
        for row in targets:
            for k in ("nl", "en", "sci"):
                v = (row.get(k) or "").strip()
                if v:
                    name_to_row.setdefault(v.casefold(), row)
                    names.append(v)
        names = sorted(set(names), key=str.casefold)
        dlg = QDialog(self)
        dlg.setWindowTitle("Move to existing species")
        dlg.setMinimumWidth(440)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Move this picture to (any species ever seen):"))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(names)
        combo.setCurrentIndex(-1)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.lineEdit().setPlaceholderText("Type a Dutch, English or scientific name…")
        completer = QCompleter(names, dlg)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        combo.setCompleter(completer)
        lay.addWidget(combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        combo.setFocus()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        chosen = combo.currentText().strip()
        row = name_to_row.get(chosen.casefold())
        if row is None:
            QMessageBox.warning(self, "Unknown species",
                                f"“{chosen}” isn’t a master species — pick one "
                                f"from the list.")
            return None
        return row
