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

import json
import logging
import re
import subprocess
from pathlib import Path

from PyQt6.QtGui import QImage

from gui.picture_provider import (
    PictureItem, PictureCollection, ExifSummary, PixmapCallback, exif_from_rows,
    sweep_dop, apply_crop, crop_for, exposure_for, jpg_is_crop_by_size)
from gui.image_loader import AsyncImageLoader
from gui.rating_editor import _compute_rate
from engine.exif_writer import exiftool_write
from engine.sidecarlib import push_metadata_to_dop
from utils.sidecar_policy import (
    color_rating_to_xmp_enabled, color_rating_to_dop_enabled,
)
from utils.raw_extensions import RAW, DXO_RAW, VIDEO, bare, HEIC, JPEG, OTHER_IMAGE

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = RAW  # canonical table; was missing .cr3/.dng/.raf — see utils/raw_extensions.py
_VIDEO_EXTS = VIDEO  # canonical table — see utils/raw_extensions.py


def preview_cache_dir_for(src_folder: Path) -> Path:
    """AppData-based preview cache directory for src_folder.

    Uses a short SHA-1 of the normalised folder path as a subdirectory so files
    with identical stems from different trip days never collide. The layout, the
    LRU stamp and the hygiene sweep all live in utils.preview_cache — this is the
    name the viewers already import.
    """
    from utils.preview_cache import cache_dir_for
    return cache_dir_for(src_folder)


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
    # Formats Qt decodes on its own are handed back as-is — every caller loads
    # the result with QImage(), which reads PNG and TIFF as happily as JPEG.
    # Sending a PNG down the RAW-extractor path instead (which is what happened
    # until the culling grid started accepting them) produces "Unsupported file
    # format or not RAW file" and a permanently grey card.
    if src.suffix.lower() in JPEG | OTHER_IMAGE:
        return src
    from utils import preview_cache
    # Records WHICH source folder this cache folder belongs to (the hash is
    # one-way), so the hygiene sweep can later tell a live entry from the leavings
    # of a deleted trip.
    preview_cache.ensure_dir(cache_dir, src.parent)
    cache = cache_dir / (src.stem + ".jpg")
    if cache.exists():
        preview_cache.touch(cache)   # LRU: a preview you still look at never ages out
        return cache
    # HEIC *and* HEIF — from the canonical table, because this branch used to
    # test ".heic" alone and a .heif was therefore handed to the RAW extractor,
    # which has no preview to find and returns None: a permanently grey card.
    if src.suffix.lower() in HEIC:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
            from PIL import Image
            from gui.preview_extract import preview_limits
            # Same size rule as a RAW's embedded preview. This used to be a
            # hard-coded 1600, which made an iPhone picture the one thing in the
            # cache that rendered softer than everything else for no stated reason.
            max_edge, quality = preview_limits()
            im = Image.open(str(src))
            if max_edge > 0:
                im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            im.convert("RGB").save(str(cache), "JPEG", quality=quality, optimize=True)
            return cache
        except Exception as e:
            logger.warning(f"HEIC decode failed for {src.name}: {e}")
            return None
    if src.suffix.lower() in _VIDEO_EXTS:
        from gui.preview_extract import extract_video_frame_bytes
        data = extract_video_frame_bytes(src)
        if data is None:
            return None
        try:
            cache.write_bytes(data)
            return cache
        except Exception as e:
            logger.warning(f"Could not write video-frame preview cache {cache.name}: {e}")
            return None
    # One shared, serialised exiftool — never a per-RAW process storm (which
    # intermittently yielded an empty preview → blank cards).
    from gui.preview_extract import extract_preview_to
    if extract_preview_to(src, cache):
        return cache
    return None


class PreviewItem(PictureItem):
    can_edit_species = False      # species edits go through SpeciesInfoDialog

    @property
    def can_edit_description(self) -> bool:
        return not self._c.read_only

    @property
    def can_edit_headline(self) -> bool:
        # The landscape / context-photo title; same write rule as the description.
        return not self._c.read_only

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

    def _profile_lines(self) -> list:
        """The row's NL/EN/Sci names as (text, is_sci) lines in the collection's
        display profile — deduped, fallback-filled. [] when the row has no names."""
        from utils.name_profile import name_lines, effective_profile
        prof = getattr(self._c, "_name_profile", "") or effective_profile()
        return name_lines(self._row, prof)

    @property
    def display_name(self) -> str:
        # A landscape / context "blog extra" is titled by its Headline (what the
        # website shows). A topic-filed landscape now carries a synthetic species
        # name (the year leaf, e.g. "2026"), so this must win BEFORE the species
        # name, not after.
        if self.is_blog_extra and self.headline:
            return self.headline
        lines = self._profile_lines()
        if lines:
            return lines[0][0]
        name = self._c.fpr.getSpeciesName(self._rep)
        return name or self._key

    @property
    def name_triple(self) -> dict:
        r = self._row
        return {"nl": r.get("NLSoortNaam", "") or "",
                "en": r.get("EngSoortNaam", "") or "",
                "sci": r.get("WetSoortNaam", "") or ""}

    @property
    def display_lines(self) -> list:
        # Follow the display profile: up to three deduped NL/EN/Sci lines, sex/age
        # suffix on the primary line. Blog extras keep their single Headline line.
        if self.is_blog_extra and self.headline:
            return [(self.headline, False)]
        lines = self._profile_lines()
        if not lines:
            name = self._c.fpr.getSpeciesName(self._rep) or self._key
            return [(name, False)]
        suffix = self.qualifier_suffix or ""
        if suffix:
            t, s = lines[0]
            lines = [(t + suffix, s)] + lines[1:]
        return lines

    @property
    def qualifier_suffix(self) -> str:
        from gui.culling_species import qualifier_suffix_from_subject
        try:
            return qualifier_suffix_from_subject(self._c.fpr.getSubject(self._rep))
        except Exception:
            return ""

    # read fields
    @property
    def species(self) -> str:
        return self._c.fpr.getSpeciesName(self._rep)

    @property
    def headline(self) -> str:
        return self._row.get("Headline", "")

    @property
    def is_blog_extra(self) -> bool:
        from engine.speciesclassification import SpeciesClassification
        try:
            return SpeciesClassification.isBlogExtra(self._c.fpr.getSubject(self._rep))
        except Exception:
            return False

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
        """Capture time. The UPN summary row often lacks it in the LIVE pipeline
        fpr (only the per-file rows carry EXIF:DateTimeOriginal until a write), so
        fall back to a file row — otherwise the map/travel-path/time-bracket and
        the grid's shooting-order sort all see empty dates."""
        dt = self._row.get("DateTimeOriginal", "")
        if dt:
            return dt
        for fname in self._c._files(self._key):
            frow = self._c.fpr.fi.files_dict.get(fname)
            if frow:
                d = (frow.get("EXIF:DateTimeOriginal")
                     or frow.get("DateTimeOriginal") or "")
                if d:
                    return d
        return ""

    @property
    def place_fields(self) -> dict:
        """Reverse-geocoded location text from the picture's data_pictures row —
        drives the map tooltip's place name (falls back to coordinates when these
        are empty). Mirrors the culling item's place_fields."""
        r = self._row
        return {k: str(r.get(k, "") or "")
                for k in ("Location", "City", "Municipality", "State")}

    @property
    def error_msg(self) -> str:
        return self._c.fpr.getErrorMsg(self._rep)

    @property
    def has_jpg(self) -> bool:
        return self._c.jpg_path(self._key) is not None

    @property
    def discard_allowed(self) -> bool:
        """Whether Blue/Purple (the discard labels) may be set on this picture even
        though it has a JPG. Normally a JPG-bearing picture can't be discarded (the
        JPG is the finished asset), so those labels are greyed. In a trip cull we
        allow it — the JPG can be culled unless it is published in a blog, which the
        delete step enforces with a warning."""
        return getattr(self._c, "allow_jpg_discard", False)

    @property
    def has_gps(self) -> bool:
        return self._c.has_gps(self._key)

    @property
    def has_crop(self) -> bool:
        return self._c.crop_present(self._key)

    @property
    def crop_rect(self) -> dict | None:
        return self._c.crop_rect(self._key)

    @property
    def file_exts(self) -> list[str]:
        files = self._row.get("Files", [])
        exts = ({Path(f).suffix.lstrip(".").upper() for f in files if f}
                if isinstance(files, list) else set())
        if self._key in getattr(self._c, "_extra_jpgs", {}):
            exts.add("JPG")   # a render found on disk but not yet in the file list
        return sorted(exts)

    @property
    def exif(self) -> ExifSummary:
        summary = exif_from_rows(self._row, self._c.rep_file_row(self._key))
        # A GPS pasted this session isn't in self._row (see note_gps_written) —
        # show it anyway instead of leaving the field blank until a re-read.
        override = getattr(self._c, "_gps_override", {}).get(self._key)
        if override:
            lat, lon = override
            summary.gps = f"{lat:.6f}, {lon:.6f} (just placed)"
        return summary

    # write ops
    def set_rating(self, value: int) -> None:
        self._c.apply(self._key, new_stars=value)

    def set_color(self, value: str) -> None:
        # Routed through apply (not fpr.updateLabel) so Rate recomputes too.
        self._c.apply(self._key, new_color=value)

    def set_description(self, value: str) -> None:
        self._c.fpr.updateDescription(self._rep, value)

    def set_headline(self, value: str) -> None:
        self._c.fpr.updateHeadline(self._rep, value)

    # async image
    def request_thumbnail(self, target_w: int, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, target_w, on_ready)

    def request_full_pixmap(self, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, None, on_ready)

    # crop suggestion — a picture with a RAW or an original camera JPG to
    # re-develop (a HEIC-only / render-only UPN has nothing to seed). Post-cull
    # the crop still seeds PhotoLab/Lightroom.
    @property
    def can_set_crop(self) -> bool:
        return (not self._c.read_only
                and self._c._crop_source(self._key) is not None)

    def request_uncropped_pixmap(self, on_ready: PixmapCallback) -> None:
        self._c.request_raw_uncropped(self._key, on_ready)

    def set_crop(self, crop: dict):
        return self._c.set_crop(self._key, crop)

    # exposure suggestion — same carve-out as crop.
    @property
    def can_set_exposure(self) -> bool:
        return (not self._c.read_only
                and self._c._crop_source(self._key) is not None)

    @property
    def exposure(self) -> float:
        return self._c._exposure_for(self._key) or 0.0

    def set_exposure(self, value: float) -> None:
        return self._c.set_exposure(self._key, value)


