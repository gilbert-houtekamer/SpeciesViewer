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

from gui.picture_provider import (PictureItem, PictureCollection, ExifSummary,
                                  PixmapCallback, sweep_dop, apply_crop, crop_for,
                                  jpg_is_crop_by_size)
from gui.image_loader import AsyncImageLoader
from gui.master_ops import MasterCardOpsMixin
from gui.preview_provider import cached_jpg_for, preview_cache_dir_for
from engine.exif_writer import exiftool_write
from engine.sidecarlib import push_metadata_to_dop, read_dop_metadata
from utils.sidecar_policy import (
    color_rating_to_xmp_enabled, color_rating_to_dop_enabled,
)
from engine.handling import deriveUniquePictureName

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = {".cr3", ".cr2", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2"}
_JPG_EXTS = {".jpg", ".jpeg"}

# Render-suffix markers (lowercased) that deriveUniquePictureName strips. Used to
# rebuild the SOURCE stem so a render's sidecar — named after the source, not the
# render (…-ORF-DxO_DeepPRIME XD2.jpg vs …N3080498.xmp) — can still be found.
_RENDER_MARKERS = ("-small", "-nef", "-orf", "-dxo", "_dxo", "-jpg", " - copy")


def _source_xmp_for(jpg_path: Path):
    """The .xmp of a render JPG's SOURCE picture: strip the render suffix from the
    (case-preserving) stem and look for <source>.xmp beside it. None when the name
    carries no render suffix or the source .xmp isn't there. One stat, no listdir."""
    stem = jpg_path.stem
    low = stem.lower()
    cut = len(stem)
    for m in _RENDER_MARKERS:
        i = low.find(m)
        if i != -1:
            cut = min(cut, i)
    if cut == len(stem):
        return None
    cand = jpg_path.with_name(stem[:cut] + ".xmp")
    return cand if cand.exists() else None


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def _read_sidecar_rows(xmp_paths: list, *, quiet_stats: dict | None = None) -> tuple[dict, dict]:
    """Cache-first metadata load.

    Returns (rows, caches):
      rows   — {normalised xmp path: metadata dict}
      caches — {folder Path: SidecarCache}, held by SidecarCollection for
               write-back updates so the cache stays consistent with edits.

    Hit path: O(stat()) per file — no exiftool call.
    Miss path: exiftool via argfile (no command-line length limit) for
               stale/missing entries only; cache is updated and flushed.

    quiet_stats: pass a dict to accumulate {"cached", "stale"} into instead of
    logging this call on its own — for a caller looping over many folders
    (e.g. one call per trip day) that wants a single combined summary rather
    than one "Sidecar cache: ..." line per folder.
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
    if quiet_stats is not None:
        quiet_stats["cached"] = quiet_stats.get("cached", 0) + len(rows)
        quiet_stats["stale"] = quiet_stats.get("stale", 0) + stale_count
    elif stale_count:
        logger.info(f"Sidecar cache: {len(rows)} cached, {stale_count} stale/missing — reading via exiftool")

    for folder, stale in stale_by_folder.items():
        fresh = read_xmp_rows(stale)
        for row in fresh:
            src = row.get("SourceFile", "")
            if src:
                xmp = Path(src)
                caches[folder].put(xmp, row)
                rows[_norm(xmp)] = row

    # Flush EVERY cache, not only the ones that had stale rows. A cache adopted from
    # the old in-folder location is dirty with nothing stale in it, and would
    # otherwise never be written to its new home — it would be re-migrated from
    # Dropbox on every single open. save() is a no-op when nothing changed.
    for cache in caches.values():
        cache.save()

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
    show_location_line = True   # culling grid: place line under the species name

    def __init__(self, collection: "SidecarCollection", stem: str):
        self._c = collection
        self._stem = stem
        # Crop suggestion applies to RAW pictures only (a JPG-only group has no
        # RAW to re-develop, and its preview is already the crop). Allowed on an
        # otherwise read-only viewer when allow_crop is set — the same carve-out as
        # geotag: a suggested crop is written to the master RAW's .dop/.xmp, which
        # PhotoLab reads on its next open, so it is a real, useful write even though
        # the viewer changes nothing itself (the virtual-trip case).
        self.can_set_crop = ((not collection.readonly or collection.allow_crop)
                             and bool(collection._groups.get(stem, {}).get("raw")))
        if collection.readonly:
            # allow_edit is the rating/colour/description carve-out (see collection):
            # these write to the master's sidecars, which is exactly what an album
            # day viewer should be able to do. Species RE-DETERMINATION is NOT part
            # of it — a settled master name is not re-typed from an album (the sex/
            # age qualifier, which IS a property of the photo, is a separate editor).
            edit = getattr(collection, "allow_edit", False)
            self.can_edit_rating = edit
            self.can_edit_color = edit
            self.can_edit_description = edit
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
        # Prefer the species name in the host's language profile (from the day CSV);
        # this also names a picture whose sidecar subject is empty (a DxO render
        # whose .xmp didn't pair). Else fall back to the sidecar subject, then stem.
        hinted = self._c._name_for_stem(self._stem)
        if hinted:
            return hinted
        from gui.culling_species import split_species_qualifiers
        name, _ = split_species_qualifiers(self.species)
        return name or self._stem

    @property
    def qualifier_suffix(self) -> str:
        from gui.culling_species import qualifier_suffix_from_subject
        return qualifier_suffix_from_subject(self.species)

    @property
    def from_master(self) -> bool:
        # A read-only sidecar viewer is the album/virtual-trip DAY viewer, whose
        # pictures ARE the master's — so their sex/age/plumage form is editable
        # here the same way it is in the Master Species Viewer. (Normal off-card
        # culling is not read-only and its pictures are not yet in the master.)
        return self._c.readonly

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
        return self._stem in self._c._gps_by_stem

    @property
    def has_crop(self) -> bool:
        # Reuses the collection's per-stem crop cache (also used for the thumbnail
        # crop), so this is free after the first read. Covers our crops and PL/LR's.
        # Fallback: a baked DxO render (folded onto this RAW group) keeps no
        # readable rectangle, so a JPG much smaller than the RAW is taken as a crop.
        if self._c._crop_for(self._stem) is not None:
            return True
        return self._c._crop_by_size(self._stem)

    @property
    def place_fields(self) -> dict:
        r = self._row
        return {k: str(r.get(k, "") or "")
                for k in ("Location", "City", "Municipality", "State")}

    @property
    def gps_text(self) -> str:
        ll = self._c._gps_by_stem.get(self._stem)
        return f"{ll[0]:.5f}, {ll[1]:.5f}" if ll else ""

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

    def request_uncropped_pixmap(self, on_ready: PixmapCallback) -> None:
        # The crop tool draws on the full frame, so bypass the edit-crop that
        # request_full_pixmap applies for the normal cull view.
        self._c.request_image(self._stem, None, on_ready, crop=False)

    def set_crop(self, crop: dict):
        """Write the suggested crop to this picture's sidecars (see collection)."""
        return self._c.set_crop(self._stem, crop)

    # write ops → the sidecar
    def set_rating(self, value: int) -> None:
        self._c.write_tag(self._stem, "XMP:Rating", str(int(value)), "Rating")

    @property
    def rate(self):
        """The master's computed Rate score for this picture, shown on the card. Comes
        from the bound master rows (album day viewer, collection._master_upn); None → the
        card shows '?', which is right for the plain culling grid (no master DB, so no
        computed score). The star rating is separate and edited via the sidecar."""
        row = getattr(self._c, "_master_upn", {}).get(self._stem)
        if not row:
            return None
        try:
            r = row.get("Rate")
            return int(r) if r not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def set_color(self, value: str) -> None:
        self._c.write_tag(self._stem, "XMP:Label", value, "Label")

    def set_description(self, value: str) -> None:
        self._c.write_tag(self._stem, "XMP:Description", value, "Description")

    def set_species(self, value: str) -> None:
        # Assigning a list-type XMP tag replaces it, so the sidecar carries the
        # single chosen keyword; the pipeline resolves it to a taxon later.
        self._c.write_tag(self._stem, "XMP:Subject", value, "Subject")


class SidecarCollection(MasterCardOpsMixin, PictureCollection):
    """Pictures discovered from RAW(+JPG)+XMP sidecar groups in a folder or an
    explicit RAW list. Reads metadata from the sidecars; edits write back to them.

    In the album day viewer the pictures ARE the master's, so bind_master() attaches
    the master DB + this day's rows and the shared MasterCardOpsMixin lights up the
    full master card menu (pin / delete-JPG / move / add / delete). The sex/age/plumage
    qualifier methods below stay SIDECAR-based (they override the mixin's DB versions),
    so they work in the large viewer even without a DB binding — and both write the same
    XMP:Subject on the master's own file."""

    # The culling grid carries species/description/GPS per card, so it can show the
    # PictureCard status/📝/📍 badge row even without an fpr project (the no-fpr
    # virtual-trip preview can't, so it leaves this False).
    show_status_badges = True

    # Class-level defaults, mirroring PreviewCollection, so the flags can be read off
    # the class and never come back missing on an instance built by an older caller.
    read_only = False
    allow_geotag = False
    allow_crop = False
    allow_edit = False

    def __init__(self, paths, *, readonly=False, cache_root=None,
                 loader: AsyncImageLoader | None = None, allow_geotag=False,
                 allow_crop=False, allow_edit=False):
        # `read_only` is the name every viewer (grid, large view, map) asks for —
        # keep the older `readonly` in step for this class's own callers.
        self.readonly = readonly
        self.read_only = bool(readonly)
        # Same carve-out as PreviewCollection: GPS + place may still be written on
        # an otherwise read-only collection, so the virtual-trip DAY viewer can fix
        # the master's missing coordinates. Everything else stays refused.
        self.allow_geotag = bool(allow_geotag)
        # A second carve-out, same spirit: a suggested crop lands in the master RAW's
        # .dop/.xmp for PhotoLab to pick up, so it is allowed on a read-only viewer too.
        self.allow_crop = bool(allow_crop)
        # Third carve-out: rating / colour / website description are durable in the
        # sidecar and write straight to the master's .dop/.xmp (an album is a window
        # ON the master, not a copy), so they may be edited on the read-only day
        # viewer too. Species RE-DETERMINATION stays out — that belongs in the master.
        self.allow_edit = bool(allow_edit)
        # Master inventory binding (album day viewer only). Empty until bind_master();
        # the shared MasterCardOpsMixin reads these to gate + drive pin/move/delete/add
        # against the master DB. Keyed by UPN, exactly like _groups
        # (deriveUniquePictureName), so a card's key resolves straight through.
        self._master_db = None
        self._master_root = None
        self._master_keys: set = set()
        self._master_upn: dict = {}
        self._master_files: dict = {}
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
                elif ext in _JPG_EXTS:
                    # A DxO/render JPG's .xmp is named after the SOURCE, not the
                    # render — so its rating/colour/subject were coming up empty.
                    src = _source_xmp_for(p)
                    if src is not None:
                        grp["xmp"] = src
        # Drop groups that ended up with no image file (e.g. a stray .xmp passed in).
        self._groups = {s: g for s, g in self._groups.items() if g.get("raw") or g.get("jpg")}
        self._crop_cache: dict = {}      # stem -> normalised crop | None (cleared on refresh)
        self._jpg_dims_cache: dict = {}  # jpg path -> "WxH" (header read, for the crop size cue)
        # {file path -> {NLSoortNaam,...}} + profile, so the caption follows the
        # name-language profile from the day CSV — and shows a name even when the
        # sidecar subject is empty (a DxO render whose .xmp didn't pair by stem).
        self._name_hints: dict = {}
        self._name_profile: str = ""
        self._image_changed: set = set() # stems whose IMAGE changed at last refresh()
        self._attach_sibling_jpgs()
        # Baseline image signatures (source file + mtime + .dop mtime) so refresh()
        # can tell a re-crop / new render (re-decode needed) from a text-only edit.
        self._image_sigs: dict = {s: self._image_sig(s) for s in self._groups}
        self._rows, self._caches = _read_sidecar_rows(
            [g["xmp"] for g in self._groups.values() if g.get("xmp")])
        # Colour/rating are stored in the DxO .dop (the write channel; the .xmp is
        # off by default), so seed the display rows from there — else a colour set
        # last session would silently read back as the sidecar's stale value.
        self._dop_mtimes: dict = {}   # stem -> last-seen .dop mtime_ns (overlay gate)
        self._overlay_dop_color_rating()
        # Batch-read GPS from each picture's best source (XMP if present, else
        # RAW). At the culling stage GPS lives in the RAW's EXIF, not the XMP
        # sidecar, so the sidecar cache alone can't populate the GPS badge. One
        # exiftool call covers all files; we keep the coordinates (not just
        # presence) so the card's location line can show them with no extra I/O.
        self._gps_by_stem: dict[str, tuple] = self._build_gps_map()
        self.rebuild()

    def _attach_sibling_jpgs(self) -> set:
        """Fold any *.jpg/*.jpeg in the picture folders onto its UPN group — a DxO
        _DxO.jpg render, or a Lightroom / v5 export — so the card's file-type note
        shows JPG even when the render only appeared after the folder was first
        listed (e.g. PhotoLab rendered it during a side-by-side session). Attaches
        to EXISTING groups only (a stray JPG never becomes its own card here);
        returns the stems that gained a JPG."""
        folders = set()
        for g in self._groups.values():
            src = g.get("raw") or g.get("xmp") or g.get("jpg")
            if src:
                folders.add(Path(src).parent)
        gained = set()
        for folder in folders:
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for p in entries:
                if p.suffix.lower() not in _JPG_EXTS or p.name.lower().endswith("-small.jpg"):
                    continue
                g = self._groups.get(deriveUniquePictureName(p.name))
                if g is not None and "jpg" not in g:
                    g["jpg"] = p
                    gained.add(deriveUniquePictureName(p.name))
        return gained

    def _build_gps_map(self) -> dict[str, tuple]:
        """{stem: (lat, lon)} for every stem with GPS in any source file.
        Uses gps_source() (XMP preferred, else RAW) so the RAW's EXIF GPS is
        found even when the XMP sidecar has none (common before geocoding)."""
        from gui.geolocate import batch_gps
        src_by_stem = {
            s: (g.get("xmp") or g.get("jpg") or g.get("raw"))
            for s, g in self._groups.items()
        }
        src_by_stem = {s: p for s, p in src_by_stem.items() if p}
        if not src_by_stem:
            return {}
        gps_result = batch_gps([str(p) for p in src_by_stem.values()])
        out = {}
        for s, p in src_by_stem.items():
            ll = gps_result.get(os.path.normcase(os.path.normpath(str(p))))
            if ll:
                out[s] = ll
        return out

    # ── ordering ──────────────────────────────────────────────────────────
    def rebuild(self, keys: list[str] | None = None) -> None:
        stems = keys if keys is not None else list(self._groups.keys())
        self._order = sorted(stems, key=self._capture_key)

    def _capture_key(self, stem: str):
        row = self._rows.get(_norm(self._groups.get(stem, {}).get("xmp", "")), {})
        dt = row.get("DateTimeOriginal", "")
        return (0, dt, stem) if dt else (1, "", stem)

    # ── filter helpers (host wires these) ────────────────────────────────────
    def all_keys(self) -> list[str]:
        return list(self._groups.keys())

    def warning_keys(self) -> list[str]:
        return []

    def no_gps_keys(self) -> list[str]:
        return [s for s in self.all_keys() if s not in self._gps_by_stem]

    def has_gps(self, key: str) -> bool:
        """Collection-level GPS test, matching PreviewCollection's. `_gps_by_stem` is
        the same source no_gps_keys trusts, and note_gps_written keeps it current, so
        a picture just given a coordinate reads as having one immediately."""
        return key in self._gps_by_stem

    def color_keys(self, colors: set[str]) -> list[str]:
        wanted = {c.lower() for c in colors}
        return [s for s in self.all_keys()
                if (SidecarItem(self, s).color or "grey").lower() in wanted]

    def jpg_keys(self) -> list[str]:
        return [s for s, g in self._groups.items() if g.get("jpg")]

    def _species_of(self, stem: str) -> str:
        """The sidecar's Subject text for this stem (the card's species line), or ''
        when the group has no XMP / no tag."""
        xmp = self._groups.get(stem, {}).get("xmp")
        if not xmp:
            return ""
        return _subject_to_text(self._rows.get(_norm(xmp), {}).get("Subject"))

    def find_keys(self, query: str) -> list[str]:
        """UPNs whose species/subject text contains `query` (case-insensitive
        substring) — the 'find species' box. Mirrors PreviewCollection so the
        virtual-trip / culling viewer searches the same way the pipeline-backed one
        does; the species text comes from the sidecar we already read."""
        q = (query or "").strip().lower()
        if not q:
            return self.all_keys()
        return [s for s in self.all_keys() if q in self._species_of(s).lower()]

    def file_keys(self, query: str) -> list[str]:
        """UPNs whose name (the picture key, e.g. '2023 tucson-dspt6421') contains
        `query` — the 'find file' box. Works regardless of species tagging."""
        q = (query or "").strip().lower()
        if not q:
            return self.all_keys()
        return [s for s in self.all_keys() if q in s.lower()]

    def species_keys(self) -> list[str]:
        """UPNs that carry a species/subject tag — the 'Species' filter."""
        return [s for s in self.all_keys() if self._species_of(s).strip()]

    # ── PictureCollection ─────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._order)

    def item(self, index: int) -> SidecarItem:
        return SidecarItem(self, self._order[index])

    def item_for_key(self, key: str) -> SidecarItem:
        """An item for ANY stem — independent of the current filter/sort (`_order`
        may be a filtered subset; the map plots all keys and must read a GPS'd
        picture's fields even when the view is filtered to no-GPS)."""
        if key not in self._groups:
            raise KeyError(key)
        return SidecarItem(self, key)

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

    def gps_cached(self, key: str):
        """(lat, lon) straight from the pre-built in-memory GPS map (no file read).
        The key IS the stem here, and `_gps_by_stem` is populated once at load, so
        the map plots the whole day with zero exiftool spawns."""
        ll = self._gps_by_stem.get(key)
        return (ll[0], ll[1]) if ll else None

    def location_targets(self, key: str):
        # THE gate every GPS/place write passes through. Read-only means these are
        # the master's files — look, don't touch — UNLESS allow_geotag, the
        # deliberate exception for fixing the master's missing coordinates.
        if self.readonly and not self.allow_geotag:
            return []
        g = self._groups.get(key, {})
        return [g[k] for k in ("xmp", "jpg") if g.get(k)]

    def gps_is_derived(self, key: str) -> bool:
        """True when a HUMAN placed this coordinate (map drag / copy-paste-GPS /
        place-name lookup) rather than the camera recording it — the only kind we
        allow moving. Read from GPSProcessingMethod, never inferred from precision."""
        from gui.geolocate import GPS_MANUAL
        if key in getattr(self, "_pasted_gps", ()):
            return True
        g = self._groups.get(key, {})
        xmp = g.get("xmp")
        if xmp:
            row = self._rows.get(_norm(xmp), {})
            for k in ("GPSProcessingMethod", "EXIF:GPSProcessingMethod",
                      "XMP:GPSProcessingMethod"):
                if str(row.get(k, "") or "").strip().upper() == GPS_MANUAL:
                    return True
        return False

    _LOC_KEYS = ("Location", "City", "Municipality", "State", "Country", "CountryCode")

    def read_location(self, key: str) -> dict:
        """Location text from the in-memory (cache-backed) row — NO exiftool spawn
        per navigation step. The row carries every location tag from the one
        batched sidecar-cache read; writes keep it fresh via note_location_written.
        Falls back to a direct read only for a JPG-only group with no sidecar."""
        g = self._groups.get(key, {})
        xmp = g.get("xmp")
        if xmp:
            r = self._rows.get(_norm(xmp), {})
            return {k: str(r.get(k, "") or "") for k in self._LOC_KEYS}
        from gui.geolocate import read_location_fields
        src = self._source_file(key)
        return read_location_fields(src) if src else {}

    def note_gps_written(self, key: str, lat=None, lon=None) -> None:
        """We just wrote GPS into this key's XMP — record the coordinates so has_gps,
        the missing-GPS badge, the card's GPS line and the MAP all see them without a
        rescan.

        Pass (lat, lon) when the caller knows them: it saves an exiftool spawn PER
        PICTURE (placing 13 pictures used to cost 26 launches — one to write, one to
        read straight back what we had just written).

        The coordinate is ALWAYS overwritten, never only filled when absent. A MOVE
        changes the GPS of a picture that already had some, and the old
        fill-if-missing behaviour silently kept the pre-move value: the map you had
        open showed the new spot, but re-opening it rebuilt from this stale map and
        the picture jumped back to where it started. The file on disk was right the
        whole time — only this dict lied.

        Anything WE wrote is human-placed by definition, so it also becomes refinable
        at once, without waiting for a re-read to populate GPSProcessingMethod."""
        if not hasattr(self, "_pasted_gps"):
            self._pasted_gps = set()
        self._pasted_gps.add(key)

        # The write changed coordinates, GPSProcessingMethod and (usually) the place
        # text — more of the row than we can honestly restate here. Drop the cached
        # entry so the next open re-reads it. Without this, note_location_written's
        # put_field re-stamps the mtime for the place text alone, the row then claims
        # to be current, and everything that trusts the cache keeps reporting the
        # picture as having no GPS long after it does.
        g = self._groups.get(key, {})
        xmp = g.get("xmp")
        if xmp:
            xmp = Path(xmp)
            cache = self._caches.get(xmp.parent)
            if cache is None:
                from engine.sidecar_cache import SidecarCache
                cache = self._caches[xmp.parent] = SidecarCache(xmp.parent)
            cache.invalidate(xmp)
            cache.save()

        if key in self._groups and lat is not None and lon is not None:
            self._gps_by_stem[key] = (float(lat), float(lon))
            return
        if key in self._groups:
            from gui.geolocate import gps_decimal
            ll = gps_decimal(self.gps_source(key))
            if ll:
                self._gps_by_stem[key] = ll

    supports_external_refresh = True

    # Fields whose change should re-paint a card (what another tool may edit).
    _REFRESH_FIELDS = ("Subject", "Rating", "Label", "Description", "Headline",
                       "Location", "City", "Municipality", "State", "Country")

    def _row_snapshot(self) -> dict:
        snap = {}
        for stem, g in self._groups.items():
            xmp = g.get("xmp")
            if xmp:
                r = self._rows.get(_norm(xmp), {})
                snap[stem] = tuple(str(r.get(k, "") or "") for k in self._REFRESH_FIELDS)
        return snap

    def _image_sig(self, stem: str):
        """A signature of what the displayed image depends on: the source file
        (RAW vs a render JPG) + its mtime + the .dop mtime (the crop lives there).
        A change here means the picture must be RE-DECODED — a new/re-rendered
        _DxO.jpg, or a re-crop — as opposed to a mere colour/rating text change."""
        g = self._groups.get(stem, {})
        src = g.get("jpg") or g.get("raw")
        raw = g.get("raw")
        dop = Path(str(raw) + ".dop") if raw else None

        def _mt(p):
            try:
                return Path(p).stat().st_mtime_ns
            except OSError:
                return None
        return (str(src) if src else None, _mt(src) if src else None,
                _mt(dop) if dop else None)

    def _overlay_dop_color_rating(self, only_changed: bool = False) -> set:
        """Fold each picture's colour/rating from its DxO .dop into the display
        rows. The .dop is the write channel (and PhotoLab's native store) when the
        .xmp channel is off, so it — not the sidecar — is the source of truth for
        Label/Rating there. No-op when the .xmp channel is on (the sidecar rows are
        already authoritative) or the .dop channel is off. With only_changed, only
        .dops whose mtime moved since last seen are re-read (near-free on refresh).
        Returns the stems whose colour/rating changed."""
        if color_rating_to_xmp_enabled() or not color_rating_to_dop_enabled():
            return set()
        changed = set()
        for stem, g in self._groups.items():
            raw = g.get("raw")
            xmp = g.get("xmp")
            if not raw or not xmp:
                continue                     # need a .dop source and a row to hold it
            dop = Path(str(raw) + ".dop")
            try:
                mt = dop.stat().st_mtime_ns
            except OSError:
                continue                     # no .dop yet → nothing to overlay
            if only_changed and self._dop_mtimes.get(stem) == mt:
                continue
            self._dop_mtimes[stem] = mt
            meta = read_dop_metadata(str(dop))
            row = self._rows.setdefault(_norm(xmp), {"SourceFile": str(xmp)})
            # Colour: DxO stores "None" for cleared, so absent/None is unambiguous —
            # the .dop always wins.
            if meta["color"] is not None and str(row.get("Label", "") or "") != meta["color"]:
                row["Label"] = meta["color"]
                changed.add(stem)
            # Rating: a .dop written for a colour-only edit keeps the skeleton
            # default Rating = 0, indistinguishable from an explicit 0 — so a .dop
            # 0 must NOT override a real rating that lives in the .xmp (ingest /
            # camera / Lightroom). A positive .dop rating is an explicit value and
            # wins; a 0 only applies when there's no rating to protect.
            if meta["rating"] is not None:
                cur = str(row.get("Rating", "") or "")
                dop_r = meta["rating"]
                if (dop_r != 0 or cur in ("", "0")) and cur != str(dop_r):
                    row["Rating"] = str(dop_r)
                    changed.add(stem)
        return changed

    def refresh(self) -> set:
        """Re-read the sidecars from disk to pick up external edits (PhotoLab /
        Lightroom colour, rating, crop, a new render — which bump the file mtime).
        Only files whose mtime changed are re-read via exiftool. Returns the stems
        whose DISPLAY changed; `self._image_changed` is the subset whose IMAGE
        changed (so the host re-decodes those, not every refreshed card)."""
        text_before = self._row_snapshot()
        self._rows, self._caches = _read_sidecar_rows(
            [g["xmp"] for g in self._groups.values() if g.get("xmp")])
        # Colour/rating live in the DxO .dop (PhotoLab's channel), NOT the .xmp,
        # unless the user opted into the .xmp channel — so the .xmp rows just read
        # carry a STALE Label/Rating. Overlay the .dop's values (our write channel
        # and PhotoLab's own store) so an edit here doesn't revert to the sidecar,
        # and an external PhotoLab colour/rating change refreshes the grid live.
        # Only .dops whose mtime moved are re-read, so this is near-free.
        self._overlay_dop_color_rating(only_changed=True)
        # A row-served read_location now reflects the new rows; drop the per-file
        # memo so the large viewer / copy-GPS re-read fresh too.
        from gui import geolocate
        geolocate._LOCATION_READ_CACHE.clear()
        self._crop_cache.clear()         # a re-crop in PhotoLab/LR must re-read
        # A render (PhotoLab _DxO.jpg, LR/v5 export) may have appeared — fold it in
        # (switches the display source RAW→JPG, caught by the image signature too).
        self._attach_sibling_jpgs()

        text_after = self._row_snapshot()
        text_changed = {s for s in text_after if text_after[s] != text_before.get(s)}
        # Image change is vs the STORED signature from the last refresh/build — an
        # external re-crop only touches the .dop (not self._rows), so a within-call
        # before/after would miss it (the rows re-read doesn't move image mtimes).
        new_sigs = {s: self._image_sig(s) for s in self._groups}
        self._image_changed = {s for s in new_sigs if new_sigs[s] != self._image_sigs.get(s)}
        self._image_sigs = new_sigs
        return text_changed | self._image_changed

    def sidecar_paths(self) -> list:
        """Files to watch for external edits: each .xmp (colour/rating/location)
        AND each .dop (the crop). Watching the .dop too catches an in-place re-crop
        that doesn't go through a temp+rename (which the folder watch would miss)."""
        out = []
        for g in self._groups.values():
            if g.get("xmp"):
                out.append(str(g["xmp"]))
            raw = g.get("raw")
            if raw:
                dop = Path(str(raw) + ".dop")
                if dop.exists():
                    out.append(str(dop))
        return out

    def note_location_written(self, key: str, fields: dict) -> None:
        """An Edit/Get-location write just put these place fields into the key's
        XMP — patch the in-memory row + the on-disk cache so the card's location
        line updates with no re-read (the write already changed the file)."""
        g = self._groups.get(key, {})
        xmp = g.get("xmp")
        if not xmp:
            return
        xmp = Path(xmp)
        row = self._rows.setdefault(_norm(xmp), {"SourceFile": str(xmp)})
        folder = xmp.parent
        if folder not in self._caches:
            from engine.sidecar_cache import SidecarCache
            self._caches[folder] = SidecarCache(folder)
        for k, v in fields.items():
            if v:
                row[k] = v
                self._caches[folder].put_field(xmp, k, v)
        self._caches[folder].save()

    # ── image + write helpers ───────────────────────────────────────────────
    def _source_file(self, stem: str):
        """Best display source: JPG (fast) if present, else the RAW (embedded preview)."""
        g = self._groups.get(stem, {})
        return g.get("jpg") or g.get("raw")

    def _cache_dir_for(self, src: Path) -> Path:
        return self._cache_root if self._cache_root else preview_cache_dir_for(src.parent)

    def _crop_for(self, stem: str):
        """Normalised crop {left,top,width,height} (0–1) from the PhotoLab .dop
        (preferred — it's the cropping tool) or the Lightroom .xmp, else None.
        Uses engine/sidecarlib (the same reader the DOP↔XMP transfer uses).
        Cached per stem; the cache is cleared on refresh()."""
        if stem in self._crop_cache:
            return self._crop_cache[stem]
        g = self._groups.get(stem, {})
        crop = crop_for(g.get("raw"), g.get("xmp"))
        self._crop_cache[stem] = crop
        return crop

    def set_name_hints(self, hints: dict, profile: str) -> None:
        self._name_hints = hints or {}
        self._name_profile = profile or ""

    def _name_for_stem(self, stem: str) -> str:
        """The species name for a group in the chosen language profile, from the
        day-CSV hints (keyed by any of the group's file paths). '' when unhinted."""
        if not self._name_hints:
            return ""
        g = self._groups.get(stem, {})
        from utils.name_profile import name_for
        for kind in ("jpg", "raw", "xmp"):
            p = g.get(kind)
            if p is not None:
                hint = self._name_hints.get(str(p))
                if hint:
                    return name_for(hint, self._name_profile)
        return ""

    def _jpg_dims(self, path) -> str:
        """"WxH" of a JPG, read from its header only (no full decode). Cached."""
        key = str(path)
        if key not in self._jpg_dims_cache:
            from PyQt6.QtGui import QImageReader
            sz = QImageReader(key).size()
            self._jpg_dims_cache[key] = (f"{sz.width()}x{sz.height()}"
                                         if sz.isValid() else "")
        return self._jpg_dims_cache[key]

    def _crop_by_size(self, stem: str) -> bool:
        """Size cue for the "#" marker when no crop rectangle is readable: the
        group's JPG (a baked DxO/LR render, folded onto its RAW) is much smaller
        than the RAW. RAW dims come from the sidecar row (ImageWidth/Height); the
        JPG dims are read from its header once. False without both a JPG and RAW."""
        g = self._groups.get(stem, {})
        jpg = g.get("jpg")
        if not jpg:
            return False
        xmp = g.get("xmp")
        row = self._rows.get(_norm(xmp), {}) if xmp else {}
        w, h = row.get("ImageWidth"), row.get("ImageHeight")
        raw_dims = f"{w}x{h}" if w and h else ""
        return jpg_is_crop_by_size(raw_dims, self._jpg_dims(jpg))

    def set_crop(self, stem: str, crop: dict):
        """Write a suggested crop to the picture's DxO .dop and Lightroom .xmp so
        the next edit in either app opens with it (creating a sidecar if missing).
        Invalidates the cached crop + image signature so the next decode shows the
        new crop and refresh() doesn't re-report our own write. Returns the
        sidecarlib result dict, or None if there's no RAW."""
        raw = self.raw_path(stem)
        if not raw:
            return None
        from engine.sidecarlib import suggest_crop
        res = suggest_crop(str(raw), crop)
        # A newly-created .xmp isn't in the group yet — attach it so later reads
        # (file_exts, location, rows on rescan) find it.
        g = self._groups.get(stem, {})
        if res and res.get("xmp_created") and "xmp" not in g:
            xmp = Path(raw).with_suffix(".xmp")
            if xmp.exists():
                g["xmp"] = xmp
        self._crop_cache.pop(stem, None)
        self._image_sigs[stem] = self._image_sig(stem)
        return res

    # ── master inventory binding (album day viewer) ───────────────────────────
    def bind_master(self, db, master_root, upn_rows, file_rows) -> None:
        """Attach the master DB + this day's picture/file rows so the shared master
        card menu can offer the inventory ops (pin / delete-JPG / move / add / delete)
        on these master pictures. Rows come from MasterDB.pictures_in_daterange +
        files_for_upns; keyed by UPN / File:FileName to match _groups and the
        MasterCardOpsMixin's reads."""
        self._master_db = db
        self._master_root = Path(master_root) if master_root else None
        self._master_upn = {r["UniquePictureName"]: r for r in upn_rows
                            if r.get("UniquePictureName")}
        self._master_files = {r["File:FileName"]: r for r in file_rows
                              if r.get("File:FileName")}
        self._master_keys = set(self._master_upn)

    # ── Sex / age / plumage qualifier (album day viewer) ──────────────────────
    # The album's pictures ARE the master's, so their FORM (a property of the
    # photograph, not of the determination) is editable here. These three methods
    # give large_image_view the same interface the master-DB collection exposes,
    # so the shared culling_species.edit_master_qualifiers flow works unchanged.
    # The write lands in the RAW's own XMP:Subject — the master's file — so a
    # master re-read produces exactly what we stored. The species NAME is never
    # touched (only the qualifier tokens are replaced around it).
    @property
    def master_read_only(self) -> bool:
        return not self.allow_edit

    def _qualifier_sc(self):
        if not hasattr(self, "_qual_sc"):
            from gui.culling_species import load_species_classification
            self._qual_sc = load_species_classification()
        return self._qual_sc

    def master_taxon_lineage(self, key: str) -> dict:
        from gui.culling_species import taxon_lineage, split_species_qualifiers
        item = self.item_for_key(key)
        name, _ = split_species_qualifiers(item.species)
        return taxon_lineage(self._qualifier_sc(), name) if name else {}

    def master_qualifiers(self, key: str) -> dict:
        """{group index: canonical token} currently on this picture's subject."""
        from gui.culling_species import split_species_qualifiers
        _name, selected = split_species_qualifiers(self.item_for_key(key).species)
        return selected

    def apply_master_qualifiers(self, key: str, selected: dict) -> bool:
        from gui.culling_species import (split_species_qualifiers,
                                         build_species_subject, qualifier_fields)
        item = self.item_for_key(key)
        name, _old = split_species_qualifiers(item.species)
        if not name:
            logger.warning(f"qualifiers: {key} has no species to attach them to.")
            return False
        subject = build_species_subject(name, selected)
        item.set_species(subject)               # writes XMP:Subject to the master's .xmp
        mf, age, sw = qualifier_fields(subject)
        # Keep the bound in-memory master row in step, so the caller's status text and any
        # later gating see the new form without a re-read. Also carry the new keyword onto
        # this UPN's file rows: the publish pipeline RE-DERIVES the form from the first
        # file's XMP:Subject (projectlib.addSpeciesInformation → getBirdInfo), so updating
        # only the picture row is not enough — the blog would re-derive the base species.
        row = getattr(self, "_master_upn", {}).get(key)
        file_updates: dict = {}
        if row is not None:
            row["subject"] = subject
            row["malefemale"], row["ageintext"], row["summerwinter"] = mf, age, sw
            # Replace the keyword on EVERY file that already carries one (the pipeline
            # uses the first file with a non-empty XMP:Subject, which can be the RAW), so
            # the re-derivation can't fall back to a sibling still holding the base name.
            for fname in (row.get("Files") or []):
                frow = getattr(self, "_master_files", {}).get(fname)
                if frow and (frow.get("XMP:Subject") or "").strip():
                    frow["XMP:Subject"] = subject
                    file_updates[fname] = {"XMP:Subject": subject}
        # Mirror the form into the master DB — like the GPS mirror. The blog and website
        # read the master, NOT the sidecar, so without this the published page shows the
        # base species with no 'imm.' / '♀' suffix. Only when bound to a master (the album
        # day viewer); the plain culling grid has no DB and just writes the sidecar.
        db = getattr(self, "_master_db", None)
        if db is not None:
            try:
                db.apply_qualifiers(key, subject=subject, malefemale=mf, ageintext=age,
                                    summerwinter=sw, file_updates=file_updates)
            except Exception as e:
                logger.warning(f"qualifier master mirror failed for {key}: {e}")
        return True

    def request_image(self, stem: str, target_w, on_ready: PixmapCallback,
                      crop: bool = True) -> None:
        src = self._source_file(stem)
        # Crop the RAW embedded preview to the PhotoLab/Lightroom crop so the cull
        # composition matches the edit. A JPG source (a DxO/LR render) is already
        # cropped, so it's shown as-is. crop=False (the crop tool) shows the full
        # uncropped frame so the user can draw a fresh rectangle.
        crop = (self._crop_for(stem)
                if crop and src and Path(src).suffix.lower() in _RAW_EXTS else None)

        def produce():
            if not src:
                return None
            p = cached_jpg_for(Path(src), self._cache_dir_for(Path(src)))
            if p and p.exists():
                img = QImage(str(p))
                if img.isNull():
                    return None
                return apply_crop(img, crop) if crop else img
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
        srcs = []
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
                        srcs.append(Path(src))
                    except Exception as e:
                        logger.error(f"Trash move failed for {Path(src).name}: {e}")
            # Sweep the DxO recipe too (else a lone .dop becomes a phantom picture).
            # A _DxO.jpg can't be here: a JPG protects the picture from deletion.
            sweep_dop(grp.get("raw"), upn_dir)
            self._groups.pop(stem, None)
            if grp.get("xmp"):
                self._rows.pop(_norm(grp["xmp"]), None)
            deleted += 1
        from utils.preview_cache import drop_for_files
        drop_for_files(srcs)     # the RAWs are gone; their cached previews are junk
        return len(matching), protected, deleted

    def _mirror_rating_to_master(self, upn: str, tag: str, value: str) -> None:
        """Mirror an album colour/rating edit into the master DB (pictures.Rating/Label,
        the files' XMP field) and recompute pictures.Rate, so the score reflects it at
        once — the same effect as editing in the Master Species Viewer. The derived Rate
        is recomputed from the picture's CURRENT files with the same formula the pipeline
        uses."""
        from gui.rating_editor import _compute_rate
        prow = self._master_upn.get(upn)
        if prow is None:
            return
        if tag == "XMP:Rating":
            prow["Rating"] = value
        else:
            prow["Label"] = value
        # Carry the value onto the file rows too — _compute_rate reads XMP:Rating from
        # the FILE rows, and the DB's file rows must match for a later re-read.
        file_updates: dict = {}
        for fname in (prow.get("Files") or []):
            frow = self._master_files.get(fname)
            if frow and (frow.get("WindowsExtension") or "").lower() in ("xmp", "jpg", "jpeg"):
                frow[tag] = value
                file_updates[fname] = {tag: value}
        new_rate = _compute_rate(prow, self._master_files)
        prow["Rate"] = str(new_rate)
        try:
            self._master_db.apply_rating(
                upn,
                rating=value if tag == "XMP:Rating" else None,
                label=value if tag == "XMP:Label" else None,
                rate=new_rate,
                file_updates=file_updates)
        except Exception as e:
            logger.warning(f"rating master mirror failed for {upn}: {e}")

    def write_tag(self, stem: str, tag: str, value: str, row_key: str) -> None:
        """Write one metadata tag for the stem, honouring Sidecar Behaviour.

        Colour/rating (XMP:Label / XMP:Rating) go to PhotoLab's .dop (its native
        store, read on 'import sidecars') and only optionally to the .xmp, since PL
        mistreats them there. Keywords/description always go to the .xmp. Updates
        the in-memory row and patches the on-disk cache so the next culling session
        opens without re-running exiftool."""
        is_colorrating = tag in ("XMP:Rating", "XMP:Label")

        # Album day viewer: the picture IS the master's, so mirror a colour/rating edit
        # into the master DB and recompute the derived Rate — the card's "Rate:" is read
        # from the master row, so without this it stays at the RAW-only value while the
        # stars move. Independent of which sidecar channel the value goes to below; a
        # no-op in plain culling (no master bound).
        if (is_colorrating and getattr(self, "_master_db", None) is not None
                and stem in self._master_upn):
            self._mirror_rating_to_master(stem, tag, value)

        # Colour/rating → the DxO .dop (preferred PhotoLab channel).
        if is_colorrating and color_rating_to_dop_enabled():
            raw = self.raw_path(stem)
            if raw:
                if tag == "XMP:Label":
                    push_metadata_to_dop(str(raw), color=value)
                else:  # XMP:Rating
                    try:
                        push_metadata_to_dop(str(raw), rating=int(value))
                    except ValueError:
                        pass
                # Our own .dop write bumps its mtime; re-baseline the image
                # signature so refresh() doesn't mistake it for an external
                # re-crop and needlessly re-decode this card's thumbnail.
                self._image_sigs[stem] = self._image_sig(stem)

        # .xmp sidecar: colour/rating is gated (default off); text always writes.
        suppress = is_colorrating and not color_rating_to_xmp_enabled()

        grp = self._groups.get(stem, {})
        xmp = grp.get("xmp")
        if xmp is None:
            # No sidecar yet (e.g. a JPG-only group) — drop one beside the source.
            src = self._source_file(stem)
            if not src:
                return
            xmp = Path(src).with_suffix(".xmp")
            if not suppress:
                grp["xmp"] = xmp
        if suppress:
            # Colour/rating in .xmp suppressed: keep the value in memory so the
            # current culling session still shows it (it's live in the .dop).
            self._rows.setdefault(_norm(xmp), {"SourceFile": str(xmp)})[row_key] = value
            return
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
