"""Sidecar-backed picture collection — the culling stage's data engine.

Unlike PreviewCollection (which needs the trip's per-day CSV metadata / fpr),
this reads everything from the XMP sidecars that ingest writes next to each RAW,
so the grid + large viewer work in full ISOLATION from the pipeline: no CSV, no
statemachine. The user culls here over many passes; Read & Summarize later
absorbs the same sidecars into the system.

Grouping is by file STEM (UPN-by-stem) — safe at this stage because there are no
_DxO renders or suffixed JPGs yet. This is deliberately kept separate from the
pipeline's UPN logic so the shortcut never leaks into it.

Editable: rating / colour / description write straight back to the .xmp via
exiftool (RAW stays untouched). Species editing is handled by the smarter culling
panel (separate step); here species is read-only display.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtGui import QImage

from gui.picture_provider import PictureItem, PictureCollection, ExifSummary, PixmapCallback
from gui.image_loader import AsyncImageLoader
from gui.preview_provider import cached_jpg_for, preview_cache_dir_for
from engine.exif_writer import exiftool_write
from engine.handling import deriveUniquePictureName

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = {".cr3", ".cr2", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2"}
_JPG_EXTS = {".jpg", ".jpeg"}


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _read_sidecar_rows(xmp_paths: list) -> tuple[dict, dict]:
    """Cache-first metadata load.

    Returns (rows, caches):
      rows   — {normalised xmp path: metadata dict}
      caches — {folder Path: SidecarCache}, held by SidecarCollection for
               write-back updates so the cache stays consistent with edits.

    Hit path: O(stat()) per file — no exiftool call.
    Miss path: exiftool via argfile (no command-line length limit) for
               stale/missing entries only; cache is updated and flushed.
    """
    from engine.sidecar_cache import SidecarCache, read_xmp_rows

    if not xmp_paths:
        return {}, {}

    paths = [Path(p) for p in xmp_paths]
    rows: dict[str, dict] = {}
    caches: dict[Path, SidecarCache] = {}
    stale_by_folder: dict[Path, list[Path]] = {}

    for p in paths:
        folder = p.parent
        if folder not in caches:
            caches[folder] = SidecarCache(folder)
        entry = caches[folder].get(p)
        if entry is not None:
            rows[_norm(p)] = entry
        else:
            stale_by_folder.setdefault(folder, []).append(p)

    stale_count = sum(len(v) for v in stale_by_folder.values())
    if stale_count:
        logger.info(f"Sidecar cache: {len(rows)} cached, {stale_count} stale/missing — reading via exiftool")

    for folder, stale in stale_by_folder.items():
        fresh = read_xmp_rows(stale)
        for row in fresh:
            src = row.get("SourceFile", "")
            if src:
                xmp = Path(src)
                caches[folder].put(xmp, row)
                rows[_norm(xmp)] = row
        caches[folder].save()

    return rows, caches


def _subject_to_text(subject) -> str:
    """dc:Subject can be a list (bag) or a string; render a display name."""
    if isinstance(subject, list):
        parts = [str(s) for s in subject if str(s).strip()]
        # The pipeline treats the longest keyword as the species name.
        return max(parts, key=len) if parts else ""
    return str(subject or "")


class SidecarItem(PictureItem):
    # Rating / colour / description / species are durable in the sidecar →
    # editable. Species editing is offered through the smart culling panel.
    can_edit_rating = True
    can_edit_color = True
    can_edit_description = True
    can_edit_headline = False
    can_edit_species = True

    def __init__(self, collection: "SidecarCollection", stem: str):
        self._c = collection
        self._stem = stem
        if collection.readonly:
            self.can_edit_rating = False
            self.can_edit_color = False
            self.can_edit_description = False
            self.can_edit_species = False

    @property
    def _grp(self) -> dict:
        return self._c._groups.get(self._stem, {})

    @property
    def _row(self) -> dict:
        return self._c._rows.get(_norm(self._grp.get("xmp", "")), {})

    # identity
    @property
    def key(self) -> str:
        return self._stem

    @property
    def display_name(self) -> str:
        return self.species or self._stem

    # read fields
    @property
    def species(self) -> str:
        return _subject_to_text(self._row.get("Subject"))

    @property
    def headline(self) -> str:
        return str(self._row.get("Headline", "") or "")

    @property
    def description(self) -> str:
        return str(self._row.get("Description", "") or "")

    @property
    def rating(self) -> int:
        try:
            return int(self._row.get("Rating", 0) or 0)
        except (ValueError, TypeError):
            return 0

    @property
    def color(self) -> str:
        return str(self._row.get("Label", "") or "").capitalize()

    @property
    def date(self) -> str:
        return str(self._row.get("DateTimeOriginal", "") or "")

    @property
    def has_jpg(self) -> bool:
        return self._grp.get("jpg") is not None

    @property
    def has_gps(self) -> bool:
        return self._stem in self._c._gps_stems

    @property
    def file_exts(self) -> list[str]:
        exts = set()
        for kind in ("raw", "jpg", "xmp"):
            p = self._grp.get(kind)
            if p:
                exts.add(Path(p).suffix.lstrip(".").upper())
        return sorted(exts)

    @property
    def exif(self) -> ExifSummary:
        r = self._row
        w, h = r.get("ImageWidth"), r.get("ImageHeight")
        dims = f"{w}x{h}" if w and h else ""
        return ExifSummary(
            iso=str(r.get("ISO", "") or ""),
            aperture=str(r.get("FNumber", "") or ""),
            shutter=str(r.get("ExposureTime", "") or ""),
            focal_length=str(r.get("FocalLength", "") or ""),
            model=str(r.get("Model", "") or ""),
            lens=str(r.get("LensModel", "") or ""),
            distance=str(r.get("SubjectDistance", "") or ""),
            dimensions=dims,
            gps=str(r.get("GPSPosition", "") or ""),
        )

    # async image — embedded preview from the best source (JPG if present, else RAW)
    def request_thumbnail(self, target_w: int, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._stem, target_w, on_ready)

    def request_full_pixmap(self, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._stem, None, on_ready)

    # write ops → the sidecar
    def set_rating(self, value: int) -> None:
        self._c.write_tag(self._stem, "XMP:Rating", str(int(value)), "Rating")

    def set_color(self, value: str) -> None:
        self._c.write_tag(self._stem, "XMP:Label", value, "Label")

    def set_description(self, value: str) -> None:
        self._c.write_tag(self._stem, "XMP:Description", value, "Description")

    def set_species(self, value: str) -> None:
        # Assigning a list-type XMP tag replaces it, so the sidecar carries the
        # single chosen keyword; the pipeline resolves it to a taxon later.
        self._c.write_tag(self._stem, "XMP:Subject", value, "Subject")


class SidecarCollection(PictureCollection):
    """Pictures discovered from RAW(+JPG)+XMP sidecar groups in a folder or an
    explicit RAW list. Reads metadata from the sidecars; edits write back to them."""

    def __init__(self, paths, *, readonly=False, cache_root=None, loader: AsyncImageLoader | None = None):
        self.readonly = readonly
        self._cache_root = Path(cache_root) if cache_root else None
        self._loader = loader or AsyncImageLoader()
        # Group input picture files by their unique-picture name. Using
        # deriveUniquePictureName (not the raw stem) folds DxO/PureRaw renders
        # and -small thumbnails onto their source RAW — otherwise e.g.
        # N2040705_DxO.jpg becomes its OWN picture, splitting one shot into two
        # and inflating the count. (At pure ingest there are no such suffixes,
        # so this is a no-op there.)
        self._groups: dict[str, dict] = {}
        for p in paths:
            p = Path(p)
            ext = p.suffix.lower()
            grp = self._groups.setdefault(deriveUniquePictureName(p.name), {})
            if ext in _RAW_EXTS:
                grp["raw"] = p
            elif ext in _JPG_EXTS:
                grp.setdefault("jpg", p)
            # Pair each group with its sidecar (stem.xmp beside the source).
            if "xmp" not in grp:
                xmp = p.with_suffix(".xmp")
                if xmp.exists():
                    grp["xmp"] = xmp
        # Drop groups that ended up with no image file (e.g. a stray .xmp passed in).
        self._groups = {s: g for s, g in self._groups.items() if g.get("raw") or g.get("jpg")}
        self._rows, self._caches = _read_sidecar_rows(
            [g["xmp"] for g in self._groups.values() if g.get("xmp")])
        # Batch-read GPS from each picture's best source (XMP if present, else
        # RAW). At the culling stage GPS lives in the RAW's EXIF, not the XMP
        # sidecar, so the sidecar cache alone can't populate the GPS badge.
        # One exiftool call covers all files.
        self._gps_stems: frozenset[str] = self._build_gps_stems()
        self.rebuild()

    def _build_gps_stems(self) -> frozenset[str]:
        """Return the set of stems that have GPS in any source file.
        Uses gps_source() (XMP preferred, else RAW) so the RAW's EXIF GPS is
        found even when the XMP sidecar has none (common before geocoding)."""
        from gui.geolocate import batch_gps
        src_by_stem = {
            s: (g.get("xmp") or g.get("jpg") or g.get("raw"))
            for s, g in self._groups.items()
        }
        src_by_stem = {s: p for s, p in src_by_stem.items() if p}
        if not src_by_stem:
            return frozenset()
        gps_result = batch_gps([str(p) for p in src_by_stem.values()])
        return frozenset(
            s for s, p in src_by_stem.items()
            if os.path.normcase(os.path.normpath(str(p))) in gps_result
        )

    # ── ordering ──────────────────────────────────────────────────────────
    def rebuild(self, keys: list[str] | None = None) -> None:
        stems = keys if keys is not None else list(self._groups.keys())
        self._order = sorted(stems, key=self._capture_key)

    def _capture_key(self, stem: str):
        row = self._rows.get(_norm(self._groups.get(stem, {}).get("xmp", "")), {})
        dt = row.get("DateTimeOriginal", "")
        return (0, dt, stem) if dt else (1, "", stem)

    # ── filter helpers (host wires these; only GPS/all are meaningful here) ──
    def all_keys(self) -> list[str]:
        return list(self._groups.keys())

    def warning_keys(self) -> list[str]:
        return []

    def no_gps_keys(self) -> list[str]:
        return [s for s in self.all_keys() if not SidecarItem(self, s).has_gps]

    def color_keys(self, colors: set[str]) -> list[str]:
        wanted = {c.lower() for c in colors}
        return [s for s in self.all_keys()
                if (SidecarItem(self, s).color or "grey").lower() in wanted]

    def jpg_keys(self) -> list[str]:
        return [s for s, g in self._groups.items() if g.get("jpg")]

    # ── PictureCollection ─────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._order)

    def item(self, index: int) -> SidecarItem:
        return SidecarItem(self, self._order[index])

    def index_of(self, key: str) -> int:
        return self._order.index(key)

    def cancel_pending_loads(self) -> None:
        self._loader.cancel_all()

    def file_directory(self, key: str) -> str:
        src = self._source_file(key)
        return str(src.parent) if src else ""

    def raw_path(self, key: str):
        raw = self._groups.get(key, {}).get("raw")
        return raw if raw and Path(raw).exists() else None

    def gps_source(self, key: str):
        return self._groups.get(key, {}).get("xmp") or self._source_file(key)

    def gps(self, key: str):
        from gui.geolocate import gps_decimal
        src = self.gps_source(key)
        return gps_decimal(src) if src else None

    def location_targets(self, key: str):
        g = self._groups.get(key, {})
        return [g[k] for k in ("xmp", "jpg") if g.get(k)]

    def read_location(self, key: str) -> dict:
        from gui.geolocate import read_location_fields
        src = self._groups.get(key, {}).get("xmp") or self._source_file(key)
        return read_location_fields(src) if src else {}

    def note_gps_written(self, key: str) -> None:
        """A paste-GPS just wrote GPS into this key's XMP — add it to the cached
        GPS set so has_gps / the missing-GPS badge clear without a rescan."""
        if key in self._groups and key not in self._gps_stems:
            self._gps_stems = self._gps_stems | {key}

    # ── image + write helpers ───────────────────────────────────────────────
    def _source_file(self, stem: str):
        """Best display source: JPG (fast) if present, else the RAW (embedded preview)."""
        g = self._groups.get(stem, {})
        return g.get("jpg") or g.get("raw")

    def _cache_dir_for(self, src: Path) -> Path:
        return self._cache_root if self._cache_root else preview_cache_dir_for(src.parent)

    def request_image(self, stem: str, target_w, on_ready: PixmapCallback) -> None:
        src = self._source_file(stem)

        def produce():
            if not src:
                return None
            p = cached_jpg_for(Path(src), self._cache_dir_for(Path(src)))
            if p and p.exists():
                img = QImage(str(p))
                return img if not img.isNull() else None
            return None
        self._loader.request(produce, on_ready, target_w=target_w)

    def jpg_path(self, key: str):
        g = self._groups.get(key, {})
        jpg = g.get("jpg")
        return Path(jpg) if jpg and Path(jpg).exists() else None

    def delete_by_color(self, color: str, trash_root: Path) -> tuple[int, int, int]:
        """Move all stems with the given color label to trash.

        Returns (total_matching, protected_count, deleted_count).
        Stems that have a JPG are skipped (HARD INVARIANT).
        Files go to trash_root/<stem>/<filename>.
        """
        import shutil
        wanted = color.lower()
        matching = [s for s in self.all_keys()
                    if SidecarItem(self, s).color.lower() == wanted]
        protected = 0
        deleted = 0
        for stem in matching:
            grp = self._groups.get(stem, {})
            if grp.get("jpg"):
                protected += 1
                continue
            upn_dir = trash_root / stem
            upn_dir.mkdir(parents=True, exist_ok=True)
            for kind in ("raw", "xmp"):
                src = grp.get(kind)
                if src and Path(src).exists():
                    try:
                        shutil.move(str(src), str(upn_dir / Path(src).name))
                    except Exception as e:
                        logger.error(f"Trash move failed for {Path(src).name}: {e}")
            self._groups.pop(stem, None)
            if grp.get("xmp"):
                self._rows.pop(_norm(grp["xmp"]), None)
            deleted += 1
        return len(matching), protected, deleted

    def write_tag(self, stem: str, tag: str, value: str, row_key: str) -> None:
        """Write one XMP tag into the stem's sidecar (creating it if needed),
        update the in-memory row, and patch the on-disk cache so the next
        culling session opens without re-running exiftool for this file."""
        grp = self._groups.get(stem, {})
        xmp = grp.get("xmp")
        if xmp is None:
            # No sidecar yet (e.g. a JPG-only group) — drop one beside the source.
            src = self._source_file(stem)
            if not src:
                return
            xmp = Path(src).with_suffix(".xmp")
            grp["xmp"] = xmp
        try:
            exiftool_write(Path(xmp), [f"-{tag}={value}"])
        except Exception as e:
            logger.error(f"Sidecar write failed for {Path(xmp).name}: {e}")
            return
        # Update in-memory row
        self._rows.setdefault(_norm(xmp), {"SourceFile": str(xmp)})[row_key] = value
        # Patch the on-disk cache entry (mtime_ns updated to the freshly written file)
        xmp_path = Path(xmp)
        folder = xmp_path.parent
        if folder not in self._caches:
            from engine.sidecar_cache import SidecarCache
            self._caches[folder] = SidecarCache(folder)
        self._caches[folder].put_field(xmp_path, row_key, value)
        self._caches[folder].save()
