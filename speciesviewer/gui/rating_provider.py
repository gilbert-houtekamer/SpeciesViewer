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

    def delete_master_file(self, key: str, fname: str) -> dict:
        from db.master_mutate import delete_file
        return delete_file(self._master_db, self._master_root, key, fname)

    def add_master_files(self, key: str) -> dict:
        """Ingest any untracked files of this UPN sitting in its species folder
        (e.g. a freshly-rendered DxO JPG + its .dop)."""
        from db.master_mutate import add_upn_files
        return add_upn_files(self._master_db, self._master_root, key)

    def _upn_row(self, key: str) -> dict:
        if key in self._master_keys:
            return self._master_upn.get(key, {})
        return self.fpr.upn.UniquePictureName_dict.get(key, {})

    def _files_dict_for(self, key: str) -> dict:
        return self._master_files if key in self._master_keys else self.fpr.fi.files_dict

    # ── ordering / filtering ──────────────────────────────────────────────
    def rebuild(self, filter_name: str | None = None) -> None:
        # Resolve the scientific name of the selected species (from the trip
        # rows) so master pictures can be matched on WetSoortNaam — robust
        # across the NL/Eng/Sci display languages.
        sci = None
        if filter_name and self._include_master and self._master_db is not None:
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

        rows = []
        # In full-master scope the master set already contains the album's own
        # pictures, so don't also add the trip/album rows (no duplicates).
        if not self._master_full:
            for key, row in self.fpr.upn.UniquePictureName_dict.items():
                if filter_name and self.species_name(row) != filter_name:
                    continue
                if self._jpg_only and self.jpg_path(key) is None:
                    continue
                rows.append((_safe_int(row.get("Rate", 0) or 0), key))
        for key in list(self._master_keys):
            if self._jpg_only and self.jpg_path(key) is None:
                self._master_keys.discard(key)   # not shown → not a master key
                continue
            rows.append((_safe_int(self._master_upn[key].get("Rate", 0) or 0), key))

        rows.sort(key=lambda x: x[0], reverse=True)
        self._order = [k for _, k in rows]

    def set_jpg_only(self, value: bool) -> None:
        self._jpg_only = value

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
