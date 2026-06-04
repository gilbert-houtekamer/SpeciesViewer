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
import subprocess
from pathlib import Path

from PyQt6.QtGui import QImage

from gui.picture_provider import (
    PictureItem, PictureCollection, ExifSummary, PixmapCallback, exif_from_rows)
from gui.image_loader import AsyncImageLoader
from gui.rating_editor import _compute_rate, _safe_int
from engine.exif_writer import exiftool_write

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = {".nef", ".orf", ".cr2", ".arw", ".rw2"}


class RatingEditorItem(PictureItem):
    """Thin view over one UPN row; writes delegate to the collection.

    A trip item is rating/colour-editable. A master item (pulled from the
    master DB as a reference for judging website-worthiness) is read-only — its
    capability flags are False so the card greys the controls."""

    # description/headline/species stay read-only on this engine for now
    can_edit_description = False
    can_edit_headline = False
    can_edit_species = False

    def __init__(self, collection: "RatingEditorCollection", upn_key: str):
        self._c = collection
        self._key = upn_key
        self._master = collection.is_master(upn_key)
        # Trip pictures are always editable. Master reference pictures are
        # editable only when the master is not in read-only mode (Phase 2).
        editable = (not self._master) or (not collection.master_read_only)
        self.can_edit_rating = editable
        self.can_edit_color = editable

    @property
    def from_master(self) -> bool:
        return self._master

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

    # write ops
    def set_rating(self, value: int) -> None:
        self._c.apply(self._key, new_stars=value)

    def set_color(self, value: str) -> None:
        self._c.apply(self._key, new_color=value)

    # async image
    def request_thumbnail(self, target_w: int, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, target_w, on_ready)

    def request_full_pixmap(self, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, None, on_ready)


class RatingEditorCollection(PictureCollection):
    def __init__(self, fpr, day_objects, upn_day_map, *,
                 jpg_only: bool = True, species_lang: str = "NL",
                 loader: AsyncImageLoader | None = None):
        self.fpr = fpr
        self._day_objects = day_objects      # dict day->(Files,Pictures), or None in Day scope
        self._upn_day_map = upn_day_map      # upn_key -> day
        self._jpg_only = jpg_only
        self._species_lang = species_lang
        self._loader = loader or AsyncImageLoader()
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
        self._master_keys: set[str] = set()  # keys currently shown from master
        # Lazy: don't scan all trip pictures (each jpg_path() does several
        # Path.exists() calls — thousands of filesystem stats over D:/E: and
        # Dropbox) until a species is actually selected. The dialog shows
        # "select a species" first; _rebuild_cards calls rebuild(filter) then.
        self._order: list[str] = []

    # ── master reference wiring ───────────────────────────────────────────
    def set_master(self, master_db, master_root, *, include: bool = True,
                   read_only: bool = True) -> None:
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
        self._master_upn = {}
        self._master_files = {}
        self._master_keys = set()

    @property
    def master_read_only(self) -> bool:
        return getattr(self, "_master_read_only", True)

    def is_master(self, key: str) -> bool:
        return key in self._master_keys

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
            trip_keys = self.fpr.upn.UniquePictureName_dict
            pics = [p for p in pics if p["UniquePictureName"] not in trip_keys]
            self._master_upn = {p["UniquePictureName"]: p for p in pics}
            if self._master_upn:
                self._master_files = self._master_db.files_for_upns(
                    self._master_upn.keys())
            self._master_keys = set(self._master_upn.keys())

        rows = []
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
            cache_dir = raw_path.parent / ".preview_cache"
            cache_dir.mkdir(exist_ok=True)
            cache_path = cache_dir / (raw_path.stem + ".jpg")
            if cache_path.exists():
                return cache_path
            for tag in ["-PreviewImage", "-ThumbnailImage"]:
                try:
                    result = subprocess.run(
                        ["exiftool", "-b", tag, str(raw_path)],
                        capture_output=True, timeout=15,
                    )
                    if result.returncode == 0 and result.stdout:
                        cache_path.write_bytes(result.stdout)
                        return cache_path
                except Exception as e:
                    logger.warning(f"RAW thumb extraction failed for {raw_path.name}: {e}")
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
        for fname in files:
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() not in ("xmp", "jpg", "jpeg"):
                continue
            fpath = Path(frow.get("WindowsFileName", ""))
            if not fpath.exists():
                continue
            args = []
            if new_stars is not None:
                args.append(f"-XMP:Rating={new_stars}")
            if new_color is not None:
                args.append(f"-XMP:Label={new_color}")
            if args:
                try:
                    exiftool_write(fpath, args)
                    wrote_file = True
                except Exception as e:
                    logger.error(f"exiftool write failed for {fpath.name}: {e}")
            if new_stars is not None:
                frow["XMP:Rating"] = str(new_stars)
            if new_color is not None:
                frow["XMP:Label"] = new_color

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
        for fname in files:
            frow = self._master_files.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() not in ("xmp", "jpg", "jpeg"):
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
