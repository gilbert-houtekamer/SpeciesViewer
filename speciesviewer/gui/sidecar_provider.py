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

import json
import logging
import os
import subprocess
from pathlib import Path

from PyQt6.QtGui import QImage

from gui.picture_provider import PictureItem, PictureCollection, ExifSummary, PixmapCallback
from gui.image_loader import AsyncImageLoader
from gui.preview_provider import cached_jpg_for
from engine.exif_writer import exiftool_write

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = {".cr3", ".cr2", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2"}
_JPG_EXTS = {".jpg", ".jpeg"}

# Tags read from each sidecar in one batched exiftool pass (short names; exiftool
# returns them un-grouped). The EXIF snapshot was written into the XMP at ingest.
_READ_TAGS = [
    "-Subject", "-Rating", "-Label", "-Description", "-Headline",
    "-DateTimeOriginal", "-ISO", "-FNumber", "-ExposureTime", "-FocalLength",
    "-Make", "-Model", "-LensModel", "-SubjectDistance", "-ImageWidth", "-ImageHeight",
    "-GPSPosition", "-GPSLatitude", "-GPSLongitude",
]


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _read_sidecar_rows(xmp_paths):
    """One batched exiftool read of all sidecars → {normalised xmp path: row}."""
    if not xmp_paths:
        return {}
    cmd = (["exiftool", "-charset", "filename=utf8", "-charset", "utf8", "-j"]
           + _READ_TAGS + [str(p) for p in xmp_paths])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        rows = json.loads(r.stdout) if r.stdout.strip() else []
    except Exception as e:
        logger.warning(f"Could not read XMP sidecars: {e}")
        return {}
    return {_norm(row.get("SourceFile", "")): row for row in rows}


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
        r = self._row
        return bool(r.get("GPSPosition") or r.get("GPSLatitude"))

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

    def __init__(self, paths, *, cache_root=None, loader: AsyncImageLoader | None = None):
        self._cache_root = Path(cache_root) if cache_root else None
        self._loader = loader or AsyncImageLoader()
        # Group input picture files by stem.
        self._groups: dict[str, dict] = {}
        for p in paths:
            p = Path(p)
            ext = p.suffix.lower()
            grp = self._groups.setdefault(p.stem, {})
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
        self._rows = _read_sidecar_rows([g["xmp"] for g in self._groups.values() if g.get("xmp")])
        self.rebuild()

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

    # ── image + write helpers ───────────────────────────────────────────────
    def _source_file(self, stem: str):
        """Best display source: JPG (fast) if present, else the RAW (embedded preview)."""
        g = self._groups.get(stem, {})
        return g.get("jpg") or g.get("raw")

    def _cache_dir_for(self, src: Path) -> Path:
        return self._cache_root if self._cache_root else src.parent / ".preview_cache"

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

    def write_tag(self, stem: str, tag: str, value: str, row_key: str) -> None:
        """Write one XMP tag into the stem's sidecar (creating it if needed) and
        update the in-memory row so the card repaints with the new value."""
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
        self._rows.setdefault(_norm(xmp), {"SourceFile": str(xmp)})[row_key] = value
