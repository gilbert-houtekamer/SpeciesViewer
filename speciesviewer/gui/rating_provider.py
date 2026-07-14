"""Rating Editor implementation of the picture-provider contract.

Wraps the Rating Editor's loaded data (a FilePicture with merged per-day
UPN/file dicts, plus the per-day objects for write-back routing) as a
PictureCollection of PictureItems. This is the richer engine — per-UPN,
trip/master scope, computed Rate — so it shakes out the interface first.

Editability here: rating + colour are writable (they drive Rate/culling, and
must stay editable even for master pictures); description/headline/species are
read-only for now (write-back needs per-day CSV routing — see memory
rating-editor-and-preview-convergence), so those capability flags are False.

Not yet wired into RatingEditorDialog — that's the next step (the dialog will
own a RatingEditorCollection and drop its inline grid/write logic).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtGui import QImage

from gui.picture_provider import (
    PictureItem, PictureCollection, ExifSummary, PixmapCallback, exif_from_rows, sweep_dop)
from gui.image_loader import AsyncImageLoader
from gui.preview_provider import preview_cache_dir_for
from gui.rating_editor import _compute_rate, _safe_int
from engine.exif_writer import exiftool_write
from engine.sidecarlib import push_metadata_to_dop
from utils.sidecar_policy import (
    color_rating_to_xmp_enabled, color_rating_to_dop_enabled,
)

_DXO_RAW_EXTS = ("orf", "nef", "cr2", "cr3", "arw", "rw2", "raf", "dng")

# The camera filter's value for a picture that never had a camera. NOT "" — an empty
# string was silently dropped by both the combo and the filter set, so the 1,421 master
# pictures whose source recorded no body (the waarneming.nl imports) could not be
# selected at all. A real sentinel makes them a bucket you can actually pick.
NO_CAMERA = "\x00no-camera"

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = {".nef", ".orf", ".cr2", ".arw", ".rw2"}


class RatingEditorItem(PictureItem):
    """Thin view over one UPN row; writes delegate to the collection.

    A trip item is rating/colour-editable. A master item (pulled from the
    master DB as a reference for judging website-worthiness) is read-only — its
    capability flags are False so the card greys the controls."""

    can_edit_headline = False
    can_edit_species = False

    def __init__(self, collection: "RatingEditorCollection", upn_key: str):
        self._c = collection
        self._key = upn_key
        self._master = collection.is_master(upn_key)
        # A whole collection can be view-only (e.g. the Album / virtual-trip
        # viewer, whose pictures ARE the master's shared files — editing would
        # be a master write-back, deferred). Otherwise trip pictures are always
        # editable; master reference pictures only when the master is not in
        # read-only mode (Phase 2).
        editable = (not collection.view_read_only) and \
                   ((not self._master) or (not collection.master_read_only))
        self.can_edit_rating = editable
        self.can_edit_color = editable
        # Description write-back uses the same per-day CSV routing as
        # rating/colour, so it works for trip pictures. Master description
        # write-back is not yet implemented.
        self.can_edit_description = (not collection.view_read_only) and not self._master

    @property
    def from_master(self) -> bool:
        return self._master

    @property
    def from_album(self) -> bool:
        """In the Album viewer's full-master scope, a master picture that also
        belongs to the album (its UPN is one of the album's extracted pictures)
        is tagged 'from Album' instead of 'from master', so the user can see
        which of the species' master pictures came from this trip's range."""
        return self._c.is_album_member(self._key)

    @property
    def _row(self) -> dict:
        return self._c._upn_row(self._key)

    # identity
    @property
    def key(self) -> str:
        return self._key

    @property
    def display_name(self) -> str:
        return self._c.species_name(self._row) or self._key

    @property
    def qualifier_suffix(self) -> str:
        from gui.culling_species import (qualifier_suffix_from_fields,
                                         qualifier_suffix_from_subject)
        r = self._row
        # Master/trip picture rows carry the derived fields; fall back to parsing
        # the raw subject when they're absent.
        suffix = qualifier_suffix_from_fields(
            r.get("malefemale", ""), r.get("ageintext", ""), r.get("summerwinter", ""))
        return suffix or qualifier_suffix_from_subject(r.get("subject", "") or "")

    # read fields
    @property
    def species(self) -> str:
        return self._c.species_name(self._row)

    @property
    def headline(self) -> str:
        return self._row.get("Headline", "")

    @property
    def description(self) -> str:
        return self._row.get("Description", "")

    @property
    def rating(self) -> int:
        return _safe_int(self._row.get("Rating", "0"))

    @property
    def color(self) -> str:
        return self._row.get("Label", "")

    @property
    def rate(self):
        r = self._row.get("Rate")
        return _safe_int(r) if r not in (None, "") else None

    @property
    def date(self) -> str:
        return self._row.get("DateTimeOriginal", "")

    @property
    def has_jpg(self) -> bool:
        return self._c.jpg_path(self._key) is not None

    @property
    def file_exts(self) -> list[str]:
        files = self._row.get("Files", [])
        if not isinstance(files, list):
            return []
        return sorted({Path(f).suffix.lstrip(".").upper() for f in files if f})

    @property
    def exif(self) -> ExifSummary:
        return self._c.exif_for(self._key)

    # write ops — no-op when this item isn't editable, so a bulk-apply or
    # keyboard path can never write past a read-only (Album) viewer or a
    # read-only master reference card.
    def set_rating(self, value: int) -> None:
        if not self.can_edit_rating:
            return
        self._c.apply(self._key, new_stars=value)

    def set_color(self, value: str) -> None:
        if not self.can_edit_color:
            return
        self._c.apply(self._key, new_color=value)

    def set_description(self, value: str) -> None:
        if not self.can_edit_description:
            return
        self._c.apply_description(self._key, value)

    # async image
    def request_thumbnail(self, target_w: int, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, target_w, on_ready)

    def request_full_pixmap(self, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, None, on_ready)


class RatingEditorCollection(PictureCollection):
    def __init__(self, fpr, day_objects, upn_day_map, *,
                 jpg_only: bool = True, species_lang: str = "NL",
                 loader: AsyncImageLoader | None = None,
                 view_read_only: bool = False):
        self.fpr = fpr
        self._day_objects = day_objects      # dict day->(Files,Pictures), or None in Day scope
        self._upn_day_map = upn_day_map      # upn_key -> day
        self._jpg_only = jpg_only
        self._color_filter: set[str] = set()   # lowercased colour names; empty = all
        self._camera_filter: set[str] = set()  # exact CameraModel strings; empty = all
        # Card ordering: "rate" (Rate high→low, the default), "time_asc"/"time_desc"
        # (capture time — groups near-identical burst frames for culling), or
        # "camera" (by body, then capture time).
        self._sort_key = "rate"
        self._species_lang = species_lang
        self._loader = loader or AsyncImageLoader()
        # Whole-collection view-only: every card non-editable (Album viewer).
        self._view_read_only = bool(view_read_only)
        # Master reference state (Trip+Master scope) — read-only pictures pulled
        # from the master DB for the selected species. Empty until set_master().
        # With the SQLite-backed MasterDB (Plan B) we no longer preload the 85k
        # master rows: only the currently-selected species' pictures + their
        # files live in these dicts, repopulated on each rebuild(filter_name).
        self._master_db = None               # MasterDB | None
        self._master_upn: dict = {}          # upn_key -> master picture row
        self._master_files: dict = {}        # filename -> master file row
        self._master_root: Path | None = None
        self._include_master = False
        self._master_read_only = True        # Phase 2: master writes opt-in
        # master_full (Album viewer "Master" scope): show the species' COMPLETE
        # master set — every picture, including the album's own in-range ones,
        # and NO trip/album cards (so nothing is shown twice). Plain Trip+Master
        # keeps False (trip cards + master-minus-trip reference).
        self._master_full = False
        self._master_keys: set[str] = set()  # keys currently shown from master
        # Lazy: don't scan all trip pictures (each jpg_path() does several
        # Path.exists() calls — thousands of filesystem stats over D:/E: and
        # Dropbox) until a species is actually selected. The dialog shows
        # "select a species" first; _rebuild_cards calls rebuild(filter) then.
        self._order: list[str] = []

    # ── master reference wiring ───────────────────────────────────────────
    def set_master(self, master_db, master_root, *, include: bool = True,
                   read_only: bool = True, full: bool = False) -> None:
        """Attach the master SQLite cache so rebuild() can append reference
        cards for the selected species. include=False keeps it attached but
        hidden (e.g. when scope is not Trip+Master).

        read_only (Phase 2): when True (the safe default, e.g. a laptop without
        the full master) master cards stay non-editable; when False, editing a
        master card writes XMP to the master image files and records the change
        in the master SQLite WAL + CSV via MasterDB.apply_rating.

        The per-species rows are pulled lazily in rebuild() — _master_upn /
        _master_files stay empty until then.
        """
        self._master_db = master_db
        self._master_root = Path(master_root) if master_root else None
        self._include_master = bool(include and master_db is not None)
        self._master_read_only = bool(read_only)
        self._master_full = bool(full)
        self._master_upn = {}
        self._master_files = {}
        self._master_keys = set()

    @property
    def master_read_only(self) -> bool:
        return getattr(self, "_master_read_only", True)

    @property
    def view_read_only(self) -> bool:
        return getattr(self, "_view_read_only", False)

    def is_master(self, key: str) -> bool:
        return key in self._master_keys

    def is_album_member(self, key: str) -> bool:
        """Full-master scope only: True when this master picture's UPN is also
        one of the album's own (extracted) pictures."""
        return self._master_full and key in self.fpr.upn.UniquePictureName_dict

    # ── master add/delete (Phase 3 of the master cache) ───────────────────────
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
        """A master picture is deletable only when writes are enabled and it has
        no JPG (the hard 'never trash a JPG' invariant)."""
        return (key in self._master_keys and not self._master_read_only
                and self._master_db is not None and not self.master_upn_has_jpg(key))

    def master_nonjpg_files(self, key: str) -> list[str]:
        files = self._master_upn.get(key, {}).get("Files", []) or []
        return [f for f in files if not self._master_file_is_jpg(f)]

    def delete_master_picture(self, key: str) -> dict:
        from db.master_mutate import delete_picture
        return delete_picture(self._master_db, self._master_root, key)

    def master_upn_moveable(self, key: str) -> bool:
        """A master picture can be moved to another species only when master
        writes are enabled and it has no JPG — same gate as delete (the JPG
        best-pick move isn't implemented)."""
        return self.master_upn_deletable(key)

    def master_species_targets(self) -> list[dict]:
        """Every species in the master (species.csv mirror) as a Move-to target:
        [{"sci","nl","en","target_path"}]. Empty when no master DB is attached."""
        if self._master_db is None:
            return []
        return self._master_db.all_species_rows()

    def move_master_picture(self, key: str, target: dict) -> dict:
        from db.master_mutate import move_picture_to_species
        return move_picture_to_species(self._master_db, self._master_root, key, target)

    def resync_master_metadata(self, keys) -> list[dict]:
        """Re-read XMP:Rating / XMP:Label from disk for these master pictures.

        The master only ever hears about a rating that SpeciesViewer itself wrote. Set
        two stars in PhotoLab or Lightroom and it lands in the .xmp — and Refresh does
        not notice, because Refresh looks for NEW files and for JPGs whose pixels moved,
        and an already-tracked sidecar with a new star inside it is neither.

        The picture's rating is the MAX of XMP:Rating across its files and its label is
        the XMP's (else the JPG's) — mirroring the pipeline (projectlib.py:1052-1055,
        findBestItem), so a re-read here and a full master re-read cannot disagree.

        ONE exiftool call for the whole set, via an argfile: a species can carry 800
        sidecars and JPGs, and per-file spawns would make this unusable. Returns what
        changed."""
        import json as _json
        import os
        import subprocess
        import tempfile
        from gui.rating_editor import _compute_rate, _safe_int

        if self._master_db is None or self._master_root is None:
            return []
        targets: list[tuple[str, str, str]] = []      # (key, fname, abspath)
        for key in keys:
            row = self._master_upn.get(key)
            if not row:
                continue
            rel = (row.get("TargetPath") or row.get("BestJPGPathName") or "").strip()
            if not rel:
                continue
            for fname in (row.get("Files") or []):
                frow = self._master_files.get(fname)
                if not frow:
                    continue
                if frow.get("WindowsExtension", "").lower() not in ("xmp", "jpg", "jpeg"):
                    continue
                p = self._master_root / rel / fname
                if p.exists():
                    targets.append((key, fname, str(p)))
        if not targets:
            return []

        by_src: dict = {}
        af = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             encoding="utf-8") as fh:
                af = fh.name
                for _k, _f, p in targets:
                    fh.write(p + "\n")
            out = subprocess.run(
                ["exiftool", "-j", "-q", "-charset", "filename=utf8",
                 "-XMP:Rating", "-XMP:Label", "-@", af],
                capture_output=True, text=True, encoding="utf-8", timeout=300).stdout
            for r in (_json.loads(out) if out.strip() else []):
                by_src[os.path.normcase(os.path.normpath(r.get("SourceFile", "")))] = r
        except Exception as e:
            logger.warning(f"master metadata resync: exiftool failed: {e}")
            return []
        finally:
            if af:
                try:
                    os.unlink(af)
                except OSError:
                    pass

        # group the fresh values per picture
        per_key: dict = {}
        for key, fname, p in targets:
            r = by_src.get(os.path.normcase(os.path.normpath(p)))
            if r is None:
                continue
            per_key.setdefault(key, []).append((fname, r))

        changed = []
        for key, items in per_key.items():
            row = self._master_upn.get(key)
            file_updates: dict = {}
            for fname, r in items:
                frow = self._master_files.get(fname) or {}
                upd = {}
                disk_rating = r.get("Rating", None)
                if disk_rating not in (None, "") and \
                        str(disk_rating) != str(frow.get("XMP:Rating", "") or ""):
                    upd["XMP:Rating"] = str(disk_rating)
                disk_label = str(r.get("Label", "") or "")
                if disk_label != str(frow.get("XMP:Label", "") or ""):
                    upd["XMP:Label"] = disk_label
                if upd:
                    frow.update(upd)
                    self._master_files[fname] = frow
                    file_updates[fname] = upd
            if not file_updates:
                continue

            # Picture rating = MAX over its files (projectlib.computeRating); label
            # prefers the XMP over the JPG (findBestItem).
            best = 0
            label = ""
            for fname in (row.get("Files") or []):
                frow = self._master_files.get(fname) or {}
                ext = frow.get("WindowsExtension", "").lower()
                if ext not in ("xmp", "jpg", "jpeg"):
                    continue
                best = max(best, _safe_int(frow.get("XMP:Rating", "0") or 0))
                lab = str(frow.get("XMP:Label", "") or "")
                if lab and (not label or ext == "xmp"):
                    label = lab
            old_rating, old_label = (str(row.get("Rating", "") or ""),
                                     str(row.get("Label", "") or ""))
            row["Rating"] = str(best)
            row["Label"] = label
            new_rate = _compute_rate(row, self._master_files)
            row["Rate"] = str(new_rate)
            try:
                self._master_db.apply_rating(
                    key, rating=best, label=label, rate=new_rate,
                    file_updates=file_updates)
            except Exception as e:
                logger.error(f"master metadata resync: DB write failed for {key}: {e}",
                             exc_info=True)
                continue
            changed.append({"key": key, "rating": (old_rating, str(best)),
                            "label": (old_label, label), "rate": new_rate})
            logger.info(f"metadata resync: {key} rating {old_rating or '0'} -> {best}, "
                        f"label {old_label or '-'} -> {label or '-'}, rate {new_rate}")
        return changed

    def delete_master_file(self, key: str, fname: str) -> dict:
        from db.master_mutate import delete_file
        return delete_file(self._master_db, self._master_root, key, fname)

    def add_master_files(self, key: str) -> dict:
        """Ingest any untracked files of this UPN sitting in its species folder
        (e.g. a freshly-rendered DxO JPG + its .dop)."""
        from db.master_mutate import add_upn_files
        return add_upn_files(self._master_db, self._master_root, key)

    def resync_master_jpgs(self, key: str) -> list[dict]:
        """Correct the master where a tracked JPG changed size on disk (a PhotoLab
        re-export over the same name). Returns [{fname, old, new}, …]."""
        from db.master_mutate import resync_jpg_sizes
        return resync_jpg_sizes(self._master_db, self._master_root, key)

    def refresh_master_species(self, pictures: list[dict]) -> dict:
        """Ingest new files + correct changed JPG sizes for a whole species — ONE folder
        listing and ONE exiftool call, not one of each per picture."""
        from db.master_mutate import refresh_species
        return refresh_species(self._master_db, self._master_root, pictures)

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

    def delete_master_jpg(self, key: str, fname: str) -> dict:
        """Trash ONE JPG (the never-trash-a-JPG carve-out); the picture survives on
        its RAW or its other renders."""
        from db.master_mutate import delete_jpg
        return delete_jpg(self._master_db, self._master_root, key, fname)

    def _upn_row(self, key: str) -> dict:
        if key in self._master_keys:
            return self._master_upn.get(key, {})
        return self.fpr.upn.UniquePictureName_dict.get(key, {})

    def _files_dict_for(self, key: str) -> dict:
        return self._master_files if key in self._master_keys else self.fpr.fi.files_dict

    # ── ordering / filtering ──────────────────────────────────────────────
    def rebuild(self, filter_name: str | None = None, *,
                forced_sci: str | None = None) -> None:
        # Resolve the scientific name of the selected species (from the trip
        # rows) so master pictures can be matched on WetSoortNaam — robust
        # across the NL/Eng/Sci display languages. The Master Species Viewer has
        # no trip rows to resolve from, so it passes the sci in directly.
        sci = (forced_sci or "").strip().casefold() or None
        if sci is None and filter_name and self._include_master and self._master_db is not None:
            for row in self.fpr.upn.UniquePictureName_dict.values():
                if self.species_name(row) == filter_name:
                    sci = (row.get("WetSoortNaam") or "").strip().casefold()
                    break

        # Pull just this species' master pictures + their files from the DB
        # (vs the old preload-all-85k approach). Routing helpers below see
        # _master_keys before jpg_path() is called, so they pick master rows.
        self._master_keys = set()
        self._master_upn = {}
        self._master_files = {}
        if sci:
            pics = self._master_db.pictures_for_species(sci)
            # "Master" (full) scope keeps every master picture of the species;
            # Trip+Master drops the ones the trip already owns to avoid showing
            # a picture twice.
            if not self._master_full:
                trip_keys = self.fpr.upn.UniquePictureName_dict
                pics = [p for p in pics if p["UniquePictureName"] not in trip_keys]
            self._master_upn = {p["UniquePictureName"]: p for p in pics}
            if self._master_upn:
                self._master_files = self._master_db.files_for_upns(
                    self._master_upn.keys())
            self._master_keys = set(self._master_upn.keys())

        rows = []   # (key, row) — the sort key is derived in _sorted_keys
        # In full-master scope the master set already contains the album's own
        # pictures, so don't also add the trip/album rows (no duplicates).
        if not self._master_full:
            for key, row in self.fpr.upn.UniquePictureName_dict.items():
                if filter_name and self.species_name(row) != filter_name:
                    continue
                if self._jpg_only and self.jpg_path(key) is None:
                    continue
                if self._color_filter and self._row_color(row) not in self._color_filter:
                    continue
                if self._camera_filter and self._camera_key(row) not in self._camera_filter:
                    continue
                rows.append((key, row))
        for key in list(self._master_keys):
            if self._jpg_only and self.jpg_path(key) is None:
                self._master_keys.discard(key)   # not shown → not a master key
                continue
            if (self._color_filter
                    and self._row_color(self._master_upn[key]) not in self._color_filter):
                continue  # view filter only — keep it a master key for routing
            if (self._camera_filter
                    and self._camera_key(self._master_upn[key]) not in self._camera_filter):
                continue
            rows.append((key, self._master_upn[key]))

        self._order = self._sorted_keys(rows)

    def _sorted_keys(self, rows) -> list[str]:
        """Order (key, row) pairs by the active sort key and return just the keys.
        Capture time uses DateTimeOriginal, whose 'YYYY:MM:DD HH:MM:SS' form sorts
        chronologically as a plain string; the UPN key is the stable tie-break."""
        sk = self._sort_key
        if sk == "time_asc":
            rows.sort(key=lambda kr: ((kr[1].get("DateTimeOriginal", "") or ""), kr[0]))
        elif sk == "time_desc":
            rows.sort(key=lambda kr: ((kr[1].get("DateTimeOriginal", "") or ""), kr[0]),
                      reverse=True)
        elif sk == "camera":
            # Unknown-camera pictures sort last ("~"), then by capture time.
            rows.sort(key=lambda kr: ((self._row_camera(kr[1]) or "~~~").casefold(),
                                      (kr[1].get("DateTimeOriginal", "") or ""), kr[0]))
        else:   # "rate" — Rate high→low (default)
            rows.sort(key=lambda kr: _safe_int(kr[1].get("Rate", 0) or 0), reverse=True)
        return [k for k, _ in rows]

    def set_jpg_only(self, value: bool) -> None:
        self._jpg_only = value

    def set_color_filter(self, colors) -> None:
        """Restrict the shown cards to these colour labels (empty = all).
        'grey' matches uncoloured pictures — same convention as the culling
        grid's ColorFilterBar."""
        self._color_filter = {c.lower() for c in (colors or set())}

    @staticmethod
    def _row_color(row: dict) -> str:
        return (row.get("Label", "") or "grey").lower()

    @staticmethod
    def _row_camera(row: dict) -> str:
        return (row.get("CameraModel", "") or "").strip()

    @staticmethod
    def _camera_key(row: dict) -> str:
        """The value the camera FILTER matches on. A picture with no CameraModel gets
        the NO_CAMERA sentinel rather than "", so it can be selected — 1,421 master
        pictures (the waarneming.nl imports, whose source never recorded a body) have
        no camera, and with "" they matched nothing and the filter could not reach
        them at all."""
        return RatingEditorCollection._row_camera(row) or NO_CAMERA

    def set_camera_filter(self, models) -> None:
        """Restrict the shown cards to these camera bodies (empty = all).
        Matched exactly against each picture's CameraModel field, or NO_CAMERA for
        the pictures that never had one."""
        self._camera_filter = {m for m in (models or set()) if m}

    def set_sort(self, key: str) -> None:
        self._sort_key = (key if key in ("rate", "time_asc", "time_desc", "camera")
                          else "rate")

    def available_cameras(self, filter_name: str | None = None) -> list[str]:
        """Distinct camera bodies among the loaded pictures, for the filter combo.
        Scoped to `filter_name` (the selected species) when given, so the list
        only offers bodies actually present in what's on screen — a species shot
        only on the OM-1 won't also list the phone. Scans both the trip rows and
        the currently-loaded master rows, so it serves the Master Species Viewer
        (trip rows empty there) as well as the Trip viewer."""
        models = set()
        cameraless = False
        for source in (self.fpr.upn.UniquePictureName_dict, self._master_upn):
            for row in source.values():
                if filter_name and self.species_name(row) != filter_name:
                    continue
                m = self._row_camera(row)
                if m:
                    models.add(m)
                else:
                    cameraless = True
        out = sorted(models, key=str.casefold)
        if cameraless:
            out.append(NO_CAMERA)     # last: it is the leftovers bucket, not a body
        return out

    def set_species_lang(self, lang: str) -> None:
        self._species_lang = lang

    # ── PictureCollection ─────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._order)

    def item(self, index: int) -> RatingEditorItem:
        return RatingEditorItem(self, self._order[index])

    def index_of(self, key: str) -> int:
        return self._order.index(key)

    def cancel_pending_loads(self) -> None:
        self._loader.cancel_all()

    # ── helpers shared with items ─────────────────────────────────────────
    def species_name(self, row: dict) -> str:
        lang = self._species_lang
        if lang == "EN":
            return (row.get("EngSoortNaam") or row.get("NLSoortNaam")
                    or row.get("WetSoortNaam") or "").strip()
        if lang == "Sci":
            return (row.get("WetSoortNaam") or row.get("NLSoortNaam")
                    or row.get("EngSoortNaam") or "").strip()
        return (row.get("NLSoortNaam") or row.get("EngSoortNaam")
                or row.get("WetSoortNaam") or "").strip()

    def _rep_file_row(self, upn_key: str) -> dict:
        """The representative (non-XMP) file row for EXIF — prefer a real
        photo file over the sidecar."""
        row = self._upn_row(upn_key)
        files_dict = self._files_dict_for(upn_key)
        files = row.get("Files", [])
        if not isinstance(files, list):
            return {}
        fallback = {}
        for fname in files:
            frow = files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() != "xmp":
                return frow
            fallback = frow
        return fallback

    def _master_jpg_path(self, upn_key: str) -> Path | None:
        """Resolve a master picture's JPG on disk: master_root + relative
        TargetPath/BestJPGPathName + BestJPG filename. Master CSV paths are
        RELATIVE (e.g. 'biota/.../pica pica/'), unlike the absolute trip paths."""
        if self._master_root is None:
            return None
        row = self._master_upn.get(upn_key, {})
        best_jpg = (row.get("BestJPG") or "").strip()
        rel_dir = (row.get("TargetPath") or row.get("BestJPGPathName") or "").strip()
        if not best_jpg or not rel_dir:
            return None
        p = self._master_root / rel_dir / best_jpg
        if p.exists() and p.suffix.lower() in (".jpg", ".jpeg"):
            return p
        return None

    def jpg_path(self, upn_key: str) -> Path | None:
        if upn_key in self._master_keys:
            return self._master_jpg_path(upn_key)
        row = self.fpr.upn.UniquePictureName_dict.get(upn_key, {})
        best = row.get("BestJPGPathName", "")
        if best:
            p = Path(best)
            if p.exists() and p.suffix.lower() in (".jpg", ".jpeg"):
                return p
        files = row.get("Files", [])
        if not isinstance(files, list):
            return None
        for fname in files:
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and frow.get("WindowsExtension", "").lower() in ("jpg", "jpeg"):
                p = Path(frow.get("WindowsFileName", ""))
                if p.exists():
                    return p
        return None

    def rep_full_path(self, upn_key: str) -> Path | None:
        """Representative RAW (else first non-XMP, else XMP) file Path for a TRIP
        UPN — the file SpeciesInfoPanel edits. None for master keys (read-only)."""
        if upn_key in self._master_keys:
            return None
        row = self.fpr.upn.UniquePictureName_dict.get(upn_key, {})
        files = row.get("Files", [])
        if not isinstance(files, list):
            return None
        fallback = None
        for fname in files:
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            p = Path(frow.get("WindowsFileName", ""))
            if not p.exists():
                continue
            if ("." + frow.get("WindowsExtension", "").lower()) in _RAW_EXTS:
                return p
            if fallback is None:
                fallback = p
        return fallback

    def species_editor_context(self, key: str):
        """Enable the large viewer's embedded species editor for TRIP pictures.
        Master reference pictures and whole-collection view-only mode (Album
        viewer) stay read-only, matching the grid's edit rules."""
        if self._view_read_only or key in self._master_keys:
            return None
        p = self.rep_full_path(key)
        return (self.fpr, [p]) if p else None

    def _raw_thumb_path(self, upn_key: str) -> Path | None:
        """Cached JPEG preview for the first RAW in the UPN, extracting it (via
        exiftool) if absent. Runs on a worker thread — keep it self-contained."""
        row = self._upn_row(upn_key)
        files_dict = self._files_dict_for(upn_key)
        files = row.get("Files", [])
        if not isinstance(files, list):
            return None
        master = upn_key in self._master_keys
        rel_dir = ((row.get("TargetPath") or row.get("BestJPGPathName") or "").strip()
                   if master else "")
        for fname in files:
            frow = files_dict.get(fname)
            if not frow:
                continue
            if ("." + frow.get("WindowsExtension", "").lower()) not in _RAW_EXTS:
                continue
            # Master file paths are relative → rebuild from master_root; trip
            # paths are absolute in WindowsFileName.
            if master and self._master_root is not None and rel_dir:
                raw_path = self._master_root / rel_dir / fname
            else:
                raw_path = Path(frow.get("WindowsFileName", ""))
            if not raw_path.exists():
                continue
            cache_dir = preview_cache_dir_for(raw_path.parent)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / (raw_path.stem + ".jpg")
            if cache_path.exists():
                return cache_path
            # One shared, serialised exiftool — never a per-RAW process storm
            # (which intermittently yielded an empty preview → blank cards).
            from gui.preview_extract import extract_preview_to
            if extract_preview_to(raw_path, cache_path):
                return cache_path
        return None

    def raw_path(self, upn_key: str) -> Path | None:
        """Absolute path of the first RAW file in the UPN (master-aware), for
        opening it in DxO PhotoLab. Master file paths are relative to the master
        root; trip paths are absolute in WindowsFileName."""
        row = self._upn_row(upn_key)
        files_dict = self._files_dict_for(upn_key)
        files = row.get("Files", [])
        if not isinstance(files, list):
            return None
        master = upn_key in self._master_keys
        rel_dir = ((row.get("TargetPath") or row.get("BestJPGPathName") or "").strip()
                   if master else "")
        for fname in files:
            frow = files_dict.get(fname)
            if not frow:
                continue
            if ("." + frow.get("WindowsExtension", "").lower()) not in _RAW_EXTS:
                continue
            if master and self._master_root is not None and rel_dir:
                p = self._master_root / rel_dir / fname
            else:
                p = Path(frow.get("WindowsFileName", ""))
            if p.exists():
                return p
        return None

    def file_directory(self, upn_key: str) -> str:
        """Absolute directory holding the UPN's files (master-aware). Prefers an
        existing image file so 'open in Explorer' lands in the right folder."""
        for p in (self.jpg_path(upn_key), self.raw_path(upn_key)):
            if p:
                return str(p.parent)
        # Fall back to any file row with an on-disk path.
        row = self._upn_row(upn_key)
        files_dict = self._files_dict_for(upn_key)
        master = upn_key in self._master_keys
        rel_dir = ((row.get("TargetPath") or row.get("BestJPGPathName") or "").strip()
                   if master else "")
        files = row.get("Files", [])
        if isinstance(files, list):
            for fname in files:
                frow = files_dict.get(fname)
                if not frow:
                    continue
                if master and self._master_root is not None and rel_dir:
                    p = self._master_root / rel_dir / fname
                else:
                    p = Path(frow.get("WindowsFileName", ""))
                if p.exists():
                    return str(p.parent)
        return ""

    def _image_path(self, upn_key: str) -> Path | None:
        jpg = self.jpg_path(upn_key)
        if jpg:
            return jpg
        if self._jpg_only:
            return None
        return self._raw_thumb_path(upn_key)

    def display_path(self, upn_key: str) -> Path | None:
        """Synchronous best-display path (JPG or extracted RAW thumb) — used by
        the large view until its loading is made async (Phase 2)."""
        return self._image_path(upn_key)

    def request_image(self, upn_key: str, target_w, on_ready: PixmapCallback) -> None:
        def produce():
            p = self._image_path(upn_key)   # may extract RAW — off the GUI thread
            if p and p.exists():
                img = QImage(str(p))
                return img if not img.isNull() else None
            return None
        self._loader.request(produce, on_ready, target_w=target_w)

    def exif_for(self, upn_key: str) -> ExifSummary:
        upn = self._upn_row(upn_key)
        return exif_from_rows(upn, self._rep_file_row(upn_key))

    # ── write-back (rating/colour), with per-day CSV routing ──────────────
    def apply(self, upn_key: str, *, new_stars: int | None = None,
              new_color: str | None = None) -> int | None:
        if upn_key in self._master_keys:
            if self._master_read_only:
                return None   # master cards are read-only on this machine
            return self._apply_master_rating(
                upn_key, new_stars=new_stars, new_color=new_color)
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key)
        if not upn_row:
            return None
        files = upn_row.get("Files", [])
        if not isinstance(files, list):
            files = []

        wrote_file = False
        raw_path = None
        for fname in files:
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            ext = frow.get("WindowsExtension", "").lower()
            if ext in _DXO_RAW_EXTS and raw_path is None:
                raw_path = frow.get("WindowsFileName", "")  # for the .dop write
            if ext not in ("xmp", "jpg", "jpeg"):
                continue
            fpath = Path(frow.get("WindowsFileName", ""))
            if not fpath.exists():
                continue
            args = []
            if new_stars is not None:
                args.append(f"-XMP:Rating={new_stars}")
            if new_color is not None:
                args.append(f"-XMP:Label={new_color}")
            # Colour/rating in the .xmp can be suppressed (Sidecar Behaviour) since
            # PhotoLab mistreats it; internal state below still updates.
            suppress = ext == "xmp" and not color_rating_to_xmp_enabled()
            if args and not suppress:
                try:
                    exiftool_write(fpath, args)
                    wrote_file = True
                except Exception as e:
                    logger.error(f"exiftool write failed for {fpath.name}: {e}")
            if new_stars is not None:
                frow["XMP:Rating"] = str(new_stars)
            if new_color is not None:
                frow["XMP:Label"] = new_color
        # Colour/rating into the DxO .dop (PhotoLab reads it on 'import sidecars').
        if raw_path and color_rating_to_dop_enabled():
            push_metadata_to_dop(raw_path, color=new_color, rating=new_stars)

        if new_stars is not None:
            upn_row["Rating"] = str(new_stars)
        if new_color is not None:
            upn_row["Label"] = new_color

        new_rate = _compute_rate(upn_row, self.fpr.fi.files_dict)
        upn_row["Rate"] = str(new_rate)
        self._persist_upn(upn_key, wrote_file)
        return new_rate

    def _persist_upn(self, upn_key: str, wrote_file: bool) -> None:
        if self._day_objects is None:
            # Day scope — fpr.fi / fpr.upn point at the single day's files
            try:
                self.fpr.upn.write_project_summary()
            except Exception as e:
                logger.error(f"Failed to write data_pictures.csv: {e}")
            if wrote_file:
                try:
                    self.fpr.fi.write_project_files()
                except Exception as e:
                    logger.error(f"Failed to write data_files.csv: {e}")
            return
        day = self._upn_day_map.get(upn_key)
        if not day or day not in self._day_objects:
            return
        fi_day, upn_day = self._day_objects[day]
        try:
            upn_day.write_project_summary()
        except Exception as e:
            logger.error(f"Failed to write data_pictures.csv for {day}: {e}")
        if wrote_file:
            try:
                fi_day.write_project_files()
            except Exception as e:
                logger.error(f"Failed to write data_files.csv for {day}: {e}")

    def persist_after_species_change(self, upn_keys) -> None:
        """Write the CSVs for a set of UPNs after their species was changed via
        fpr.updateSubjects (Set-species / Move-to-species). In Trip scope fpr.fi /
        fpr.upn are merged, path-less objects, so updateSubjects' own CSV write is
        a no-op there — the real files are the per-day objects, which share the
        same row dicts (so they already reflect the edit). Persist each affected
        day once. In Day scope fpr IS the single day's object with a real path, so
        persist it directly."""
        if self._day_objects is None:
            try:
                self.fpr.upn.write_project_summary()
                self.fpr.fi.write_project_files()
            except Exception as e:
                logger.error(f"persist_after_species_change (day scope) failed: {e}")
            return
        days = {self._upn_day_map.get(k) for k in upn_keys}
        days.discard(None)
        for day in days:
            if day not in self._day_objects:
                continue
            fi_day, upn_day = self._day_objects[day]
            try:
                upn_day.write_project_summary()
                fi_day.write_project_files()
            except Exception as e:
                logger.error(f"persist_after_species_change failed for {day}: {e}")

    def apply_description(self, upn_key: str, value: str) -> None:
        if upn_key in self._master_keys:
            return  # master description write-back not yet implemented
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key)
        if not upn_row:
            return
        files = upn_row.get("Files", [])
        if not isinstance(files, list):
            files = []
        wrote_file = False
        for fname in files:
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() not in ("xmp", "jpg", "jpeg"):
                continue
            fpath = Path(frow.get("WindowsFileName", ""))
            if not fpath.exists():
                continue
            try:
                exiftool_write(fpath, [f"-XMP:Description={value}"])
                wrote_file = True
            except Exception as e:
                logger.error(f"exiftool write failed for {fpath.name}: {e}")
            frow["XMP:Description"] = value
        upn_row["Description"] = value
        self._persist_upn(upn_key, wrote_file)

    def delete_by_color(self, color: str, trash_root: Path,
                        day_hint: str = "") -> tuple[int, int, int]:
        """Move all trip UPNs with the given color label to trash.

        Returns (total_matching, protected_count, deleted_count).
        Skips master keys and JPG-bearing UPNs (HARD INVARIANT).
        trash_root should be <trip_root>/.trash; files land at
        trash_root/<day>/<key>/<file> with day from _upn_day_map or day_hint.
        """
        matching, _ = self.color_matches(color)
        return self.delete_trip_keys(matching, trash_root, day_hint)

    def color_matches(self, color: str, species: str | None = None,
                      day: str | None = None) -> tuple[list[str], list[str]]:
        """UPNs labelled `color`, split into (trip_keys, master_keys).

        Scoping (WP: species-view delete): when `species` is given, only that
        species' pictures match. `day` only gates master inclusion — the trip
        set is already day-limited by what day scope loads (readTripDay loads a
        single day and leaves _upn_day_map empty), so it must NOT be re-filtered
        here. Master pictures are included only outside day scope, and are
        already limited to the loaded species (see rebuild) — so a species view
        can blue-delete master pictures too. With neither, it is trip-wide (the
        culling default)."""
        wanted = (color or "").lower()
        trip_keys = []
        for k, row in self.fpr.upn.UniquePictureName_dict.items():
            if (row.get("Label", "") or "").lower() != wanted:
                continue
            if k in self._master_keys:
                continue
            if species and self.species_name(row) != species:
                continue
            trip_keys.append(k)
        master_keys = []
        if not day:
            for k in self._master_keys:
                row = self._master_upn.get(k, {})
                if (row.get("Label", "") or "").lower() == wanted:
                    master_keys.append(k)
        return trip_keys, master_keys

    def delete_trip_keys(self, keys, trash_root: Path,
                         day_hint: str = "") -> tuple[int, int, int]:
        """Move the given TRIP UPNs to the trip trash. Returns
        (total, protected, deleted). JPG-bearing UPNs are skipped (HARD
        INVARIANT). Master keys must NOT be passed here — delete those via
        delete_master_picture."""
        import shutil
        matching = list(keys)
        protected = 0
        deleted = 0
        srcs = []
        days_to_persist: set[str] = set()
        for key in matching:
            if self.jpg_path(key):
                protected += 1
                continue
            upn_row = self.fpr.upn.UniquePictureName_dict.get(key, {})
            files = upn_row.get("Files", [])
            if not isinstance(files, list):
                files = []
            day = self._upn_day_map.get(key) or day_hint or "unknown"
            upn_dir = trash_root / day / key
            upn_dir.mkdir(parents=True, exist_ok=True)
            raw_src = None
            for fname in files:
                frow = self.fpr.fi.files_dict.get(fname)
                if not frow:
                    continue
                src = Path(frow.get("WindowsFileName", ""))
                if src.suffix.lower() in _RAW_EXTS:
                    raw_src = src
                if src.exists():
                    try:
                        shutil.move(str(src), str(upn_dir / src.name))
                        srcs.append(src)
                    except Exception as e:
                        logger.error(f"Trash move failed for {src.name}: {e}")
                self.fpr.fi.files_dict.pop(fname, None)
                # Also clean the per-day files dict (shared reference won't auto-reflect pops)
                if self._day_objects and day in self._day_objects:
                    self._day_objects[day][0].files_dict.pop(fname, None)
            sweep_dop(raw_src, upn_dir)
            self.fpr.upn.UniquePictureName_dict.pop(key, None)
            if self._day_objects and day in self._day_objects:
                self._day_objects[day][1].UniquePictureName_dict.pop(key, None)
            if self._day_objects and day in self._day_objects:
                days_to_persist.add(day)
            deleted += 1
        from utils.preview_cache import drop_for_files
        drop_for_files(srcs)     # the RAWs are gone; their cached previews are junk
        for day in days_to_persist:
            fi_day, upn_day = self._day_objects[day]
            try:
                upn_day.write_project_summary()
            except Exception as e:
                logger.error(f"delete_by_color: pictures CSV write failed for {day}: {e}")
            try:
                fi_day.write_project_files()
            except Exception as e:
                logger.error(f"delete_by_color: files CSV write failed for {day}: {e}")
        if not days_to_persist and deleted:
            # Day scope — fpr.fi / fpr.upn ARE the single day's objects
            try:
                self.fpr.upn.write_project_summary()
                self.fpr.fi.write_project_files()
            except Exception as e:
                logger.error(f"delete_by_color: CSV write failed (day scope): {e}")
        return len(matching), protected, deleted

    # ── master write-back (Phase 2) ───────────────────────────────────────────
    def _apply_master_rating(self, upn_key: str, *, new_stars: int | None = None,
                             new_color: str | None = None) -> int | None:
        """Re-rate a master picture. Writes XMP to the master image files (so the
        rating survives a future master EXIF re-read), then records the change
        durably via MasterDB.apply_rating (SQLite WAL + queued CSV rewrite).
        Master file paths are RELATIVE in the CSV → rebuilt from master_root."""
        upn_row = self._master_upn.get(upn_key)
        if not upn_row or self._master_db is None or self._master_root is None:
            return None
        rel_dir = (upn_row.get("TargetPath")
                   or upn_row.get("BestJPGPathName") or "").strip()
        files = upn_row.get("Files", [])
        if not isinstance(files, list):
            files = []

        file_updates: dict[str, dict] = {}
        raw_path = None
        for fname in files:
            frow = self._master_files.get(fname)
            if not frow:
                continue
            ext = frow.get("WindowsExtension", "").lower()
            if ext in _DXO_RAW_EXTS and raw_path is None and rel_dir:
                raw_path = str(self._master_root / rel_dir / fname)  # for the .dop write
            if ext not in ("xmp", "jpg", "jpeg"):
                continue
            if not rel_dir:
                continue
            fpath = self._master_root / rel_dir / fname
            if not fpath.exists():
                continue
            args = []
            if new_stars is not None:
                args.append(f"-XMP:Rating={new_stars}")
            if new_color is not None:
                args.append(f"-XMP:Label={new_color}")
            if not args:
                continue
            # Colour/rating in the .xmp can be suppressed (Sidecar Behaviour) since
            # PhotoLab mistreats it; the CSV/DB update below still records the value.
            suppress = ext == "xmp" and not color_rating_to_xmp_enabled()
            if not suppress:
                try:
                    exiftool_write(fpath, args)
                except Exception as e:
                    logger.error(f"master exiftool write failed for {fpath.name}: {e}")
                    continue
            upd: dict = {}
            if new_stars is not None:
                frow["XMP:Rating"] = str(new_stars)
                upd["XMP:Rating"] = str(new_stars)
            if new_color is not None:
                frow["XMP:Label"] = new_color
                upd["XMP:Label"] = new_color
            file_updates[fname] = upd
        # Colour/rating into the DxO .dop (PhotoLab reads it on 'import sidecars').
        if raw_path and color_rating_to_dop_enabled():
            push_metadata_to_dop(raw_path, color=new_color, rating=new_stars)

        if new_stars is not None:
            upn_row["Rating"] = str(new_stars)
        if new_color is not None:
            upn_row["Label"] = new_color
        new_rate = _compute_rate(upn_row, self._master_files)
        upn_row["Rate"] = str(new_rate)

        try:
            self._master_db.apply_rating(
                upn_key,
                rating=new_stars, label=new_color, rate=new_rate,
                file_updates=file_updates)
        except Exception as e:
            logger.error(f"master DB write failed for {upn_key}: {e}", exc_info=True)
        return new_rate
