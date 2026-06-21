"""Species Image Preview implementation of the picture-provider contract.

Per-UPN view over the preview's FilePicture (day-folder mode: fpr loaded via
readMaster + readTripDay). Converges the preview onto "pictures" (UPN) instead
of one card per file, so it shares the field set + UI with the Rating Editor.

Editability: colour + description are writable (FilePicture.updateLabel /
updateDescription handle XMP + JPG siblings + CSV). Rating is read-only (this
engine has no rating writer); species editing stays in SpeciesInfoDialog for
now, so set_species is not offered. The preview also exposes has_gps and
error_msg for its missing-GPS / warnings filters.

Foundation only — not yet wired into ImagePreviewDialog (the grid rewire is the
next step). The virtual-trip file_list mode (no fpr) is not covered here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtGui import QImage

from gui.picture_provider import (
    PictureItem, PictureCollection, ExifSummary, PixmapCallback, exif_from_rows)
from gui.image_loader import AsyncImageLoader
from gui.rating_editor import _compute_rate
from engine.exif_writer import exiftool_write

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = {".nef", ".orf", ".cr2", ".arw", ".rw2"}


def preview_cache_dir_for(src_folder: Path) -> Path:
    """AppData-based preview cache directory for src_folder.

    Uses a short SHA-1 of the normalised folder path as a subdirectory so files
    with identical stems from different trip days never collide.
    """
    import hashlib
    import os
    from utils.cache_paths import cache_root
    folder_hash = hashlib.sha1(
        os.path.normcase(str(src_folder)).encode()
    ).hexdigest()[:16]
    return cache_root("preview") / folder_hash


def _safe_int(s, default=None):
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


def cached_jpg_for(src: Path | None, cache_dir: Path) -> Path | None:
    """Return a synchronously-loadable JPG for `src`: the file itself if it is
    already a JPG, else a cached preview (extracting from RAW / decoding HEIC on
    first use). Shared by both preview collections so the day-folder and
    virtual-trip modes decode identically. Returns None if nothing loadable."""
    if src is None or not src.exists():
        return None
    if src.suffix.lower() in (".jpg", ".jpeg"):
        return src
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / (src.stem + ".jpg")
    if cache.exists():
        return cache
    if src.suffix.lower() == ".heic":
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
            from PIL import Image
            im = Image.open(str(src))
            im.thumbnail((1600, 1600))
            im.save(str(cache), "JPEG")
            return cache
        except Exception as e:
            logger.warning(f"HEIC decode failed for {src.name}: {e}")
            return None
    # One shared, serialised exiftool — never a per-RAW process storm (which
    # intermittently yielded an empty preview → blank cards).
    from gui.preview_extract import extract_preview_to
    if extract_preview_to(src, cache):
        return cache
    return None


class PreviewItem(PictureItem):
    can_edit_description = True
    can_edit_headline = False
    can_edit_species = False      # species edits go through SpeciesInfoDialog

    def __init__(self, collection: "PreviewCollection", upn_key: str):
        self._c = collection
        self._key = upn_key

    # Rating + colour are only editable when the picture has a file we can write
    # the value into (an XMP sidecar or a JPG). HEIC-only / PNG-only pictures
    # have neither — a star/colour set on them would live only in the CSV and be
    # lost on the next Read Directory — so the card greys those controls. (The
    # durable-HEIC-capture follow-up is tracked in project memory.)
    @property
    def can_edit_rating(self) -> bool:
        return self._c.has_writable_sidecar(self._key)

    @property
    def can_edit_color(self) -> bool:
        return self._c.has_writable_sidecar(self._key)

    @property
    def _row(self) -> dict:
        return self._c.fpr.upn.UniquePictureName_dict.get(self._key, {})

    @property
    def _rep(self) -> str:
        """A representative filename for fpr.* calls (they map filename→UPN)."""
        return self._c.rep_filename(self._key)

    # identity
    @property
    def key(self) -> str:
        return self._key

    @property
    def display_name(self) -> str:
        return self._c.fpr.getSpeciesName(self._rep) or self._key

    # read fields
    @property
    def species(self) -> str:
        return self._c.fpr.getSpeciesName(self._rep)

    @property
    def headline(self) -> str:
        return self._row.get("Headline", "")

    @property
    def description(self) -> str:
        return self._c.fpr.getDescription(self._rep)

    @property
    def rating(self) -> int:
        return _safe_int(self._row.get("Rating", "0")) or 0

    @property
    def color(self) -> str:
        return (self._c.fpr.getColor(self._rep) or "").capitalize()

    @property
    def rate(self):
        r = self._row.get("Rate")
        return _safe_int(r) if r not in (None, "") else None

    @property
    def date(self) -> str:
        return self._row.get("DateTimeOriginal", "")

    @property
    def error_msg(self) -> str:
        return self._c.fpr.getErrorMsg(self._rep)

    @property
    def has_jpg(self) -> bool:
        return self._c.jpg_path(self._key) is not None

    @property
    def has_gps(self) -> bool:
        return self._c.has_gps(self._key)

    @property
    def file_exts(self) -> list[str]:
        files = self._row.get("Files", [])
        if not isinstance(files, list):
            return []
        return sorted({Path(f).suffix.lstrip(".").upper() for f in files if f})

    @property
    def exif(self) -> ExifSummary:
        return exif_from_rows(self._row, self._c.rep_file_row(self._key))

    # write ops
    def set_rating(self, value: int) -> None:
        self._c.apply(self._key, new_stars=value)

    def set_color(self, value: str) -> None:
        # Routed through apply (not fpr.updateLabel) so Rate recomputes too.
        self._c.apply(self._key, new_color=value)

    def set_description(self, value: str) -> None:
        self._c.fpr.updateDescription(self._rep, value)

    # async image
    def request_thumbnail(self, target_w: int, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, target_w, on_ready)

    def request_full_pixmap(self, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, None, on_ready)


class PreviewCollection(PictureCollection):
    def __init__(self, fpr, *, loader: AsyncImageLoader | None = None):
        self.fpr = fpr
        self._loader = loader or AsyncImageLoader()
        self._order: list[str] = []
        self.rebuild()

    # ── ordering ──────────────────────────────────────────────────────────
    def rebuild(self, keys: list[str] | None = None) -> None:
        """Order the given UPN keys (or all) by capture time, so iPhone HEIC and
        Olympus ORF interleave in shooting order — same intent as the preview's
        per-file sort. `keys` lets the host pass a filtered subset."""
        self._present_cache = None
        if keys is None:
            keys = list(self.fpr.upn.UniquePictureName_dict.keys())
        self._order = sorted(keys, key=self._capture_key)

    def _capture_key(self, key: str):
        dt = self.fpr.upn.UniquePictureName_dict.get(key, {}).get("DateTimeOriginal", "")
        return (0, dt, key) if dt else (1, "", key)

    # ── filter helpers (host wires these to the filter buttons) ───────────
    def all_keys(self) -> list[str]:
        return list(self.fpr.upn.UniquePictureName_dict.keys())

    def warning_keys(self) -> list[str]:
        return [k for k in self.all_keys() if self.fpr.getErrorMsg(self.rep_filename(k))]

    def no_gps_keys(self) -> list[str]:
        return [k for k in self.all_keys() if not self.has_gps(k)]

    def color_keys(self, colors: set[str]) -> list[str]:
        wanted = {c.lower() for c in colors}
        return [k for k in self.all_keys()
                if (self.fpr.getColor(self.rep_filename(k)) or "grey").lower() in wanted]

    def species_keys(self) -> list[str]:
        """UPNs that the user TRIED to identify: a resolved species OR a species
        warning (a name that didn't resolve — "may have a typo"). The latter
        carry no WetSoortNaam so they'd otherwise be invisible in this filter and
        never get committed; surfacing them (flagged with the ⚠ status icon) lets
        the user spot and fix the name. Plain untagged pictures stay excluded."""
        out = []
        for k in self.all_keys():
            rep = self.rep_filename(k)
            if (self.fpr.getSpeciesName(rep) or "").strip() or self.fpr.getErrorMsg(rep):
                out.append(k)
        return out

    def jpg_keys(self) -> list[str]:
        """UPNs that have at least one JPG file — the 'JPG only' (column 2) set."""
        return [k for k in self.all_keys() if self.jpg_path(k) is not None]

    def present_keys(self) -> list[str]:
        """UPNs that still have at least one file physically on disk. The rest are
        'ghost' pictures — admin records whose files were deleted/moved — so the
        host can show an honest count instead of trusting the admin total. Cached
        per rebuild (one existence pass)."""
        if self._present_cache is None:
            self._present_cache = [k for k in self.all_keys()
                                   if self.has_writable_sidecar(k)]
        return self._present_cache

    # ── PictureCollection ─────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._order)

    def item(self, index: int) -> PreviewItem:
        return PreviewItem(self, self._order[index])

    def index_of(self, key: str) -> int:
        return self._order.index(key)

    def cancel_pending_loads(self) -> None:
        self._loader.cancel_all()

    def species_editor_context(self, key: str):
        return self.fpr, [self.rep_full_path(key)]

    def file_directory(self, key: str) -> str:
        p = self.rep_full_path(key)
        return str(p.parent) if p and p.parent != p else ""

    def raw_path(self, key: str) -> Path | None:
        """Absolute path of the UPN's first RAW file (for opening in DxO)."""
        for fname in self._files(key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if ("." + frow.get("WindowsExtension", "").lower()) in _RAW_EXTS:
                p = Path(frow.get("WindowsFileName", ""))
                if p.exists():
                    return p
        return None

    def gps_source(self, upn_key: str):
        return self.rep_full_path(upn_key)

    def gps(self, upn_key: str):
        from gui.geolocate import gps_decimal
        p = self.gps_source(upn_key)
        return gps_decimal(p) if p else None

    def location_targets(self, upn_key: str):
        """The UPN's XMP/JPG files — where LR-style location text is written."""
        out = []
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() in ("xmp", "jpg", "jpeg"):
                wf = frow.get("WindowsFileName", "")
                if wf and Path(wf).exists():
                    out.append(wf)
        return out

    def read_location(self, upn_key: str) -> dict:
        from gui.geolocate import read_location_fields
        for f in (self.location_targets(upn_key) or [self.rep_full_path(upn_key)]):
            if f and Path(f).exists():
                d = read_location_fields(f)
                if any(d.values()):
                    return d
        return {}

    # ── helpers shared with items ─────────────────────────────────────────
    def _files(self, upn_key: str) -> list[str]:
        files = self.fpr.upn.UniquePictureName_dict.get(upn_key, {}).get("Files", [])
        return files if isinstance(files, list) else []

    def rep_filename(self, upn_key: str) -> str:
        """First filename of the UPN present in the file dict (any works for
        fpr.* calls, which map filename→UPN)."""
        for fname in self._files(upn_key):
            if fname in self.fpr.fi.files_dict:
                return fname
        return upn_key

    def rep_full_path(self, upn_key: str) -> Path:
        """Full on-disk path of a representative file for this UPN — what the
        SpeciesInfoDialog needs (its scope options walk the parent folder)."""
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and frow.get("WindowsFileName"):
                return Path(frow["WindowsFileName"])
        return Path(self.rep_filename(upn_key))

    def rep_file_row(self, upn_key: str) -> dict:
        """Representative non-XMP file row for EXIF."""
        fallback = {}
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() != "xmp":
                return frow
            fallback = frow
        return fallback

    def jpg_path(self, upn_key: str) -> Path | None:
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and frow.get("WindowsExtension", "").lower() in ("jpg", "jpeg"):
                p = Path(frow.get("WindowsFileName", ""))
                if p.exists():
                    return p
        return None

    def has_gps(self, upn_key: str) -> bool:
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and (frow.get("Composite:GPSPosition") or frow.get("EXIF:GPSLatitude")):
                return True
        return False

    def has_writable_sidecar(self, upn_key: str) -> bool:
        """True if rating/colour can be persisted for this picture — either an
        existing XMP/JPG to write, or a source image (HEIC/PNG/RAW) we can drop a
        standard <stem>.xmp sidecar beside (see apply / _create_xmp_sidecar).
        Only false for the degenerate case of a UPN with no on-disk image file
        (e.g. a stray .dop), so the card greys nothing in practice."""
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() == "dop":
                continue
            if Path(frow.get("WindowsFileName", "")).exists():
                return True
        return False

    def _source_file(self, upn_key: str) -> Path | None:
        """Best display source: JPG > RAW > HEIC."""
        jpg = raw = heic = None
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            ext = frow.get("WindowsExtension", "").lower()
            p = Path(frow.get("WindowsFileName", ""))
            if not p.exists():
                continue
            if ext in ("jpg", "jpeg") and jpg is None:
                jpg = p
            elif ("." + ext) in _RAW_EXTS and raw is None:
                raw = p
            elif ext == "heic" and heic is None:
                heic = p
        return jpg or raw or heic

    def display_path(self, upn_key: str) -> Path | None:
        """Synchronous loadable path (JPG direct, or a cached preview for
        RAW/HEIC, extracting/decoding if needed). Used by the large view."""
        src = self._source_file(upn_key)
        if src is None:
            return None
        return cached_jpg_for(src, preview_cache_dir_for(src.parent))

    def request_image(self, upn_key: str, target_w, on_ready: PixmapCallback) -> None:
        def produce():
            p = self.display_path(upn_key)  # extraction/decode off the GUI thread
            if p and p.exists():
                img = QImage(str(p))
                return img if not img.isNull() else None
            return None
        self._loader.request(produce, on_ready, target_w=target_w)

    def delete_by_color(self, color: str, trash_root: Path) -> tuple[int, int, int]:
        """Move all UPNs with the given color label to trash.

        Returns (total_matching, protected_count, deleted_count).
        UPNs that have a JPG are skipped (HARD INVARIANT: never trash a JPG-bearing picture).
        Caller supplies trash_root = <trip_root>/.trash/<day>; files land at trash_root/<key>/<file>.
        """
        import shutil
        wanted = color.lower()
        matching = [k for k in self.all_keys()
                    if (self.fpr.getColor(self.rep_filename(k)) or "").lower() == wanted]
        protected = 0
        deleted = 0
        for key in matching:
            if self.jpg_path(key):
                protected += 1
                continue
            upn_dir = trash_root / key
            upn_dir.mkdir(parents=True, exist_ok=True)
            for fname in self._files(key):
                frow = self.fpr.fi.files_dict.get(fname)
                if not frow:
                    continue
                src = Path(frow.get("WindowsFileName", ""))
                if src.exists():
                    try:
                        shutil.move(str(src), str(upn_dir / src.name))
                    except Exception as e:
                        logger.error(f"Trash move failed for {src.name}: {e}")
                self.fpr.fi.files_dict.pop(fname, None)
            self.fpr.upn.UniquePictureName_dict.pop(key, None)
            deleted += 1
        if deleted:
            try:
                self.fpr.upn.write_project_summary()
                self.fpr.fi.write_project_files()
            except Exception as e:
                logger.error(f"delete_by_color: CSV write failed: {e}")
        return len(matching), protected, deleted

    # ── write-back (rating / colour) with Rate recompute ──────────────────
    def apply(self, upn_key: str, *, new_stars: int | None = None,
              new_color: str | None = None) -> int | None:
        """Persist rating / colour for one picture, recompute Rate, and write
        both day CSVs. ORF·XMP / JPG pictures get XMP:Rating/Label via exiftool
        on their sidecar/JPG (RAW stays immutable); HEIC/PNG-only pictures get
        the value written INTO the HEIC/PNG itself (the single source LR + DxO +
        our Read Directory share). Day scope only."""
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key)
        if not upn_row:
            return None
        files = upn_row.get("Files", [])
        if not isinstance(files, list):
            files = []

        # Record the desired state on the UPN first — the single source of truth
        # for the HEIC write below and the immediate card/Rate refresh.
        if new_stars is not None:
            upn_row["Rating"] = str(new_stars)
        if new_color is not None:
            upn_row["Label"] = new_color

        wrote_file = False
        if self._heic_only_upn(upn_key):
            # HEIC/PNG-only — write the value into the HEIC/PNG itself.
            wrote_file = self._write_into_heic(
                upn_key, upn_row.get("Rating", "0") or "0", upn_row.get("Label", "") or "")
        else:
            # ORF·XMP / JPG — write XMP:Rating/Label into the existing files via
            # exiftool (those are LR-style and already read by DxO).
            for fname in files:
                frow = self.fpr.fi.files_dict.get(fname)
                if not frow:
                    continue
                if frow.get("WindowsExtension", "").lower() not in ("xmp", "jpg", "jpeg"):
                    continue
                fpath = Path(frow.get("WindowsFileName", ""))
                args = []
                if new_stars is not None:
                    args.append(f"-XMP:Rating={new_stars}")
                if new_color is not None:
                    args.append(f"-XMP:Label={new_color}")
                if args and fpath.exists():
                    try:
                        exiftool_write(fpath, args)
                        wrote_file = True
                    except Exception as e:
                        logger.error(f"exiftool write failed for {fpath.name}: {e}")
                if new_stars is not None:
                    frow["XMP:Rating"] = str(new_stars)
                if new_color is not None:
                    frow["XMP:Label"] = new_color

        new_rate = _compute_rate(upn_row, self.fpr.fi.files_dict)
        upn_row["Rate"] = str(new_rate)

        try:
            self.fpr.upn.write_project_summary()
            if wrote_file:
                self.fpr.fi.write_project_files()
        except Exception as e:
            logger.error(f"Preview apply: failed to persist CSV(s): {e}")
        return new_rate

    def _heic_only_upn(self, upn_key: str) -> bool:
        """True when the picture has no JPG and no RAW — i.e. HEIC-only /
        PNG-only. Those carry rating/colour in the HEIC/PNG itself; everything
        else (RAW owns a Lightroom XMP, or has a JPG) keeps the exiftool path."""
        raws = {"orf", "nef", "cr2", "cr3", "arw", "rw2", "raf", "dng"}
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            ext = frow.get("WindowsExtension", "").lower()
            if ext in ("jpg", "jpeg") or ext in raws:
                return False
        return True

    def _write_into_heic(self, upn_key: str, rating: str, label: str) -> bool:
        """Write XMP:Rating/Label (+ EXIF:Rating) INTO the HEIC (or PNG) itself —
        the single source of truth Lightroom, DxO PhotoLab, and our own Read
        Directory all read. Updates the cached file row so Rate recomputes now;
        the next Read Directory re-reads the embedded values. (Abandons the old
        'HEIC immutable' convention for metadata — pixels are untouched.)"""
        src = src_row = None
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() not in ("heic", "png"):
                continue
            p = Path(frow.get("WindowsFileName", ""))
            if p.exists():
                src, src_row = p, frow
                break
        if src is None:
            logger.warning(f"No HEIC/PNG to write rating/colour into for {upn_key}")
            return False

        rating = str(rating or "0")
        # EXIF:Rating too — Lightroom writes it, so keep it in sync to avoid a
        # stale EXIF star shadowing the XMP one in LR / Windows Explorer.
        args = [f"-XMP:Rating={rating}", f"-EXIF:Rating={rating}", f"-XMP:Label={label}"]
        try:
            exiftool_write(src, args)
        except Exception as e:
            logger.error(f"Failed to write metadata into {src.name}: {e}")
            return False
        src_row["XMP:Rating"] = rating
        src_row["XMP:Label"] = label
        logger.info(f"Wrote rating/colour into {src.name} for {upn_key} "
                    f"(Rating={rating}, Label={label or '-'}).")
        return True


# ── No-fpr (virtual-trip / metadata-less) preview ────────────────────────────
# The day-folder preview groups files into UPNs via fpr; the virtual-trip and
# metadata-less modes have no project CSVs, so there is no UPN to group on. Here
# each file is its own "picture": a thumbnail with a filename, no editable
# fields and no species/GPS/warning state (the filter buttons are greyed by the
# host in this mode). Same async image path as PreviewCollection, so the grid
# renders identically.

class FilePreviewItem(PictureItem):
    can_edit_color = False
    can_edit_description = False
    can_edit_rating = False
    can_edit_species = False

    def __init__(self, collection: "FileListPreviewCollection", path_key: str):
        self._c = collection
        self._key = path_key

    @property
    def key(self) -> str:
        return self._key

    @property
    def display_name(self) -> str:
        return Path(self._key).name

    @property
    def species(self) -> str:
        return ""

    @property
    def headline(self) -> str:
        return ""

    @property
    def description(self) -> str:
        return ""

    @property
    def rating(self) -> int:
        return 0

    @property
    def color(self) -> str:
        return ""

    @property
    def file_exts(self) -> list[str]:
        return [Path(self._key).suffix.lstrip(".").upper()] if Path(self._key).suffix else []

    def request_thumbnail(self, target_w: int, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, target_w, on_ready)

    def request_full_pixmap(self, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, None, on_ready)


class FileListPreviewCollection(PictureCollection):
    """Per-file preview over a plain list of paths (no fpr). Backs the
    virtual-trip file_list mode and metadata-less day folders."""

    def __init__(self, paths, *, cache_root: Path | None = None,
                 loader: AsyncImageLoader | None = None):
        self._paths = [Path(p) for p in paths]
        self._cache_root = Path(cache_root) if cache_root else None
        self._loader = loader or AsyncImageLoader()
        self._order: list[str] = [str(p) for p in self._paths]

    # ── ordering / filter helpers (host wires these; no fpr → no real filters)
    def rebuild(self, keys: list[str] | None = None) -> None:
        self._order = list(keys) if keys is not None else [str(p) for p in self._paths]

    def all_keys(self) -> list[str]:
        return [str(p) for p in self._paths]

    def warning_keys(self) -> list[str]:
        return []

    def no_gps_keys(self) -> list[str]:
        return []

    def color_keys(self, colors: set[str]) -> list[str]:
        return self.all_keys()

    def jpg_keys(self) -> list[str]:
        return [str(p) for p in self._paths if p.suffix.lower() in (".jpg", ".jpeg")]

    # ── PictureCollection ─────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._order)

    def item(self, index: int) -> FilePreviewItem:
        return FilePreviewItem(self, self._order[index])

    def index_of(self, key: str) -> int:
        return self._order.index(key)

    def cancel_pending_loads(self) -> None:
        self._loader.cancel_all()

    # ── image helpers ─────────────────────────────────────────────────────
    def _cache_dir_for(self, src: Path) -> Path:
        return self._cache_root if self._cache_root else preview_cache_dir_for(src.parent)

    def display_path(self, key: str) -> Path | None:
        src = Path(key)
        return cached_jpg_for(src, self._cache_dir_for(src))

    def request_image(self, key: str, target_w, on_ready: PixmapCallback) -> None:
        def produce():
            p = self.display_path(key)
            if p and p.exists():
                img = QImage(str(p))
                return img if not img.isNull() else None
            return None
        self._loader.request(produce, on_ready, target_w=target_w)