class PreviewCollection(PictureCollection):
    # The day-folder XMPs are the same files PhotoLab/Lightroom write, so the
    # post-Read&Summarize viewer can pick up external colour/rating edits live —
    # same machinery (watcher + app-active + F5) as the culling viewer.
    supports_external_refresh = True

    # A viewer over pictures we must not touch (the virtual trip's files ARE the
    # master's — see the Album Rating Editor, read-only for the same reason). The
    # flag greys the edit affordances (rating / colour / description / crop) AND
    # hard-refuses the write paths, so a stray caller can't slip past the UI.
    read_only = False

    # THE ONE CARVE-OUT from read_only: GPS + place may still be written.
    #
    # A virtual trip's pictures ARE the master's, which is exactly why the map is
    # otherwise look-don't-touch. But it is also the best surface we have for
    # FIXING the master's missing coordinates — the day map, the no-GPS tray and
    # the time-interpolated position suggestion already exist and are proven. So
    # geotagging is allowed through deliberately, and only from a caller that opts
    # in; rating, colour, crop, description and delete stay refused.
    #
    # Fill-empties only: the map's drag-a-bubble-to-MOVE (which overwrites GPS the
    # camera recorded) is switched off in this mode. Cleaning up the master means
    # adding what is missing, never overwriting what is already there.
    allow_geotag = False

    # Whether Blue/Purple (discard labels) may be set on a JPG-bearing picture. Off
    # by default (a JPG is the finished asset); the trip-cull viewer turns it on so
    # a JPG that isn't published in a blog can be marked for delete.
    allow_jpg_discard = False

    def __init__(self, fpr, *, loader: AsyncImageLoader | None = None,
                 read_only: bool = False, allow_geotag: bool = False):
        self.fpr = fpr
        self.read_only = bool(read_only)
        self.allow_geotag = bool(allow_geotag)
        # Which of NL/EN/Sci reads out under each card — the global default (the
        # culling viewer has no per-trip override). Items resolve their names
        # through this so the culling grid follows the same profile as everywhere.
        from utils.name_profile import effective_profile
        self._name_profile = effective_profile()
        self._loader = loader or AsyncImageLoader()
        # Scaled-thumbnail cache, shared with the large-viewer filmstrip via this
        # same collection: a colour-filter rebuild re-attaches pixmaps instead of
        # re-decoding every preview. Keyed by (key, width, image signature).
        from gui.thumb_cache import ThumbCache
        self._thumb_cache = ThumbCache()
        self._order: list[str] = []
        self.rebuild()
        # Baseline the colour/rating sidecar mtimes NOW (the fpr matches disk at
        # load), so the first refresh detects only what an external tool changes
        # afterwards — not the whole day.
        self._xmp_mtimes: dict = {}
        self._image_changed: set = set()
        self._baseline_xmp_mtimes()
        # Renders (DxO _DxO.jpg / LR exports) found on disk that aren't in the
        # pipeline file list yet (created after Read & Summarize). Side-channel so
        # the card note can show JPG without touching the fpr's file rows.
        self._extra_jpgs: dict = {}
        self._scan_extra_jpgs()
        # Baseline image signatures so refresh can tell a re-crop / new render
        # (re-decode) from a colour/rating text edit.
        self._img_sigs: dict = {k: self._img_sig(k) for k in self.all_keys()}
        # A single colour/rating edit rewrites BOTH day CSVs (data_pictures ≈ N
        # rows, data_files ≈ 2N). Tagging one card at a time on a 1000+ picture
        # day, that is the felt lag — so apply() only MARKS the CSVs dirty and a
        # debounced flush_csv() (host timer + focus-out + close + any real
        # refresh) does the write once the burst settles.
        self._csv_dirty_summary = False
        self._csv_dirty_files = False
        # Our own .xmp write trips the folder watcher ~400 ms later; that echo is
        # not an external edit and must not trigger a full disk re-read.
        self._self_write_ts = 0.0

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

    def find_keys(self, query: str) -> list[str]:
        """UPNs whose species name OR raw tagged subject contains `query` (case-
        insensitive substring) — the 'find species' box. Matching the raw subject
        too means a typo'd/unresolved name (e.g. 'paraque') is still findable."""
        q = (query or "").strip().lower()
        if not q:
            return self.all_keys()
        out = []
        for k in self.all_keys():
            rep = self.rep_filename(k)
            name = (self.fpr.getSpeciesName(rep) or "").lower()
            subj = (self.fpr.getSubject(rep) or "").lower()
            if q in name or q in subj:
                out.append(k)
        return out

    def file_keys(self, query: str) -> list[str]:
        """UPNs whose name (the picture key, e.g. '2024 costa rica-nb188309')
        contains `query` — the 'find file' box. Works regardless of species
        recognition, so it finds problem pictures by filename fragment."""
        q = (query or "").strip().lower()
        if not q:
            return self.all_keys()
        return [k for k in self.all_keys() if q in k.lower()]

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

    def blog_extra_keys(self) -> list[str]:
        """UPNs marked as a landscape / context 'blog extra' — the subject carries
        the landscape marker. These ARE committable (they get a home in the master
        under topics/<topic>/<leaf>) but are counted and shown as landscapes, not
        species — so the Add-to-Master line doesn't call a landscape 'species 2025'.
        Keyed on the subject marker so it holds both BEFORE ingest (species not yet
        resolved) and AFTER (the topic leaf sits in the species name slot). Callers
        that also count species subtract this set to avoid double-counting a
        picture that carries both a real species and the marker. See topics.py."""
        from engine.speciesclassification import SpeciesClassification
        out = []
        for k in self.all_keys():
            rep = self.rep_filename(k)
            if SpeciesClassification.isBlogExtra(self.fpr.getSubject(rep) or ""):
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
                                   if self.has_disk_file(k)]
        return self._present_cache

    # ── PictureCollection ─────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._order)

    def item(self, index: int) -> PreviewItem:
        return PreviewItem(self, self._order[index])

    def item_for_key(self, key: str) -> PreviewItem:
        """An item for ANY valid UPN — independent of the current filter/sort
        (`_order` may be a filtered subset, e.g. the 📍 no-GPS filter; the map
        plots all_keys and must still read a GPS'd picture's fields)."""
        if key not in self.fpr.upn.UniquePictureName_dict:
            raise KeyError(key)
        return PreviewItem(self, key)

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

    def video_path(self, key: str) -> Path | None:
        """Absolute path of the UPN's video file, if it has one. QuickViewer's
        hook for playing a movie instead of showing a still — only relevant when
        raw_path() is None (a UPN with a RAW/JPG plays neither)."""
        for fname in self._files(key):
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and ("." + frow.get("WindowsExtension", "").lower()) in _VIDEO_EXTS:
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

    def gps_cached(self, upn_key: str):
        """(lat, lon) parsed from the in-memory file rows' Composite:GPSPosition —
        no file read — so the map plots a whole day without an exiftool spawn per
        picture. Same DMS→decimal parse the master DB uses.

        A coordinate WE wrote this session wins over the row. The rows come from
        the last Read & Summarize and know nothing about a paste or a map
        placement until the next one; the override was written to the XMP on
        disk seconds ago. Without this the map rebuilt itself from the stale
        rows every time it re-opened, and a picture you had just placed was back
        in the no-GPS tray as if nothing had happened — the sidecar collection
        never had the bug because ITS note_gps_written writes into the very dict
        this reads, and only this one kept the two apart.
        """
        override = getattr(self, "_gps_override", {}).get(upn_key)
        if override:
            return override
        from engine.geo import parse_gps_dms
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            pos = frow.get("Composite:GPSPosition") or ""
            if pos:
                ll = parse_gps_dms(pos)
                if ll:
                    return ll
        return None

    def location_targets(self, upn_key: str):
        """The UPN's XMP/JPG files — where LR-style location text is written.

        When the file index carries no existing sidecar/JPG (a RAW whose .xmp was
        created after the last Read, or a stale/empty indexed path), fall back to
        the RAW's sibling `<stem>.xmp`: exiftool creates it, and it's the sidecar
        PhotoLab/Lightroom read. Only HEIC/PNG-only UPNs genuinely have nowhere to
        write — raw_path is None there, so they still return [].

        A read-only collection has NO write targets: that is what stops the map's
        drag-to-geotag reaching the master's sidecars through a virtual trip —
        UNLESS allow_geotag is set, the deliberate exception for cleaning up the
        master's missing coordinates (see the class comment). This method is THE
        gate every GPS/place write passes through, so the exception is made here,
        once, rather than at each call site."""
        if self.read_only and not self.allow_geotag:
            return []
        out = []
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() in ("xmp", "jpg", "jpeg"):
                wf = frow.get("WindowsFileName", "")
                if wf and Path(wf).exists():
                    out.append(wf)
        if out:
            return out
        raw = self.raw_path(upn_key)
        if raw:
            # Guardrail: the index named a sidecar/JPG but none resolved to a live
            # path (dead/stale/mangled WindowsFileName — the trip-side twin of the
            # master dead-link issue). Recompute from the RAW's real location and
            # log it, so we can measure how widespread stale trip sidecars are.
            named = [f for f in self._files(upn_key)
                     if Path(f).suffix.lower() in (".xmp", ".jpg", ".jpeg")]
            if named:
                logger.warning(
                    "location_targets: %s — indexed sidecar/JPG %s had no live path; "
                    "falling back to sibling .xmp next to %s",
                    upn_key, named, raw.name)
            return [str(raw.with_suffix(".xmp"))]
        return []

    # Location text lives in data_files.csv as these XMP:<field> columns —
    # Read Directory already wrote them, so read_location() below is a plain
    # in-memory lookup instead of an exiftool spawn per picture. CountryCode
    # is not one of the CSV's columns, so it always comes back empty here
    # (nothing in this collection's display reads it).
    _LOC_XMP_KEYS = {"Location": "XMP:Location", "City": "XMP:City",
                     "Municipality": "XMP:Municipality", "State": "XMP:State",
                     "Country": "XMP:Country"}

    def read_location(self, upn_key: str) -> dict:
        """Location text from the in-memory file rows data_files.csv already
        loaded — no exiftool spawn per navigation step, unlike a fresh read
        of the file (~0.3-0.8s, the dominant cost of stepping through the
        large viewer on a big day). Falls back to a direct file read only
        when the CSV has nothing for any of the UPN's files (e.g. a file
        added since the last Read Directory)."""
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            d = {field: frow.get(xmp_key) or ""
                for field, xmp_key in self._LOC_XMP_KEYS.items()}
            d["CountryCode"] = ""
            if any(d.values()):
                return d
        from gui.geolocate import read_location_fields
        for f in (self.location_targets(upn_key) or [self.rep_full_path(upn_key)]):
            if f and Path(f).exists():
                d = read_location_fields(f)
                if any(d.values()):
                    return d
        return {}

    def note_location_written(self, upn_key: str, fields: dict, protect=()) -> None:
        """An Edit/Get-location write just put these place fields into the
        UPN's XMP — patch the in-memory file rows so read_location() (which
        reads from here now, not a fresh exiftool spawn) reflects the write
        immediately rather than showing the pre-write value until the next
        Read Directory. `protect` names fields whose pre-existing value must
        be kept (a coarse reverse-geocode must not overwrite a hand-entered
        Location) — same contract as sidecar_provider's."""
        protect = set(protect)
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            for field, value in fields.items():
                if not value:
                    continue
                xmp_key = self._LOC_XMP_KEYS.get(field)
                if xmp_key is None:
                    continue   # CountryCode — not a CSV column, nothing to patch
                if field in protect and (frow.get(xmp_key) or "").strip():
                    continue
                frow[xmp_key] = value

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

    def _heic_path(self, upn_key: str) -> Path | None:
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and frow.get("WindowsExtension", "").lower() in ("heic", "heif"):
                p = Path(frow.get("WindowsFileName", ""))
                if p.exists():
                    return p
        return None

    def jpg_names(self, upn_key: str) -> set:
        """Basenames of every JPG/JPEG this UPN carries (a _DxO.jpg render, a camera
        JPG). Used to test a picture against a blog's BestJPGs list when deciding
        whether a trip JPG may be blue/purple-deleted."""
        out = set()
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and frow.get("WindowsExtension", "").lower() in ("jpg", "jpeg"):
                name = Path(frow.get("WindowsFileName", "")).name
                if name:
                    out.add(name)
        return out

    def has_gps(self, upn_key: str) -> bool:
        if upn_key in getattr(self, "_pasted_gps", ()):   # GPS just pasted into the XMP
            return True
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and (frow.get("Composite:GPSPosition") or frow.get("EXIF:GPSLatitude")):
                return True
        return False

    def note_gps_written(self, upn_key: str, lat=None, lon=None) -> None:
        """A paste-GPS wrote GPS into this UPN's XMP — remember it so the
        missing-GPS badge clears without a rescan. Kept in a side set rather than
        poked into the pipeline's in-memory file rows (which feed Write Summary).

        (lat, lon), when given, also go into a small override dict so the info
        panel's EXIF/GPS text (PreviewItem.exif, sourced from those same
        in-memory rows) shows the new coordinate immediately too — otherwise it
        stays blank for the rest of the session even though has_gps() already
        knows better. Mirrors SidecarCollection's _gps_by_stem.

        Anything WE wrote is by definition human-placed, so it also becomes movable
        immediately — without this a picture you just placed would be frozen until
        the next master re-read populated its GPSProcessingMethod column."""
        if not hasattr(self, "_pasted_gps"):
            self._pasted_gps = set()
        self._pasted_gps.add(upn_key)
        if lat is not None and lon is not None:
            if not hasattr(self, "_gps_override"):
                self._gps_override = {}
            self._gps_override[upn_key] = (float(lat), float(lon))

    def gps_is_derived(self, upn_key: str) -> bool:
        """True when this picture's coordinate was placed by a HUMAN — dragged onto
        the map, copied from a neighbour, or looked up from a place name — rather
        than recorded by the camera.

        This is the one thing that grants permission to MOVE a picture: a camera's
        fix is evidence and we do not overwrite it, but a coordinate we derived is a
        guess and refining it is the whole point. Provenance is read from
        GPSProcessingMethod, never inferred from how precise the number looks —
        1.9% of the master's genuine fixes already print as whole arcseconds.
        """
        from gui.geolocate import GPS_MANUAL
        if upn_key in getattr(self, "_pasted_gps", ()):   # we wrote it this session
            return True
        row = self.fpr.upn.UniquePictureName_dict.get(upn_key) or {}
        if (row.get("GPSProcessingMethod") or "").strip().upper() == GPS_MANUAL:
            return True
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname) or {}
            if (frow.get("EXIF:GPSProcessingMethod") or "").strip().upper() == GPS_MANUAL:
                return True
        return False

    # ── external-edit refresh (PhotoLab / Lightroom colour + rating) ──────────
    def _xmp_for(self, upn_key: str):
        """The .xmp sidecar carrying this picture's colour/rating, or None."""
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and frow.get("WindowsExtension", "").lower() == "xmp":
                p = Path(frow.get("WindowsFileName", ""))
                if p.exists():
                    return p
        p = self.rep_full_path(upn_key)
        if p:
            for xmp in (Path(p).with_suffix(".xmp"), Path(p).with_suffix(".XMP")):
                if xmp.exists():
                    return xmp
        return None

    def _baseline_xmp_mtimes(self):
        import os
        for key in self.all_keys():
            xmp = self._xmp_for(key)
            if xmp:
                try:
                    self._xmp_mtimes[str(xmp)] = os.stat(xmp).st_mtime_ns
                except OSError:
                    pass

    def _img_sig(self, upn_key: str):
        """Signature of what the displayed image depends on — the source (a render
        switches it RAW→JPG) + its mtime + the .dop mtime (crop). A change means
        re-decode, vs a colour/rating text edit."""
        import os
        src = self._display_source(upn_key)
        raw = self._raw_path(upn_key)
        dop = Path(str(raw) + ".dop") if raw else None

        def _mt(p):
            try:
                return os.stat(p).st_mtime_ns
            except OSError:
                return None
        return (str(src) if src else None, _mt(src) if src else None,
                _mt(dop) if dop else None)

    def _pic_folder_set(self) -> set:
        if getattr(self, "_pic_folders", None) is None:
            folders = set()
            for key in self.all_keys():
                p = self.rep_full_path(key)
                if p:
                    folders.add(Path(p).parent)
            self._pic_folders = folders
        return self._pic_folders

    def _scan_extra_jpgs(self) -> set:
        """Find *.jpg renders (DxO _DxO.jpg, LR/v5 exports) in the picture folders
        that aren't already in a UPN's pipeline file list — created after Read &
        Summarize — so the card's file-type note can show JPG. Side-channel only:
        does NOT touch the fpr file rows (pipeline data). Returns the keys that
        gained a render."""
        from engine.handling import deriveUniquePictureName
        gained = set()
        for folder in self._pic_folder_set():
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for p in entries:
                if p.suffix.lower() not in (".jpg", ".jpeg") or p.name.lower().endswith("-small.jpg"):
                    continue
                key = deriveUniquePictureName(p.name)
                if (key not in self.fpr.upn.UniquePictureName_dict
                        or key in self._extra_jpgs):
                    continue
                files = self.fpr.upn.UniquePictureName_dict.get(key, {}).get("Files", []) or []
                if any(str(f).lower() == p.name.lower() for f in files):
                    continue   # the render is already a tracked file → note shows it
                self._extra_jpgs[key] = p
                gained.add(key)
        return gained

    def sidecar_paths(self) -> list:
        out = []
        for key in self.all_keys():
            xmp = self._xmp_for(key)
            if xmp:
                out.append(str(xmp))
            raw = self._raw_path(key)
            if raw:
                dop = Path(str(raw) + ".dop")   # watch the crop recipe too
                if dop.exists():
                    out.append(str(dop))
        return out

    # ── deferred day-CSV writes (colour/rating burst while culling) ───────────
    def flush_csv(self) -> None:
        """Persist the day CSVs if a colour/rating edit left them dirty. Cheap
        no-op otherwise. Called on the host debounce, on focus-out, on close,
        and at the top of refresh()."""
        if not (self._csv_dirty_summary or self._csv_dirty_files):
            return
        try:
            if self._csv_dirty_summary:
                self.fpr.upn.write_project_summary()
            if self._csv_dirty_files:
                self.fpr.fi.write_project_files()
            self._csv_dirty_summary = self._csv_dirty_files = False
        except Exception as e:
            logger.error(f"Preview: failed to persist day CSV(s): {e}")

    def has_pending_csv(self) -> bool:
        return bool(self._csv_dirty_summary or self._csv_dirty_files)

    def consume_self_write_echo(self) -> bool:
        """True when a watch-driven refresh is just the echo of a colour/rating
        write we made in the last ~2 s — the caller may then skip the disk
        re-read (the in-memory rows are already current). Clears the marker, so
        a genuine external edit arriving afterwards still gets a real refresh."""
        import time as _time
        if self._self_write_ts and (_time.monotonic() - self._self_write_ts) < 2.0:
            self._self_write_ts = 0.0
            return True
        return False

    def refresh(self) -> set:
        """Re-read XMP:Label / XMP:Rating that an external tool (PhotoLab,
        Lightroom) wrote, and update the in-memory fpr rows so the open viewer
        reflects them. Only sidecars whose mtime changed since the baseline are
        re-read via exiftool. Returns the changed UPN keys; persists the summary
        CSV so the change survives close + reaches downstream steps."""
        import os
        self.flush_csv()   # land any debounced edits before we re-read/re-persist
        self._image_changed = set()
        changed = set()
        to_read = []
        for key in self.all_keys():
            xmp = self._xmp_for(key)
            if not xmp:
                continue
            try:
                mt = os.stat(xmp).st_mtime_ns
            except OSError:
                continue
            if self._xmp_mtimes.get(str(xmp)) != mt:
                to_read.append((key, xmp, mt))
        by_src = {}
        if to_read:
            try:
                # Through an argfile, never the command line: "only sidecars whose
                # mtime changed" is normally a handful, but it is a whole day the
                # first time a folder is seen — 678 sidecars created at once
                # overflowed Windows' ~32 KB command line and this refresh died
                # with WinError 206, silently and on every pass (2026-09-22).
                from engine.exif_writer import run_exiftool_files
                out = run_exiftool_files(
                    ["-j", "-q", "-XMP:Label", "-XMP:Rating"],
                    [str(xmp) for _k, xmp, _mt in to_read], timeout=60).stdout
                rows = json.loads(out) if out.strip() else []
                by_src = {os.path.normcase(os.path.normpath(r.get("SourceFile", ""))): r for r in rows}
            except Exception as e:
                logger.warning(f"External-edit refresh: exiftool failed: {e}")
                to_read = []
        for key, xmp, mt in to_read:
            self._xmp_mtimes[str(xmp)] = mt
            r = by_src.get(os.path.normcase(os.path.normpath(str(xmp))))
            upn_row = self.fpr.upn.UniquePictureName_dict.get(key)
            if r is None or upn_row is None:
                continue
            diff = False
            new_label = str(r.get("Label", "") or "")
            if new_label != str(upn_row.get("Label", "") or ""):
                upn_row["Label"] = new_label
                diff = True
            new_rating = r.get("Rating", None)
            if new_rating not in (None, "") and str(new_rating) != str(upn_row.get("Rating", "") or ""):
                upn_row["Rating"] = str(new_rating)
                diff = True
            if diff:
                changed.add(key)
        if changed:
            try:
                self.fpr.upn.write_project_summary()
            except Exception as e:
                logger.warning(f"External-edit refresh: could not persist summary: {e}")
            logger.info(f"External-edit refresh: {len(changed)} picture(s) updated from disk (colour/rating).")
        # A render may have appeared since the last scan — fold it in so the
        # file-type note gains JPG (no exiftool, just a folder listing).
        changed |= self._scan_extra_jpgs()
        # Image-change detection (new render switches the source, a re-crop bumps
        # the .dop) → the host re-decodes only those; a colour edit isn't here.
        new_sigs = {k: self._img_sig(k) for k in self.all_keys()}
        self._image_changed = {k for k in new_sigs if new_sigs[k] != self._img_sigs.get(k)}
        self._img_sigs = new_sigs
        for k in self._image_changed:
            self._drop_thumb_caches(k)
        changed |= self._image_changed
        return changed

    def _drop_thumb_caches(self, key):
        """Forget every cached thumbnail (RAM + on-disk) for one picture — its
        image changed (re-crop / new render / Create JPG)."""
        self._thumb_cache.invalidate(key)
        from gui.thumb_cache import drop_disk_thumbs
        seen = set()
        for p in (self._display_source(key), self._raw_path(key)):
            if p is not None and p.stem not in seen:
                seen.add(p.stem)
                drop_disk_thumbs(preview_cache_dir_for(p.parent), p.stem)

    def has_disk_file(self, upn_key: str) -> bool:
        """True if at least one of the UPN's real files (not a stray .dop) is
        actually ON DISK. This is about EXISTENCE, not permission: a read-only
        collection's pictures are still present. A UPN that fails this is a ghost
        — an index row whose files were deleted, moved, or never copied here."""
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() == "dop":
                continue
            if Path(frow.get("WindowsFileName", "")).exists():
                return True
        return False

    def has_writable_sidecar(self, upn_key: str) -> bool:
        """True if rating/colour can be persisted for this picture — either an
        existing XMP/JPG to write, or a source image (HEIC/PNG/RAW) we can drop a
        standard <stem>.xmp sidecar beside (see apply / _create_xmp_sidecar).
        Only false for the degenerate case of a UPN with no on-disk image file
        (e.g. a stray .dop), so the card greys nothing in practice — and for a
        read-only collection, which may look but not touch."""
        return not self.read_only and self.has_disk_file(upn_key)

    def _source_file(self, upn_key: str) -> Path | None:
        """Best display source: the BestJPG render (the DxO `_DxO.jpg` crop) >
        any JPG > RAW > HEIC > video. Honours the pipeline's already-computed
        BestJPG so the card shows the processed crop, not the untouched original
        that merely happens to come first in the Files list. Video is last: a
        UPN with any still at all shows the still, and a frame grab only stands
        in for a picture that is otherwise blank."""
        best_name = (self.fpr.upn.UniquePictureName_dict
                     .get(upn_key, {}).get("BestJPG") or "").strip().lower()
        best = jpg = raw = heic = video = None
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            ext = frow.get("WindowsExtension", "").lower()
            p = Path(frow.get("WindowsFileName", ""))
            if not p.exists():
                continue
            if ext in ("jpg", "jpeg"):
                if jpg is None:
                    jpg = p
                if best_name and p.name.lower() == best_name:
                    best = p
            elif ("." + ext) in _RAW_EXTS and raw is None:
                raw = p
            elif ext == "heic" and heic is None:
                heic = p
            elif ("." + ext) in _VIDEO_EXTS and video is None:
                video = p
        return best or jpg or raw or heic or video

    def _raw_path(self, upn_key: str) -> Path | None:
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if frow and ("." + frow.get("WindowsExtension", "").lower()) in _RAW_EXTS:
                p = Path(frow.get("WindowsFileName", ""))
                if p.exists():
                    return p
        return None

    def _crop_source(self, upn_key: str) -> Path | None:
        """The image the crop tool draws on: the RAW if there is one, else the
        original camera JPG — the LARGEST tracked JPG that is not itself a DxO/LR
        render (`_DxO`/`-small`). Lets a phone JPG be cropped too: the recipe
        lands in <jpg>.dop + <jpg-stem>.xmp, exactly what PhotoLab/Lightroom read.
        The render (_DxO.jpg) is skipped so we never crop an already-cropped file.
        A HEIC has no RAW/recipe pipeline but is croppable by baking the rectangle
        into a JPG (set_crop does that) — so it is a valid crop source, preferred
        over any sibling <stem>.jpg, which IS that previous bake (cropping it again
        would crop an already-cropped image)."""
        raw = self.raw_path(upn_key)
        if raw is not None:
            return raw
        heic = self._heic_path(upn_key)
        if heic is not None:
            return heic
        best = None
        best_area = -1
        for fname in self._files(upn_key):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            if frow.get("WindowsExtension", "").lower() not in ("jpg", "jpeg"):
                continue
            name = (frow.get("File:FileName", "")
                    or Path(frow.get("WindowsFileName", "")).name).lower()
            if "dxo" in name or "-small" in name:
                continue   # a render/derivative, not the original to re-crop
            p = Path(frow.get("WindowsFileName", ""))
            if not p.exists():
                continue
            try:
                width, height = map(int, re.split(r"[x ]", frow.get("ImageSize", "")))
                area = width * height
            except (ValueError, TypeError):
                area = 0
            if area > best_area:
                best_area = area
                best = p
        return best

    def _display_source(self, upn_key: str) -> Path | None:
        """Best display source. A render found on disk (DxO _DxO.jpg — already
        cropped + processed) wins, so the viewer matches what PhotoLab shows;
        else the tracked JPG > RAW > HEIC."""
        extra = self._extra_jpgs.get(upn_key)
        if extra and extra.exists():
            return extra
        return self._source_file(upn_key)

    def display_path(self, upn_key: str) -> Path | None:
        """Synchronous loadable path (JPG direct, or a cached preview for
        RAW/HEIC, extracting/decoding if needed). Used by the large view."""
        src = self._display_source(upn_key)
        if src is None:
            return None
        return cached_jpg_for(src, preview_cache_dir_for(src.parent))

    def _stale_render_fallback(self, upn_key: str, render_src: Path):
        """A render (_DxO/-small) is normally trusted as already-cropped. But if our
        crop is NEWER than the render, the render predates the crop (DxO hasn't
        re-exported yet) and is full-frame — so fall back to the RAW/original + the
        crop, so the thumbnail matches the edit. Returns (base, crop) to switch to,
        or (None, None) to keep trusting the render."""
        base = self._crop_source(upn_key)          # RAW / original JPG (never a render)
        if base is None:
            return None, None
        crop = crop_for(base, self._xmp_for(upn_key))
        if not crop:
            return None, None                      # no crop → nothing to apply
        # mtime of whichever sidecar holds the crop (.dop preferred, else .xmp).
        dop = Path(str(base) + ".dop")
        crop_mtime = dop.stat().st_mtime if dop.exists() else None
        if crop_mtime is None:
            xmp = self._xmp_for(upn_key)
            crop_mtime = Path(xmp).stat().st_mtime if xmp and Path(xmp).exists() else None
        try:
            render_mtime = Path(render_src).stat().st_mtime
        except OSError:
            return None, None
        if crop_mtime is not None and crop_mtime > render_mtime:
            return base, crop                      # render is stale → show base + crop
        return None, None

    def request_image(self, upn_key: str, target_w, on_ready: PixmapCallback) -> None:
        src = self._display_source(upn_key)
        # Crop the preview to the PhotoLab/LR crop so the composition matches the
        # edit. Applies to a RAW preview OR an ORIGINAL camera JPG (reads its crop
        # from <source>.dop — what our crop tool wrote). A render (_DxO/-small) is
        # normally shown as-is (already cropped) — unless it's older than our crop,
        # in which case it's stale and we show the RAW/original + crop instead.
        crop = None
        if src:
            ext = src.suffix.lower()
            is_render = "dxo" in src.name.lower() or "-small" in src.name.lower()
            if is_render:
                base, base_crop = self._stale_render_fallback(upn_key, src)
                if base is not None:
                    src, crop = base, base_crop
            elif ext in _RAW_EXTS:
                crop = crop_for(src, self._xmp_for(upn_key))
            elif ext in (".jpg", ".jpeg"):
                crop = crop_for(src)   # <jpg>.dop, written by suggest_crop

        def produce():
            if src is None:
                return None
            p = cached_jpg_for(src, preview_cache_dir_for(src.parent))
            if p and p.exists():
                img = QImage(str(p))
                if img.isNull():
                    return None
                return apply_crop(img, crop) if crop else img
            return None
        from gui.thumb_cache import cached_thumb_request
        sig = self._img_sigs.get(upn_key)   # source + mtimes; changes on re-crop / new render
        disk_dir = preview_cache_dir_for(src.parent) if src is not None else None
        stem = src.stem if src is not None else None
        cached_thumb_request(self._thumb_cache, self._loader, upn_key,
                             target_w, sig, produce, on_ready,
                             disk_dir=disk_dir, stem=stem)

    def crop_present(self, upn_key: str) -> bool:
        """True if this picture carries a crop, for the card's "#" marker. Checks
        the croppable BASE (RAW / original JPG), not the display source, so a
        picture whose render exists still shows it. One small .dop/.xmp read per
        call, then a free size-cue fallback: a baked DxO/LR render keeps no
        readable rectangle, so a JPG much smaller than its RAW is taken as a crop
        (jpg_is_crop_by_size) — the same rule the trip/master viewers use."""
        if self.crop_rect(upn_key) is not None:
            return True
        row = self.fpr.upn.UniquePictureName_dict.get(upn_key, {})
        return jpg_is_crop_by_size(row.get("ImageSize", ""), row.get("JPGSize", ""))

    def crop_rect(self, upn_key: str):
        """The crop rectangle {left,top,width,height} (0-1) from the croppable
        BASE's .dop/.xmp, or None. crop_present's readable half — the large
        viewer prints the cropped pixel size from it."""
        base = self._crop_source(upn_key)
        return crop_for(base, self._xmp_for(upn_key)) if base is not None else None

    def request_raw_uncropped(self, upn_key: str, on_ready: PixmapCallback) -> None:
        """The full uncropped frame — the canvas the crop tool draws on (bypasses
        _display_source, which would prefer an already-cropped render). RAW when
        present, else the original camera JPG (so phone JPGs are croppable too)."""
        raw = self._crop_source(upn_key)
        if raw is None:
            on_ready(None)
            return

        def produce():
            p = cached_jpg_for(raw, preview_cache_dir_for(raw.parent))
            if p and p.exists():
                img = QImage(str(p))
                return img if not img.isNull() else None
            return None
        self._loader.request(produce, on_ready, target_w=None)

    def set_crop(self, upn_key: str, crop: dict):
        """Write a suggested crop to the UPN's source sidecars (.dop + .xmp),
        creating one if missing. Uses the RAW when present, else the original
        camera JPG (writing <jpg>.dop, exactly what PhotoLab reads). Refreshes the
        image signature so refresh() doesn't re-report our own write. Returns the
        sidecarlib result, or None."""
        if self.read_only:
            logger.warning(f"set_crop refused for {upn_key}: collection is read-only.")
            return None
        raw = self._crop_source(upn_key)
        if not raw:
            return None
        if raw.suffix.lower() in (".heic", ".heif"):
            return self._bake_heic_crop(upn_key, raw, crop)
        from engine.sidecarlib import suggest_crop
        res = suggest_crop(str(raw), crop)
        self._img_sigs[upn_key] = self._img_sig(upn_key)
        self._drop_thumb_caches(upn_key)   # a crop can land in the .xmp, which the sig doesn't see
        return res

    def _exposure_for(self, upn_key: str):
        """Current suggested exposure (EV) for the UPN's croppable base (RAW or
        original camera JPG), from its .dop (preferred) or .xmp. Mirrors the
        crop reader exactly — no cache here (crop_for/exposure_for are cheap
        sidecar reads, and this collection has no per-stem crop cache either;
        _crop_for-style caching lives in sidecar_provider, the other engine)."""
        base = self._crop_source(upn_key)
        if base is None:
            return None
        return exposure_for(base, self._xmp_for(upn_key))

    def set_exposure(self, upn_key: str, exposure: float):
        """Write a suggested exposure (EV) to the UPN's source sidecars (.dop +
        .xmp), creating the .xmp if missing — the exposure mirror of set_crop.
        Returns the sidecarlib result, or None."""
        if self.read_only:
            logger.warning(f"set_exposure refused for {upn_key}: collection is read-only.")
            return None
        raw = self._crop_source(upn_key)
        if not raw:
            return None
        if raw.suffix.lower() in (".heic", ".heif"):
            logger.info(f"set_exposure: no develop step for a HEIC-only picture ({upn_key}).")
            return None
        from engine.sidecarlib import suggest_exposure
        return suggest_exposure(str(raw), exposure)

    def create_blog_jpg(self, upn_key: str) -> dict:
        """Bake a website-ready JPG beside the RAW from the camera's embedded
        preview + the sidecar crop (NO scaling) — a quick trip-blog picture
        without the full DxO render. `<stem>.jpg` folds onto the UPN
        (deriveUniquePictureName), so it becomes the picture's usable image on
        the next Read & Summarize.

        Returns {'status': 'created'|'skipped'|'no_raw'|'failed', ...}. Skips a
        picture that already has a tracked JPG (a camera JPEG or a DxO render) so
        nothing is clobbered; our own bake is not tracked, so a re-run overwrites
        it (e.g. after re-cropping)."""
        raw = self.raw_path(upn_key)
        if raw is None:
            return {"status": "no_raw"}
        if self.jpg_path(upn_key) is not None:
            return {"status": "skipped", "jpg": str(self.jpg_path(upn_key))}
        out = raw.with_suffix(".jpg")   # <stem>.jpg beside the RAW
        from gui.preview_extract import extract_preview_bytes, decode_preview_jpeg
        data = extract_preview_bytes(raw)   # LARGEST embed — full-size on Nikon, 3200px on OM-1
        if not data:
            # No embedded JPEG at all (the R5 II's HDR-PQ CR3). Decode the RAW —
            # at FULL size (max_edge 0), not the 3200px preview size: this JPG
            # becomes the picture's usable image and can end up on the website, so
            # it has to match what Create JPG gives every other body.
            data = decode_preview_jpeg(raw, 0, 95)
        if not data:
            return {"status": "failed", "reason": "no preview, and the RAW "
                                                  "could not be decoded"}
        try:
            from io import BytesIO
            from PIL import Image
            im = Image.open(BytesIO(data)).convert("RGB")
            crop = crop_for(raw, self._xmp_for(upn_key))   # same reader request_image uses
            if crop:
                w, h = im.size
                x  = max(0, min(w - 1, round(crop.get("left", 0.0)   * w)))
                y  = max(0, min(h - 1, round(crop.get("top", 0.0)    * h)))
                cw = max(1, min(w - x, round(crop.get("width", 1.0)  * w)))
                ch = max(1, min(h - y, round(crop.get("height", 1.0) * h)))
                im = im.crop((x, y, x + cw, y + ch))
            im.save(str(out), "JPEG", quality=92, optimize=True)
        except Exception as e:
            logger.warning(f"Create JPG failed for {raw.name}: {e}")
            return {"status": "failed", "reason": str(e)}
        # Reflect it at once (mirrors _bake_heic_crop): show the new JPG as this
        # UPN's display source and bump the signature so the card re-decodes.
        self._extra_jpgs[upn_key] = out
        if getattr(self, "_pic_folders", None) is not None:
            self._pic_folders.add(out.parent)
        self._img_sigs[upn_key] = self._img_sig(upn_key)
        self._drop_thumb_caches(upn_key)   # a crop can land in the .xmp, which the sig doesn't see
        logger.info(f"Blog JPG created → {out.name} "
                    f"({im.size[0]}×{im.size[1]}, {'cropped' if crop else 'full frame'}).")
        return {"status": "created", "jpg": str(out), "size": im.size, "cropped": bool(crop)}

    def _bake_heic_crop(self, upn_key: str, heic: Path, crop: dict):
        """Crop a HEIC by baking the rectangle into a FULL-RES JPG beside it — a HEIC
        has no RAW-style develop step, so the crop is applied directly (the user's
        'crop by writing a JPG, just like RAWs'). The JPG shares the HEIC's stem, so
        it folds onto the same picture and becomes its usable image; a re-crop
        re-bakes from the HEIC (never from this JPG — see _crop_source)."""
        out = heic.with_suffix(".jpg")   # <stem>.jpg — same stem folds onto the UPN
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
            from PIL import Image
            im = Image.open(str(heic)).convert("RGB")   # FULL resolution, not the capped preview
            w, h = im.size
            x  = max(0, min(w - 1, round(crop.get("left", 0.0)   * w)))
            y  = max(0, min(h - 1, round(crop.get("top", 0.0)    * h)))
            cw = max(1, min(w - x, round(crop.get("width", 1.0)  * w)))
            ch = max(1, min(h - y, round(crop.get("height", 1.0) * h)))
            im.crop((x, y, x + cw, y + ch)).save(str(out), "JPEG", quality=92, optimize=True)
        except Exception as e:
            logger.warning(f"HEIC crop bake failed for {heic.name}: {e}")
            return None
        # Show the crop at once: fold the new JPG in as this UPN's display source and
        # bump the image signature so refresh_external re-decodes to the cropped JPG.
        self._extra_jpgs[upn_key] = out
        if getattr(self, "_pic_folders", None) is not None:
            self._pic_folders.add(out.parent)
        self._img_sigs[upn_key] = self._img_sig(upn_key)
        self._drop_thumb_caches(upn_key)   # a crop can land in the .xmp, which the sig doesn't see
        logger.info(f"HEIC crop baked → {out.name} (full-res JPG).")
        return {"jpg_created": True, "baked_jpg": True, "jpg": str(out)}

    def delete_keys(self, keys, trash_root: Path,
                    allow_jpg_keys: frozenset = frozenset(),
                    progress_cb=None) -> tuple[int, int]:
        """Move the given UPN keys to trash → (protected_count, deleted_count).

        A JPG-bearing key is skipped (default HARD INVARIANT: never trash a
        JPG-bearing picture) UNLESS it is in `allow_jpg_keys` — the deliberate
        trip-culling carve-out (a JPG not published in a blog may be culled). The
        default empty set keeps every caller strict. Both day CSVs are written once
        at the end. Shared by delete_by_color (colour cull) and the right-click
        Delete (arbitrary selection).

        `progress_cb(done, total)`, if given, is called after every key (moved,
        skipped, or protected) — a slow move (network drive, big batch) gets a
        wait dialog with a real cancel point. Returning False from it stops the
        loop early; work already moved stays moved (recoverable via .trash)."""
        import shutil
        if self.read_only:
            logger.warning("delete_keys refused: collection is read-only.")
            return 0, 0
        protected = 0
        deleted = 0
        srcs = []
        total = len(keys)
        for i, key in enumerate(keys):
            if key not in self.fpr.upn.UniquePictureName_dict:
                if progress_cb and progress_cb(i + 1, total) is False:
                    break
                continue
            if self.jpg_path(key) and key not in allow_jpg_keys:
                protected += 1
                if progress_cb and progress_cb(i + 1, total) is False:
                    break
                continue
            upn_dir = trash_root / key
            upn_dir.mkdir(parents=True, exist_ok=True)
            raw_src = None
            for fname in self._files(key):
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
            sweep_dop(raw_src, upn_dir)
            self.fpr.upn.UniquePictureName_dict.pop(key, None)
            deleted += 1
            if progress_cb and progress_cb(i + 1, total) is False:
                break
        from utils.preview_cache import drop_for_files
        drop_for_files(srcs)     # the RAWs are gone; their cached previews are junk
        if deleted:
            try:
                self.fpr.upn.write_project_summary()
                self.fpr.fi.write_project_files()
            except Exception as e:
                logger.error(f"delete_keys: CSV write failed: {e}")
        return protected, deleted

    def delete_by_color(self, color: str, trash_root: Path,
                        allow_jpg_keys: frozenset = frozenset(),
                        progress_cb=None) -> tuple[int, int, int]:
        """Move all UPNs with the given color label to trash.

        Returns (total_matching, protected_count, deleted_count). Same JPG carve-out
        and `progress_cb` as delete_keys (see it). Caller supplies
        trash_root = <trip_root>/.trash/<day>.
        """
        if self.read_only:
            logger.warning(f"delete_by_color refused ({color}): collection is read-only.")
            return 0, 0, 0
        wanted = color.lower()
        matching = [k for k in self.all_keys()
                    if (self.fpr.getColor(self.rep_filename(k)) or "").lower() == wanted]
        protected, deleted = self.delete_keys(matching, trash_root, allow_jpg_keys, progress_cb)
        return len(matching), protected, deleted

    def trash_upn(self, key: str, trash_dir) -> list | None:
        """Move ONE UPN's files to `trash_dir/<key>/` and drop it from the in-memory
        fpr — WITHOUT writing any CSV (the caller edits the correct per-day CSV;
        this collection's fpr may span many days, so its own writeback would go to
        the wrong file). HARD INVARIANT: never trash a JPG-bearing picture → returns
        None (protected). Returns the moved file names on success (possibly empty if
        the files were already gone). Used by the map's right-click Delete."""
        import shutil
        if self.read_only:
            logger.warning(f"trash_upn refused for {key}: collection is read-only.")
            return None
        if self.jpg_path(key):
            return None                      # protected — the JPG is the finished asset
        dest = Path(trash_dir) / key
        dest.mkdir(parents=True, exist_ok=True)
        moved, raw_src, srcs = [], None, []
        for fname in list(self._files(key)):
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            src = Path(frow.get("WindowsFileName", ""))
            if src.suffix.lower() in _RAW_EXTS:
                raw_src = src
            if src.exists():
                try:
                    shutil.move(str(src), str(dest / src.name))
                    moved.append(src.name)
                    srcs.append(src)
                except Exception as e:
                    logger.error(f"trash_upn move failed for {src.name}: {e}")
            self.fpr.fi.files_dict.pop(fname, None)
        sweep_dop(raw_src, dest)
        self.fpr.upn.UniquePictureName_dict.pop(key, None)
        from utils.preview_cache import drop_for_files
        drop_for_files(srcs)     # the RAW is gone; its cached preview is now junk
        return moved

    # ── write-back (rating / colour) with Rate recompute ──────────────────
    def apply(self, upn_key: str, *, new_stars: int | None = None,
              new_color: str | None = None) -> int | None:
        """Persist rating / colour for one picture, recompute Rate, and write
        both day CSVs. ORF·XMP / JPG pictures get XMP:Rating/Label via exiftool
        on their sidecar/JPG (RAW stays immutable); HEIC/PNG-only pictures get
        the value written INTO the HEIC/PNG itself (the single source LR + DxO +
        our Read Directory share). Day scope only."""
        if self.read_only:
            logger.warning(f"apply refused for {upn_key}: collection is read-only.")
            return None
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
            dxo_raw_exts = bare(DXO_RAW)  # canonical table — see utils/raw_extensions.py
            raw_path = None
            for fname in files:
                frow = self.fpr.fi.files_dict.get(fname)
                if not frow:
                    continue
                ext = frow.get("WindowsExtension", "").lower()
                if ext in dxo_raw_exts and raw_path is None:
                    raw_path = frow.get("WindowsFileName", "")  # for the .dop write
                if ext not in ("xmp", "jpg", "jpeg"):
                    continue
                fpath = Path(frow.get("WindowsFileName", ""))
                args = []
                if new_stars is not None:
                    args.append(f"-XMP:Rating={new_stars}")
                if new_color is not None:
                    args.append(f"-XMP:Label={new_color}")
                # Colour/rating in the .xmp can be suppressed (Sidecar Behaviour)
                # since PhotoLab mistreats it; internal state below still updates.
                suppress = ext == "xmp" and not color_rating_to_xmp_enabled()
                if args and fpath.exists() and not suppress:
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

        new_rate = _compute_rate(upn_row, self.fpr.fi.files_dict)
        upn_row["Rate"] = str(new_rate)

        # The file(s) were written synchronously above (the durable copy); the day
        # CSVs are just a projection of the in-memory rows, so defer them — the
        # host flushes on a short debounce, on focus-out, on close, and before any
        # real refresh. See flush_csv().
        self._csv_dirty_summary = True
        if wrote_file:
            self._csv_dirty_files = True
        import time as _time
        self._self_write_ts = _time.monotonic()
        return new_rate

    def apply_many(self, upn_keys, *, new_stars: int | None = None,
                   new_color: str | None = None, progress=None) -> int:
        """apply() for a whole selection, in ONE exiftool call.

        Measured before writing: a single exiftool_write costs ~270 ms, almost
        all of it spawning the process, while rewriting a day CSV costs 1 ms. So
        rating 80 pictures the old way — one apply() for the stars and another
        for the colour, each spawning exiftool — was ~160 spawns and about 43
        seconds. The CSVs were never the problem.

        exiftool takes any number of files per invocation, and run_exiftool_files
        passes them through an argfile so the command line cannot overflow. One
        spawn does the lot.

        HEIC/PNG-only pictures keep the per-picture path: the value goes INTO the
        image, so they cannot share a single call. There are usually none.

        progress(done, total) is called while the file list is gathered — the
        cheap part; the write that follows is one step.
        """
        if self.read_only:
            logger.warning("apply_many refused: collection is read-only.")
            return 0
        keys = [k for k in upn_keys if self.fpr.upn.UniquePictureName_dict.get(k)]
        if not keys:
            return 0

        dxo_raw_exts = bare(DXO_RAW)  # canonical table — see utils/raw_extensions.py
        batch_paths, touched_rows, raw_paths, heic_keys = [], [], [], []

        for i, key in enumerate(keys):
            if progress:
                progress(i, len(keys))
            upn_row = self.fpr.upn.UniquePictureName_dict[key]
            if new_stars is not None:
                upn_row["Rating"] = str(new_stars)
            if new_color is not None:
                upn_row["Label"] = new_color
            if self._heic_only_upn(key):
                heic_keys.append(key)
                continue
            for fname in (upn_row.get("Files") or []):
                frow = self.fpr.fi.files_dict.get(fname)
                if not frow:
                    continue
                ext = frow.get("WindowsExtension", "").lower()
                if ext in dxo_raw_exts:
                    raw_paths.append(frow.get("WindowsFileName", ""))
                    continue
                if ext not in ("xmp", "jpg", "jpeg"):
                    continue
                if ext == "xmp" and not color_rating_to_xmp_enabled():
                    continue          # suppressed in .xmp; internal state still set
                fpath = Path(frow.get("WindowsFileName", ""))
                if fpath.exists():
                    batch_paths.append(str(fpath))
                touched_rows.append(frow)

        args = []
        if new_stars is not None:
            args.append(f"-XMP:Rating={new_stars}")
        if new_color is not None:
            args.append(f"-XMP:Label={new_color}")

        wrote = False
        if args and batch_paths:
            from engine.exif_writer import run_exiftool_files
            try:
                res = run_exiftool_files(args + ["-overwrite_original"], batch_paths)
                wrote = True
                if getattr(res, "returncode", 0):
                    logger.warning(
                        f"apply_many: exiftool reported a problem over "
                        f"{len(batch_paths)} file(s): "
                        f"{(res.stderr or '').strip()[:300]}")
            except Exception as e:
                logger.error(f"apply_many: exiftool write failed: {e}")

        # Internal rows only after the write was attempted, so the cached state
        # and the files agree.
        for frow in touched_rows:
            if new_stars is not None:
                frow["XMP:Rating"] = str(new_stars)
            if new_color is not None:
                frow["XMP:Label"] = new_color

        if color_rating_to_dop_enabled():
            for rp in raw_paths:
                if rp:
                    try:
                        push_metadata_to_dop(rp, color=new_color, rating=new_stars)
                    except Exception as e:
                        logger.warning(f"apply_many: .dop write failed for {rp}: {e}")

        # HEIC-only ones go the single-picture route, CSVs and all.
        for key in heic_keys:
            self.apply(key, new_stars=new_stars, new_color=new_color)

        for key in keys:
            upn_row = self.fpr.upn.UniquePictureName_dict[key]
            upn_row["Rate"] = str(_compute_rate(upn_row, self.fpr.fi.files_dict))

        # A bulk edit is a deliberate one-shot — persist now (and this write
        # covers any single-edit dirt buffered earlier, so clear those flags).
        try:
            self.fpr.upn.write_project_summary()
            if wrote:
                self.fpr.fi.write_project_files()
            self._csv_dirty_summary = self._csv_dirty_files = False
        except Exception as e:
            logger.error(f"apply_many: failed to persist CSV(s): {e}")
        import time as _time
        self._self_write_ts = _time.monotonic()
        if progress:
            progress(len(keys), len(keys))
        return len(keys)

    def _heic_only_upn(self, upn_key: str) -> bool:
        """True when the picture has no JPG and no RAW — i.e. HEIC-only /
        PNG-only. Those carry rating/colour in the HEIC/PNG itself; everything
        else (RAW owns a Lightroom XMP, or has a JPG) keeps the exiftool path."""
        raws = bare(RAW)  # canonical table — see utils/raw_extensions.py
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
        # Prefer the species name in the host's chosen language profile (built from
        # the day CSV, keyed by path). Falls back to the parent folder, which under
        # biota/…/<scientific species>/<file> IS the scientific name — all a bare
        # path can give.
        hint = self._c._name_hints.get(self._key)
        if hint:
            # A landscape / context photo (topics/ leaf) is titled by its Headline,
            # not the leaf ("2025") — matching how the other viewers show a blog extra.
            if self.is_blog_extra:
                hl = (hint.get("Headline") or "").strip()
                if hl:
                    return hl
            from utils.name_profile import name_for
            n = name_for(hint, self._c._name_profile)
            if n:
                return n
        p = Path(self._key)
        return p.parent.name or p.name

    @property
    def key_label(self) -> str:
        # The card's small key line: filename only. The full path is long enough to
        # blow out the grid column, and it's already in the card's tooltip.
        return Path(self._key).name

    @property
    def species(self) -> str:
        return ""

    @property
    def headline(self) -> str:
        # Carried from the master picture row via the name hint (this viewer does no
        # per-file exiftool read), so a committed landscape shows its stored Headline.
        hint = self._c._name_hints.get(self._key)
        return (hint.get("Headline") or "").strip() if hint else ""

    @property
    def is_blog_extra(self) -> bool:
        hint = self._c._name_hints.get(self._key)
        if not hint:
            return False
        from engine.speciesclassification import SpeciesClassification
        return SpeciesClassification.isBlogExtra(hint.get("subject", ""))

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

    @property
    def has_crop(self) -> bool:
        return self._c.crop_present(self._key)

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
        from gui.thumb_cache import ThumbCache
        self._thumb_cache = ThumbCache()
        self._order: list[str] = [str(p) for p in self._paths]
        # {key (=str path) -> has a crop}, precomputed by the host from the day CSV
        # sizes: a flat path list carries no RAW-vs-JPG dimensions to judge it from.
        self._crop_hints: dict[str, bool] = {}
        # {key (=str path) -> {NLSoortNaam, EngSoortNaam, WetSoortNaam}} from the
        # day CSV, so the caption can honour the name-language profile instead of
        # falling back to the scientific folder name. Empty → folder name.
        self._name_hints: dict[str, dict] = {}
        self._name_profile: str = ""

    def set_crop_hints(self, hints: dict) -> None:
        self._crop_hints = hints or {}

    def set_name_hints(self, hints: dict, profile: str) -> None:
        self._name_hints = hints or {}
        self._name_profile = profile or ""

    def crop_present(self, key: str) -> bool:
        return bool(self._crop_hints.get(key, False))

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
        from gui.thumb_cache import cached_thumb_request
        # A static file list — no per-key image signature to track.
        src = Path(key)
        cached_thumb_request(self._thumb_cache, self._loader, key,
                             target_w, None, produce, on_ready,
                             disk_dir=self._cache_dir_for(src), stem=src.stem)
