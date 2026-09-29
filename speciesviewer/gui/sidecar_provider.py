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
import re
import time
from pathlib import Path

from PyQt6.QtGui import QImage

from gui.picture_provider import (PictureItem, PictureCollection, ExifSummary,
                                  PixmapCallback, sweep_dop, apply_crop, crop_for,
                                  exposure_for, jpg_is_crop_by_size,
                                  verify_crop_written)
from gui.image_loader import AsyncImageLoader
from gui.master_ops import MasterCardOpsMixin
from gui.preview_provider import cached_jpg_for, preview_cache_dir_for
from engine.exif_writer import exiftool_write
from engine.sidecarlib import push_metadata_to_dop, read_dop_metadata
from utils.sidecar_policy import (
    color_rating_to_xmp_enabled, color_rating_to_dop_enabled,
)
from engine.handling import deriveUniquePictureName
from utils.raw_extensions import (RAW, JPEG, OTHER_IMAGE, RAW_OR_HEIC,
                                  SELF_DESCRIBING)

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = RAW  # canonical table — see utils/raw_extensions.py
# The undeveloped ORIGINAL of a picture — what goes in the group's "raw" slot.
# A HEIC is one: it is the capture, its metadata lives in a .xmp beside it, and
# PhotoLab develops it into a .jpg exactly as it does an ORF. So it groups like a
# RAW, NOT like a JPG — put it in the "jpg" slot instead and it would count as a
# finished render in the JPG-only overlay and inherit the "a JPG is the finished
# asset" delete protection, both of which would be lies about an undeveloped file.
# Deliberately NOT the same set as _RAW_EXTS: crop-to-.dop (request_image) stays
# RAW-only, because a HEIC has no develop step for a .dop to describe and SV must
# never invent one.
# The ORIGINAL a picture is made from — what goes in the group's "raw" slot.
# RAW_OR_HEIC plus PNG, which is self-describing and, like a HEIC, a SOURCE you
# bake a website JPG from rather than a finished render. Putting PNG in the "jpg"
# slot instead (it needs no develop step to be DISPLAYED, so that looked right)
# made it count as a finished JPG: the JPG-only overlay listed it, the "a JPG is
# the finished asset" delete protection covered it, and Create JPG refused it as
# already having a JPG. Displayable is not the same as web-ready — his words:
# shown as if it were a JPG, which does not make it suitable for websites.
_SOURCE_EXTS = RAW_OR_HEIC | SELF_DESCRIBING  # canonical table — see utils/raw_extensions.py
# Where a picture's annotations live when they are NOT in a sidecar — the whole
# rationale is on the set itself in the canonical table.
_SELF_DESCRIBING = SELF_DESCRIBING  # canonical table — see utils/raw_extensions.py
# What a card can show WITHOUT a develop step — the group's "jpg". PNG belongs
# here, with TIFF: Qt decodes them and a phone screenshot is a finished picture
# exactly as a JPEG is, so leaving them out only meant they vanished. HEIC does
# NOT belong here, however finished it is: Qt cannot decode HEIC at all
# (QImageReader.supportedImageFormats() has no entry for it), so a HEIC card
# would be a permanently grey tile. See CARD_EXTENSIONS on SidecarCollection.
# A FINISHED render, web-ready as it stands: a JPEG, or a TIFF (which
# OTHER_IMAGE calls a rendered format that is never a camera original). PNG is
# deliberately not here — see _SOURCE_EXTS.
_JPG_EXTS = JPEG | (OTHER_IMAGE - SELF_DESCRIBING)  # canonical table — see utils/raw_extensions.py

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


# exiftool without -n prints GPS as "16 deg 31' 3.46\" S" (the sidecar cache
# reads it that way). Parse it back to a signed decimal so the GPS map can be
# built from the rows the cache ALREADY read — no second exiftool pass.
_DMS_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*deg\s*(\d+(?:\.\d+)?)'\s*(\d+(?:\.\d+)?)\"?\s*([NSEW])?",
    re.IGNORECASE)


def _dms_to_dd(token) -> float | None:
    s = str(token or "").strip()
    if not s:
        return None
    m = _DMS_RE.search(s)
    if m:
        deg, minutes, sec, hemi = m.groups()
        deg_f = float(deg)
        dd = abs(deg_f) + float(minutes) / 60.0 + float(sec) / 3600.0
        h = (hemi or "").upper()
        if h in ("S", "W") or (not h and deg_f < 0):
            dd = -dd
        return dd
    try:                       # already a bare decimal ("-16.5176")
        return float(s)
    except ValueError:
        return None


def _gps_from_row(row) -> tuple | None:
    """(lat, lon) from a cached sidecar row's GPS strings, or None."""
    if not row:
        return None
    pos = str(row.get("GPSPosition") or "").strip()
    if pos and "," in pos:
        lat_s, lon_s = (t.strip() for t in pos.split(",", 1))
    else:
        lat_s, lon_s = row.get("GPSLatitude"), row.get("GPSLongitude")
    lat, lon = _dms_to_dd(lat_s), _dms_to_dd(lon_s)
    return (lat, lon) if (lat is not None and lon is not None) else None


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
    can_edit_headline = True
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
        # Same carve-out as crop — a suggestion written to the RAW's sidecars,
        # not a pixel edit this app develops itself.
        self.can_set_exposure = self.can_set_crop
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
            self.can_edit_headline = edit
            self.can_edit_species = False

    @property
    def _grp(self) -> dict:
        return self._c._groups.get(self._stem, {})

    @property
    def _row(self) -> dict:
        return self._c._rows.get(_norm(self._grp.get("meta", "")), {})

    # identity
    @property
    def key(self) -> str:
        return self._stem

    def _profile_lines(self) -> list:
        """The group's names as (text, is_sci) lines in the display profile —
        from the day-CSV hint (album/virttrip) or resolved locally from the
        sidecar Subject (culling). [] when no name is known."""
        from utils.name_profile import name_lines, effective_profile
        t = self.name_triple
        row = {"nl": t.get("nl", ""), "en": t.get("en", ""), "sci": t.get("sci", "")}
        prof = getattr(self._c, "_name_profile", "") or effective_profile()
        return name_lines(row, prof)

    @property
    def name_triple(self) -> dict:
        # Album/virttrip day viewers carry the triple on the day-CSV hint row; a
        # culling picture has only the sidecar Subject, resolved locally to its
        # full NL/EN/Sci triple through the species collection (no network).
        hint = self._c._hint_row_for_stem(self._stem)
        if hint:
            return {"nl": (hint.get("NLSoortNaam") or hint.get("nl") or "").strip(),
                    "en": (hint.get("EngSoortNaam") or hint.get("en") or "").strip(),
                    "sci": (hint.get("WetSoortNaam") or hint.get("sci") or "").strip()}
        return self._c._triple_from_subject(self.species) or {}

    @property
    def display_name(self) -> str:
        # A landscape / context "blog extra" is titled by its Headline (what the
        # website shows) rather than a species name; a topic-filed landscape now
        # carries a synthetic species name (the year leaf, e.g. "2026"), so check
        # this BEFORE the species-name hint.
        if self.is_blog_extra and self.headline:
            return self.headline
        lines = self._profile_lines()
        if lines:
            return lines[0][0]
        # No resolvable name (unhinted render / unknown subject): sidecar subject, then stem.
        from gui.culling_species import split_species_qualifiers
        name, _ = split_species_qualifiers(self.species)
        return name or self._stem

    @property
    def display_lines(self) -> list:
        # Follow the display profile: the group's NL/EN/Sci names (deduped), from
        # the day-CSV hint or resolved locally from the Subject, sex/age suffix on
        # the primary line. Falls back to the ABC default only when no name at all.
        if self.is_blog_extra and self.headline:
            return [(self.headline, False)]
        lines = self._profile_lines()
        if not lines:
            return super().display_lines
        suffix = self.qualifier_suffix or ""
        if suffix:
            t, s = lines[0]
            lines = [(t + suffix, s)] + lines[1:]
        return lines

    @property
    def qualifier_suffix(self) -> str:
        from gui.culling_species import qualifier_suffix_from_subject
        return qualifier_suffix_from_subject(self.species)

    @property
    def sci_name(self) -> str:
        # From the day-CSV hint (album/virttrip) or resolved locally from the
        # sidecar Subject (culling), so the large viewer shows the scientific
        # name under the common name.
        return self._c._sci_for_stem(self._stem, self.species)

    @property
    def from_master(self) -> bool:
        # A read-only sidecar viewer is the album/virtual-trip DAY viewer, whose
        # pictures ARE the master's — so their sex/age/plumage form is editable
        # here the same way it is in the Master Species Viewer. (Normal off-card
        # culling is not read-only and its pictures are not yet in the master.)
        return self._c.readonly

    @property
    def has_pending_render(self) -> bool:
        """True when a JPG render sits on disk beside this master picture that the
        master hasn't registered yet — drives the amber ➕ 'new render' card badge.
        Always False on a plain culling/trip day (nothing bound to register into)."""
        return bool(self._c.pending_render_for(self._stem))

    @property
    def pending_render_names(self) -> list:
        return self._c.pending_render_for(self._stem)

    # read fields
    @property
    def species(self) -> str:
        return _subject_to_text(self._row.get("Subject"))

    @property
    def headline(self) -> str:
        return str(self._row.get("Headline", "") or "")

    @property
    def is_blog_extra(self) -> bool:
        from engine.speciesclassification import SpeciesClassification
        return SpeciesClassification.isBlogExtra(self._row.get("Subject", ""))

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
        # [:19] because a sidecar written since 2026-08-17 carries the camera's
        # UTC offset ("2026:08:14 10:34:01+01:00" — see
        # sv0ReadSDCard._write_sidecars). This is the DISPLAY value; showing the
        # zone here would just make the card's date line longer and ragged
        # against older days. Whoever needs the offset reads it from the sidecar
        # itself (engine.geo.camera_offset_from_dates).
        return str(self._row.get("DateTimeOriginal", "") or "")[:19]

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
    def crop_rect(self) -> dict | None:
        # Same cached read as has_crop — None for the size-cue case (a baked
        # render keeps no rectangle).
        return self._c._crop_for(self._stem)

    @property
    def place_fields(self) -> dict:
        r = self._row
        base = {k: str(r.get(k, "") or "")
                for k in ("Location", "City", "Municipality", "State")}
        if any(base.values()):
            return base
        # JPG-only picture: no .xmp row, so read the name folded in from the JPG.
        return dict(self._c._place_by_stem.get(self._stem, {})) or base

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

    @property
    def exposure(self) -> float:
        return self._c._exposure_for(self._stem) or 0.0

    def set_exposure(self, value: float) -> None:
        """Write the suggested exposure (EV) to this picture's sidecars (see
        collection)."""
        return self._c.set_exposure(self._stem, value)

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

    def set_headline(self, value: str) -> None:
        self._c.write_tag(self._stem, "XMP:Headline", value, "Headline")

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

    # Exactly the extensions __init__ can turn into a card: anything else
    # contributes no "raw" and no "jpg" to its group, and the group is then
    # dropped as imageless. Published so a CALLER can ask instead of keeping its
    # own list — Read SD-cards filtered by the much wider PICTURE table and
    # handed over 279 HEICs plus 7 PNGs that were discarded here in silence,
    # reporting "460 copied" and then showing 49. A caller that filters by this
    # set can still discard files, but it now knows which, and can say so.
    CARD_EXTENSIONS = frozenset(_SOURCE_EXTS | _JPG_EXTS)

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
        # A colour / rating edit that does not reach disk is otherwise silent —
        # the card repaints optimistically and the value is lost on close. These
        # record every write that failed; the host dialog sets `on_write_error`
        # to a callback(msg) so the user is told the moment it happens.
        self._write_failures: list[dict] = []
        self.on_write_error = None
        # Master inventory binding (album day viewer only). Empty until bind_master();
        # the shared MasterCardOpsMixin reads these to gate + drive pin/move/delete/add
        # against the master DB. Keyed by UPN, exactly like _groups
        # (deriveUniquePictureName), so a card's key resolves straight through.
        self._master_db = None
        self._master_root = None
        self._master_keys: set = set()
        self._master_upn: dict = {}
        self._master_files: dict = {}
        # {upn: [render filenames]} for master pictures with a JPG render on disk
        # the master doesn't track yet — filled by pending_renders() on a focus-in
        # scan, read by SidecarItem.has_pending_render for the amber ➕ card badge.
        self._pending_renders: dict[str, list] = {}
        self._cache_root = Path(cache_root) if cache_root else None
        self._loader = loader or AsyncImageLoader()
        from gui.thumb_cache import ThumbCache
        self._thumb_cache = ThumbCache()   # shared with the large-viewer filmstrip
        # Group input picture files by their unique-picture name. Using
        # deriveUniquePictureName (not the raw stem) folds DxO/PureRaw renders
        # and -small thumbnails onto their source RAW — otherwise e.g.
        # N2040705_DxO.jpg becomes its OWN picture, splitting one shot into two
        # and inflating the count. (At pure ingest there are no such suffixes,
        # so this is a no-op there.)
        # Phase timing for the "8 s before the grid shows" investigation — one
        # summary line at the end of __init__.
        _phase: list[tuple[str, float]] = []
        _t = time.perf_counter()

        def _mark(label: str):
            nonlocal _t
            now = time.perf_counter()
            _phase.append((label, (now - _t) * 1000))
            _t = now

        self._groups: dict[str, dict] = {}
        for p in paths:
            p = Path(p)
            ext = p.suffix.lower()
            grp = self._groups.setdefault(deriveUniquePictureName(p.name), {})
            if ext in _SOURCE_EXTS:
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
        # "meta" = the file this picture's ANNOTATIONS live in, which is not always
        # a sidecar (see _SELF_DESCRIBING). Everything that reads or writes a
        # rating, colour, species, caption or place goes through this; everything
        # that means the SIDECAR FILE itself — delete/trash, crop, exposure, the
        # .dop recipe — keeps using "xmp", because a HEIC is not a sidecar and must
        # not be trashed as one.
        #
        # For a self-describing file the file WINS over any .xmp beside it. That is
        # the whole point: a stray sidecar (ingest briefly wrote one for every HEIC
        # on 2026-09-23) must not shadow what PhotoLab and Lightroom read and write
        # inside the file. Such sidecars are simply ignored from here on.
        for g in self._groups.values():
            own = g.get("raw") or g.get("jpg")
            if own is not None and Path(own).suffix.lower() in _SELF_DESCRIBING:
                g["meta"] = own
            elif g.get("xmp"):
                g["meta"] = g["xmp"]
        _mark("group")
        self._crop_cache: dict = {}      # stem -> normalised crop | None (cleared on refresh)
        self._exposure_cache: dict = {}  # stem -> EV float | None (cleared on refresh)
        self._jpg_dims_cache: dict = {}  # jpg path -> "WxH" (header read, for the crop size cue)
        self._created_blog_jpgs: set = set()  # stems whose <stem>.jpg WE baked (safe to overwrite)
        # {file path -> {NLSoortNaam,...}} + profile, so the caption follows the
        # name-language profile from the day CSV — and shows a name even when the
        # sidecar subject is empty (a DxO render whose .xmp didn't pair by stem).
        self._name_hints: dict = {}
        self._name_profile: str = ""
        # Scientific-name resolution for the identity header: lazily-loaded
        # SpeciesClassification (from the trip/master species collection) +
        # a common-name→scientific cache, so a culling picture whose only name
        # is the sidecar Subject still shows the Latin name.
        self._sci_by_name: dict[str, str] = {}
        self._triple_by_name: dict[str, dict] = {}
        self._sc = None
        self._sc_loaded = False
        self._image_changed: set = set() # stems whose IMAGE changed at last refresh()
        self._attach_sibling_jpgs()
        _mark("attach_sibling_jpgs")
        # Baseline image signatures (source file + mtime + .dop mtime) so refresh()
        # can tell a re-crop / new render (re-decode needed) from a text-only edit.
        self._image_sigs: dict = {s: self._image_sig(s) for s in self._groups}
        _mark("image_sigs")
        _stats: dict = {}
        self._rows, self._caches = _read_sidecar_rows(
            [g["meta"] for g in self._groups.values() if g.get("meta")],
            quiet_stats=_stats)
        _mark("read_sidecar_rows" + (f" [{_stats.get('cached', '?')} cached, "
                                     f"{_stats.get('stale', '?')} via exiftool]"
                                     if _stats else ""))
        # Colour/rating are stored in the DxO .dop (the write channel; the .xmp is
        # off by default), so seed the display rows from there — else a colour set
        # last session would silently read back as the sidecar's stale value.
        self._dop_mtimes: dict = {}   # stem -> last-seen .dop mtime_ns (overlay gate)
        self._overlay_dop_color_rating()
        _mark("overlay_dop_color_rating")
        # Batch-read GPS from each picture's best source (XMP if present, else
        # RAW). At the culling stage GPS lives in the RAW's EXIF, not the XMP
        # sidecar, so the sidecar cache alone can't populate the GPS badge. One
        # exiftool call covers all files; we keep the coordinates (not just
        # presence) so the card's location line can show them with no extra I/O.
        self._gps_by_stem: dict[str, tuple] = self._build_gps_map()
        _mark("build_gps_map")
        # Place names for stems whose .xmp row has none — chiefly JPG-only pictures,
        # whose location lives in the JPG itself, not an .xmp sidecar. Without this the
        # card's location line falls back to raw coordinates for them.
        self._place_by_stem: dict[str, dict] = self._build_place_map()
        _mark("build_place_map")
        self.rebuild()
        _mark("rebuild")
        total = sum(ms for _, ms in _phase)
        logger.info("SidecarCollection built: %d pictures in %.2f s  ·  %s",
                    len(self._groups), total / 1000,
                    ", ".join(f"{lbl} {ms:.0f}ms" for lbl, ms in _phase if ms >= 1))

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
        """{stem: (lat, lon)} for every stem with GPS.

        Fast path: `_read_sidecar_rows` just read every `.xmp` WITH the GPS tags
        into `self._rows`, so parse those — no second exiftool pass over the
        same files (that pass was the whole "8 s before the grid shows", and on
        a day with no GPS it read ~1800 files to learn nothing). Only groups
        with NO `.xmp` at all (a JPG-only DxO render) fall back to a batched
        exiftool read of the jpg/raw — the same source the old code used for
        them, and normally a handful.
        """
        out: dict[str, tuple] = {}
        need_read: dict[str, Path] = {}
        for s, g in self._groups.items():
            xmp = g.get("meta")
            if xmp:
                ll = _gps_from_row(self._rows.get(_norm(xmp)))
                if ll:
                    out[s] = ll
                # xmp present, no GPS in it → "no GPS" at the culling stage,
                # exactly what the old exiftool read of that same .xmp returned.
            else:
                src = g.get("jpg") or g.get("raw")
                if src:
                    need_read[s] = Path(src)
        if need_read:
            from gui.geolocate import batch_gps
            res = batch_gps([str(p) for p in need_read.values()])
            for s, p in need_read.items():
                ll = res.get(_norm(p))
                if ll:
                    out[s] = ll
        return out

    _PLACE_FALLBACK_KEYS = ("Location", "City", "Municipality", "State")

    def _build_place_map(self) -> dict:
        """{stem: {Location,City,Municipality,State}} read from the best source for the
        stems whose .xmp row carries NO place text — chiefly JPG-only pictures (their
        location lives in the JPG, not an .xmp). One batched exiftool call, and only for
        the stems that need it, so a fully-sidecar'd album pays nothing. This keeps the
        card's location line off the raw-coordinate fallback when a name is on disk.

        Skips stems with no coordinate: the location line pairs a place name WITH
        the coords, so a name on a GPS-less picture never shows anyway — and a
        fresh un-geocoded day is otherwise ~1800 fruitless RAW reads."""
        from gui.geolocate import read_location_fields_batch
        need = {}
        for s, g in self._groups.items():
            if s not in self._gps_by_stem:
                continue
            xmp = g.get("meta")
            row = self._rows.get(_norm(xmp), {}) if xmp else {}
            if not any(str(row.get(k, "") or "").strip() for k in self._PLACE_FALLBACK_KEYS):
                src = g.get("jpg") or g.get("raw") or xmp
                if src:
                    need[s] = src
        if not need:
            return {}
        read = read_location_fields_batch([str(p) for p in need.values()])
        out = {}
        for s, p in need.items():
            d = read.get(os.path.normcase(os.path.normpath(str(p))), {})
            loc = {k: (d.get(k, "") or "") for k in self._PLACE_FALLBACK_KEYS}
            if any(loc.values()):
                out[s] = loc
        return out

    # ── ordering ──────────────────────────────────────────────────────────
    def rebuild(self, keys: list[str] | None = None) -> None:
        stems = keys if keys is not None else list(self._groups.keys())
        self._order = sorted(stems, key=self._capture_key)

    def _capture_key(self, stem: str):
        row = self._rows.get(_norm(self._groups.get(stem, {}).get("meta", "")), {})
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
        xmp = self._groups.get(stem, {}).get("meta")
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
        return self._groups.get(key, {}).get("meta") or self._source_file(key)

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
        return [g[k] for k in ("meta", "jpg") if g.get(k)]

    def gps_is_derived(self, key: str) -> bool:
        """True when a HUMAN placed this coordinate (map drag / copy-paste-GPS /
        place-name lookup) rather than the camera recording it — the only kind we
        allow moving. Read from GPSProcessingMethod, never inferred from precision."""
        from gui.geolocate import GPS_MANUAL
        if key in getattr(self, "_pasted_gps", ()):
            return True
        g = self._groups.get(key, {})
        xmp = g.get("meta")
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
        xmp = g.get("meta")
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
        xmp = g.get("meta")
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
            xmp = g.get("meta")
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
        labels_before = {k: r.get("Label") for k, r in self._rows.items() if r.get("Label")}
        self._rows, self._caches = _read_sidecar_rows(
            [g["meta"] for g in self._groups.values() if g.get("meta")])
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
        self._exposure_cache.clear()     # ditto for an exposure change made externally
        # A render (PhotoLab _DxO.jpg, LR/v5 export) may have appeared — fold it in
        # (switches the display source RAW→JPG, caught by the image signature too).
        self._attach_sibling_jpgs()
        # Rebuild the JPG-only place-name fallback — a "Get GPS from location" or an
        # external edit may have written a name into a JPG that has no .xmp row.
        self._place_by_stem = self._build_place_map()

        self._warn_lost_labels(labels_before)
        text_after = self._row_snapshot()
        text_changed = {s for s in text_after if text_after[s] != text_before.get(s)}
        # Image change is vs the STORED signature from the last refresh/build — an
        # external re-crop only touches the .dop (not self._rows), so a within-call
        # before/after would miss it (the rows re-read doesn't move image mtimes).
        new_sigs = {s: self._image_sig(s) for s in self._groups}
        self._image_changed = {s for s in new_sigs if new_sigs[s] != self._image_sigs.get(s)}
        self._image_sigs = new_sigs
        for s in self._image_changed:
            self._drop_thumb_caches(s)
        return text_changed | self._image_changed

    def _warn_lost_labels(self, labels_before):
        """Say so when a refresh finds a colour GONE from a sidecar that had one.

        Here the sidecar is the only store (a Day has no CSV), so there is nothing
        to keep the colour in — but the loss must not pass silently: an absent
        label is what a flattened sidecar looks like (PhotoLab writing its
        database over our .xmp, 2026-09-25), and that evening it went unnoticed.
        A colour cleared in SV itself comes through the write path, so its row is
        already empty before the refresh and does not show up here."""
        for k, was in labels_before.items():
            row = self._rows.get(k)
            if row is not None and not row.get("Label"):
                logger.warning(f"[colour] refresh: {Path(str(row.get('SourceFile', k))).name} "
                               f"lost its colour {was!r} on disk (changed outside SV)")

    def _drop_thumb_caches(self, stem):
        """Forget every cached thumbnail (RAM + on-disk) for one picture."""
        self._thumb_cache.invalidate(stem)
        src = self._source_file(stem)
        raw = self._groups.get(stem, {}).get("raw")
        from gui.thumb_cache import drop_disk_thumbs
        seen = set()
        for p in (src, raw):
            if p:
                p = Path(p)
                if p.stem not in seen:
                    seen.add(p.stem)
                    drop_disk_thumbs(self._cache_dir_for(p), p.stem)

    def sidecar_paths(self) -> list:
        """Files to watch for external edits: each .xmp (colour/rating/location)
        AND each .dop (the crop). Watching the .dop too catches an in-place re-crop
        that doesn't go through a temp+rename (which the folder watch would miss)."""
        out = []
        for g in self._groups.values():
            # "meta", not "xmp": for a HEIC the file Lightroom/PhotoLab edit IS
            # the HEIC, so watching only sidecars would miss every external edit
            # to one.
            if g.get("meta"):
                out.append(str(g["meta"]))
            raw = g.get("raw")
            if raw:
                dop = Path(str(raw) + ".dop")
                if dop.exists():
                    out.append(str(dop))
        return out

    def note_location_written(self, key: str, fields: dict, protect=()) -> None:
        """An Edit/Get-location write just put these place fields into the key's
        XMP — patch the in-memory row + the on-disk cache so the card's location
        line updates with no re-read (the write already changed the file).
        `protect` names fields the file kept a pre-existing value for (a coarse
        reverse-geocode must not overwrite a hand-entered Location), so the cache
        must keep the row's existing value rather than adopt the proposed one."""
        g = self._groups.get(key, {})
        xmp = g.get("meta")
        protect = set(protect)
        if not xmp:
            # JPG-only picture: no .xmp row to patch — keep the card's location line
            # fresh via the JPG place-name fallback instead (same protect semantics).
            cur = self._place_by_stem.get(key, {})
            merged = dict(cur)
            for k, v in fields.items():
                if v and not (k in protect and (cur.get(k) or "").strip()):
                    merged[k] = v
            if merged:
                self._place_by_stem[key] = merged
            return
        xmp = Path(xmp)
        row = self._rows.setdefault(_norm(xmp), {"SourceFile": str(xmp)})
        folder = xmp.parent
        if folder not in self._caches:
            from engine.sidecar_cache import SidecarCache
            self._caches[folder] = SidecarCache(folder)
        for k, v in fields.items():
            if not v:
                continue
            if k in protect and (row.get(k) or "").strip():
                continue          # file kept its existing value → so does the cache
            row[k] = v
            self._caches[folder].put_field(xmp, k, v)
        self._caches[folder].save()

    # ── image + write helpers ───────────────────────────────────────────────
    def _source_file(self, stem: str):
        """Best display source: JPG (fast) if present, else the RAW (embedded
        preview). Always a Path (or None) — callers do `.parent` on it."""
        g = self._groups.get(stem, {})
        src = g.get("jpg") or g.get("raw")
        return Path(src) if src else None

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

    def create_blog_jpg(self, key: str) -> dict:
        """Write a website-ready JPG next to the RAW from the camera's embedded
        preview + the sidecar crop (NO scaling) — a quick trip-blog picture
        without the full DxO render. `<stem>.jpg` folds onto the RAW's UPN
        (deriveUniquePictureName), so Read & Summarize picks it up as the
        picture's JPG.

        Returns {'status': 'created'|'skipped'|'no_raw'|'failed', ...}. Skips a
        picture that already has a tracked JPG we did NOT bake (a camera JPEG in
        RAW+JPEG mode, or a DxO render) so nothing is clobbered."""
        g = self._groups.get(key, {})
        raw = g.get("raw")
        if not raw or not Path(raw).exists():
            # Its only file IS a JPG: nothing to bake, and saying "no RAW"
            # about it was true and useless. Never an in-place resize — a derived
            # file must not overwrite its source, and <stem>.jpg IS the source here
            # (Windows ignores the case difference).
            if g.get("jpg"):
                return {"status": "already_jpg", "jpg": str(g["jpg"])}
            return {"status": "no_raw"}
        raw = Path(raw)
        out = raw.with_suffix(".jpg")   # <stem>.jpg beside the RAW
        existing = self.jpg_path(key)
        if existing is not None and key not in self._created_blog_jpgs:
            return {"status": "skipped", "jpg": str(existing)}
        # ONE bake for all three collections (gui.preview_extract.bake_blog_jpg):
        # a RAW goes through its embedded preview / LibRaw decode, a HEIC or PNG is
        # opened directly by Pillow and capped after cropping. Each collection used
        # to carry its own copy of this, which is how a HEIC failed in all three
        # with "the RAW could not be decoded" about a file Pillow reads fine.
        from gui.preview_extract import bake_blog_jpg
        crop = self._crop_for(key)
        try:
            baked_size = bake_blog_jpg(raw, out, crop)
        except Exception as e:
            logger.warning(f"Create JPG failed for {raw.name}: {e}")
            return {"status": "failed", "reason": str(e)}
        # Track it as ours + reflect it so the card and a re-run see the new JPG.
        # NOTE: g["jpg"] / g["raw"] are Path objects everywhere else in this
        # collection (_source_file → file_directory → src.parent) — keep it a Path.
        self._created_blog_jpgs.add(key)
        g["jpg"] = out
        self._jpg_dims_cache.pop(str(out), None)
        self._image_sigs[key] = self._image_sig(key)   # display source is now the JPG
        self._drop_thumb_caches(key)
        logger.info(f"Blog JPG created → {out.name} "
                    f"({baked_size[0]}×{baked_size[1]}, "
                    f"{'cropped' if crop else 'full frame'}).")
        return {"status": "created", "jpg": str(out), "size": baked_size,
                "cropped": bool(crop)}

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

    def _lines_for_stem(self, stem: str) -> list:
        """The group's NL/EN/Sci names as (text, is_sci) lines in the display
        profile, from the day-CSV hint. [] when unhinted (pure culling sidecar)."""
        hint = self._hint_row_for_stem(stem)
        if not hint:
            return []
        from utils.name_profile import name_lines
        return name_lines(hint, self._name_profile)

    def _hint_row_for_stem(self, stem: str) -> dict:
        """The raw day-CSV hint row for a group (keyed by any of its file paths),
        so callers can read fields the display name doesn't carry (e.g. the
        scientific name). {} when unhinted."""
        if not self._name_hints:
            return {}
        g = self._groups.get(stem, {})
        for kind in ("jpg", "raw", "xmp"):
            p = g.get(kind)
            if p is not None:
                hint = self._name_hints.get(str(p))
                if hint:
                    return hint
        return {}

    def _sci_for_stem(self, stem: str, subject: str = "") -> str:
        """The scientific name for a group, so the large viewer's identity header
        can show the Latin name under the common one.

        Two sources: the album/virttrip day viewers carry it on the day-CSV hint
        row (WetSoortNaam); a culling picture has only the sidecar Subject (the
        common name typed at the camera stage), which we resolve LOCALLY through
        the trip/master species collection — no network."""
        hint = self._hint_row_for_stem(stem)
        sci = (hint.get("WetSoortNaam") or hint.get("sci") or "").strip()
        if sci:
            return sci
        return self._sci_from_subject(subject)

    def _sci_from_subject(self, subject: str) -> str:
        """Resolve a common (or scientific) name to a scientific name, local-only
        and cached. '' when unknown — the header just hides the line."""
        from gui.culling_species import split_species_qualifiers
        name, _ = split_species_qualifiers(subject or "")
        name = (name or "").strip().lower()
        if not name:
            return ""
        if name in self._sci_by_name:
            return self._sci_by_name[name]
        sci = ""
        sc = self._ensure_species_classification()
        if sc is not None:
            try:
                sci = sc.getKnownSpecies(name, False, LookUpInGBIF=False) or ""
            except Exception as e:
                logger.debug(f"sci-name resolve failed for {name!r}: {e}")
        # Scientific convention "Genus species": the collection stores lower-case,
        # so capitalise the leading genus letter (leave the epithet as-is).
        if sci:
            sci = sci[0].upper() + sci[1:]
        self._sci_by_name[name] = sci
        return sci

    def _triple_from_subject(self, subject: str) -> dict:
        """Resolve a common/scientific name to its full {nl,en,sci} triple, LOCAL-
        only and cached — the same species collection that gives us the scientific
        name also carries the Dutch/English names, so the culling grid can follow
        the NL/EN profile instead of only ever showing the camera-stage name + Latin.
        {} when unknown."""
        from gui.culling_species import split_species_qualifiers
        name, _ = split_species_qualifiers(subject or "")
        name = (name or "").strip().lower()
        if not name:
            return {}
        if name in self._triple_by_name:
            return self._triple_by_name[name]
        triple: dict = {}
        sc = self._ensure_species_classification()
        if sc is not None:
            try:
                sci = sc.getKnownSpecies(name, False, LookUpInGBIF=False) or ""
            except Exception as e:
                logger.debug(f"name-triple resolve failed for {name!r}: {e}")
                sci = ""
            if sci:
                rec = sc.sc_dict.get(sci) or sc.sc_dict.get(sci.lower()) or {}
                triple = {
                    "nl": (rec.get("DutchName") or "").strip(),
                    "en": (rec.get("EnglishName") or "").strip(),
                    # "Genus species": collection stores lower-case, capitalise genus.
                    "sci": (sci[0].upper() + sci[1:]) if sci else "",
                }
        self._triple_by_name[name] = triple
        return triple

    def _ensure_species_classification(self):
        """Load the trip/master species collection once (the same source the
        culling Set-species dialog uses). Cached; None when unavailable."""
        if self._sc_loaded:
            return self._sc
        self._sc_loaded = True
        try:
            from gui.culling_species import load_species_classification
            self._sc = load_species_classification()
        except Exception as e:
            logger.debug(f"could not load species classification for sci names: {e}")
            self._sc = None
        return self._sc

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
        xmp = g.get("meta")
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
        self._drop_thumb_caches(stem)   # a crop can land in the .xmp, which the sig doesn't see
        verify_crop_written(raw, crop, res, who="SidecarCollection")
        return res

    def _exposure_for(self, stem: str):
        """Current suggested exposure (EV) from the PhotoLab .dop (preferred) or
        the Lightroom .xmp, else None. Mirrors _crop_for exactly, including the
        per-stem cache (cleared on refresh())."""
        if stem in self._exposure_cache:
            return self._exposure_cache[stem]
        g = self._groups.get(stem, {})
        exposure = exposure_for(g.get("raw"), g.get("xmp"))
        self._exposure_cache[stem] = exposure
        return exposure

    def set_exposure(self, stem: str, exposure: float):
        """Write a suggested exposure (EV) to the picture's DxO .dop and
        Lightroom .xmp (creating the .xmp if missing) — the exposure mirror of
        set_crop. Invalidates the cached exposure so the next read (the large
        viewer re-opening this picture, or a repeat Set Exposure) sees it.
        Returns the sidecarlib result dict, or None if there's no RAW."""
        raw = self.raw_path(stem)
        if not raw:
            return None
        from engine.sidecarlib import suggest_exposure
        res = suggest_exposure(str(raw), exposure)
        g = self._groups.get(stem, {})
        if res and res.get("xmp_created") and "xmp" not in g:
            xmp = Path(raw).with_suffix(".xmp")
            if xmp.exists():
                g["xmp"] = xmp
        self._exposure_cache.pop(stem, None)
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

    # ── new-render detector (focus-in "Auto-notice new renders") ──────────────
    def _folder_for_stem(self, stem: str) -> str:
        """Normalised on-disk folder of a group's picture — the folder a DxO/LR
        render lands in. Matches the PhotoLab-launch folder the host records, so a
        scan can be scoped to just the folders you actually rendered into."""
        g = self._groups.get(stem, {})
        src = g.get("raw") or g.get("xmp") or g.get("jpg")
        if not src:
            return ""
        return os.path.normcase(os.path.normpath(str(Path(src).parent)))

    def pending_renders(self, only_folders=None) -> dict:
        """{upn: [render filenames]} for master pictures whose species folder holds
        a JPG render the master doesn't track yet — the read-only detector behind the
        amber ➕ badge, sharing the exact logic of the manual "➕ Add new renders"
        scan (db.master_mutate.untracked_renders_for_upns).

        Only meaningful when the album's master inventory is bound and editable;
        returns {} otherwise (a plain culling/trip day has no master to register
        into). `only_folders`, when given, restricts the scan to pictures living in
        those folders — the PhotoLab-launch scoping that keeps a focus-in scan cheap
        and, being additive, merges onto the previously-found set rather than
        replacing it. Result is cached for pending_render_for()."""
        if (not self._master_db or self.master_read_only or not self._master_keys):
            self._pending_renders = {}
            return self._pending_renders
        upns = set(self._master_keys)
        if only_folders is not None:
            wanted = {os.path.normcase(os.path.normpath(str(f))) for f in only_folders}
            upns = {u for u in upns if self._folder_for_stem(u) in wanted}
        if not upns:
            # A scoped scan of nothing leaves earlier findings intact (merge over an
            # empty set); an unscoped scan of nothing means no master pictures at all.
            if only_folders is None:
                self._pending_renders = {}
            return self._pending_renders
        from db.master_mutate import untracked_renders_for_upns
        try:
            hits = untracked_renders_for_upns(self._master_db, self._master_root, upns)
        except Exception as e:
            logger.warning(f"pending-render scan failed: {e}")
            return self._pending_renders
        cache = {} if only_folders is None else dict(self._pending_renders)
        for u in upns:                       # refresh exactly the folders we scanned
            if hits.get(u):
                cache[u] = hits[u]
            else:
                cache.pop(u, None)
        self._pending_renders = cache
        return self._pending_renders

    def pending_render_for(self, key: str) -> list:
        return self._pending_renders.get(key, [])

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
        from gui.thumb_cache import cached_thumb_request
        # Uncropped (crop-tool) requests come with target_w=None, so they take
        # the straight-through branch and are never cached alongside the cropped
        # display pixmap.
        sig = self._image_sigs.get(stem)   # source + mtimes; changes on re-crop / new render
        disk_dir = self._cache_dir_for(Path(src)) if src else None
        stem_name = Path(src).stem if src else None
        cached_thumb_request(self._thumb_cache, self._loader, stem,
                             target_w, sig, produce, on_ready,
                             disk_dir=disk_dir, stem=stem_name)

    def jpg_path(self, key: str):
        g = self._groups.get(key, {})
        jpg = g.get("jpg")
        return Path(jpg) if jpg and Path(jpg).exists() else None

    def rep_full_path(self, key: str) -> Path:
        """Full on-disk path of a representative file for this stem — mirrors
        PreviewCollection.rep_full_path for the pre-pipeline (culling) backend
        (used by the map's day lookup and the "Modified" info line)."""
        grp = self._groups.get(key, {})
        for kind in ("raw", "jpg", "xmp"):
            p = grp.get(kind)
            if p:
                return Path(p)
        return Path(key)

    def trash_upn(self, key: str, trash_dir) -> list | None:
        """Move ONE stem's files to `trash_dir/<key>/` and drop it from the
        in-memory groups. HARD INVARIANT: never trash a JPG-bearing picture ->
        returns None (protected). Returns the moved file names on success
        (possibly empty if the files were already gone). Used by the map's
        right-click Delete — mirrors PreviewCollection.trash_upn for the
        pre-pipeline (culling) backend."""
        import shutil
        if self.read_only:
            logger.warning(f"trash_upn refused for {key}: collection is read-only.")
            return None
        grp = self._groups.get(key)
        if not grp:
            return None
        if grp.get("jpg"):
            return None                      # protected — the JPG is the finished asset
        dest = Path(trash_dir) / key
        dest.mkdir(parents=True, exist_ok=True)
        moved, srcs = [], []
        for kind in ("raw", "xmp"):
            src = grp.get(kind)
            if src and Path(src).exists():
                try:
                    shutil.move(str(src), str(dest / Path(src).name))
                    moved.append(Path(src).name)
                    srcs.append(Path(src))
                except Exception as e:
                    logger.error(f"trash_upn move failed for {Path(src).name}: {e}")
        sweep_dop(grp.get("raw"), dest)
        self._groups.pop(key, None)
        if grp.get("xmp"):
            self._rows.pop(_norm(grp["xmp"]), None)
        from utils.preview_cache import drop_for_files
        drop_for_files(srcs)     # the RAW is gone; its cached preview is now junk
        return moved

    def delete_by_color(self, color: str, trash_root: Path,
                        progress_cb=None) -> tuple[int, int, int]:
        """Move all stems with the given color label to trash.

        Returns (total_matching, protected_count, deleted_count).
        Stems that have a JPG are skipped (HARD INVARIANT).
        Files go to trash_root/<stem>/<filename>.

        `progress_cb(done, total)`, if given, is called after every stem — see
        PreviewCollection.delete_keys for the same contract. Returning False
        stops the loop early; work already moved stays moved."""
        import shutil
        wanted = color.lower()
        matching = [s for s in self.all_keys()
                    if SidecarItem(self, s).color.lower() == wanted]
        protected = 0
        deleted = 0
        srcs = []
        total = len(matching)
        for i, stem in enumerate(matching):
            grp = self._groups.get(stem, {})
            if grp.get("jpg"):
                protected += 1
                if progress_cb and progress_cb(i + 1, total) is False:
                    break
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
            if progress_cb and progress_cb(i + 1, total) is False:
                break
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

    def _mirror_description_to_master(self, upn: str, value: str) -> None:
        """Mirror an album Description edit (the website 'story') into the master DB —
        pictures.Description and the files' XMP:Description. The blog and website read
        the master, NOT the sidecar (same reason as _mirror_rating_to_master and the
        qualifier mirror), so without this a story typed in the album day viewer is
        written to the sidecar but never reaches the published page. No-op in plain
        culling (no master bound)."""
        prow = self._master_upn.get(upn)
        if prow is None:
            return
        prow["Description"] = value
        # Keep the DB's file rows in step with the sidecar write_tag just made, so a
        # later master re-read stays consistent (mirrors _mirror_rating_to_master).
        file_updates: dict = {}
        for fname in (prow.get("Files") or []):
            frow = self._master_files.get(fname)
            if frow and (frow.get("WindowsExtension") or "").lower() in ("xmp", "jpg", "jpeg"):
                frow["XMP:Description"] = value
                file_updates[fname] = {"XMP:Description": value}
        try:
            self._master_db.apply_description(upn, value, file_updates=file_updates)
        except Exception as e:
            logger.warning(f"description master mirror failed for {upn}: {e}")

    def write_tag_many(self, stems, tag: str, value: str, row_key: str) -> int:
        """write_tag for a whole selection, in ONE exiftool call.

        Same measurement that drove the culling grid: an exiftool_write costs
        ~270 ms, nearly all of it spawning the process, so twenty-two pictures
        one at a time is six seconds of process startup. exiftool takes any
        number of files per run and run_exiftool_files passes them through an
        argfile, so the command line cannot overflow however big the selection.

        The per-stem parts that are NOT exiftool — the master mirror, the .dop
        push — stay in a loop; they are cheap. The sidecar cache is written once
        per folder at the end instead of once per picture: it is ~100 KB, and
        re-serialising it twenty-two times to record twenty-two fields is work
        for its own sake.
        """
        stems = [s for s in stems if s]
        if not stems:
            return 0
        is_colorrating = tag in ("XMP:Rating", "XMP:Label")

        # Cheap per-picture channels first, exactly as write_tag does them.
        for stem in stems:
            if is_colorrating:
                if (getattr(self, "_master_db", None) is not None
                        and stem in self._master_upn):
                    self._mirror_rating_to_master(stem, tag, value)
                if color_rating_to_dop_enabled():
                    raw = self.raw_path(stem)
                    if raw:
                        if tag == "XMP:Label":
                            push_metadata_to_dop(str(raw), color=value)
                        else:
                            try:
                                push_metadata_to_dop(str(raw), rating=int(value))
                            except ValueError:
                                pass
                        self._image_sigs[stem] = self._image_sig(stem)
            elif (tag == "XMP:Description"
                    and getattr(self, "_master_db", None) is not None
                    and stem in self._master_upn):
                self._mirror_description_to_master(stem, value)

        gate_off = is_colorrating and not color_rating_to_xmp_enabled()

        # Resolve every annotation target, creating the path for a group that has
        # none. The .xmp colour/rating gate is decided PER FILE, not once for the
        # batch: a selection can mix RAWs (gated — the value lives in their .dop)
        # with HEICs (never gated — the file is the only channel they have). One
        # flag for the whole batch would have silently dropped every HEIC colour
        # in a mixed selection.
        targets, deferred = [], []
        for stem in stems:
            grp = self._groups.get(stem, {})
            xmp = grp.get("meta")
            own = xmp is not None and Path(xmp).suffix.lower() in _SELF_DESCRIBING
            if xmp is None:
                src = self._source_file(stem)
                if not src:
                    continue
                xmp = Path(src).with_suffix(".xmp")
                if not gate_off:
                    grp["xmp"] = xmp
                    grp["meta"] = xmp
            (deferred if (gate_off and not own) else targets).append(Path(xmp))

        # Gated value lives in the .dop; keep it in memory so this session shows it.
        for xmp in deferred:
            self._rows.setdefault(_norm(xmp), {"SourceFile": str(xmp)})[row_key] = value
        if not targets:
            return len(deferred)

        from engine.exif_writer import run_exiftool_files
        try:
            res = run_exiftool_files([f"-{tag}={value}", "-overwrite_original"],
                                     [str(t) for t in targets])
            if getattr(res, "returncode", 0):
                reason = f"exiftool exit {res.returncode}: {(res.stderr or '').strip()[:200]}"
                logger.warning(f"Sidecar batch write reported a problem over "
                               f"{len(targets)} file(s): {reason}")
                if is_colorrating:
                    for stem in stems:
                        self._note_write_failure(stem, tag, value, reason)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            if is_colorrating:
                for stem in stems:
                    self._note_write_failure(stem, tag, value, reason)
            else:
                logger.error(f"Sidecar batch write failed: {reason}")
            return 0

        from engine.sidecar_cache import SidecarCache
        touched = set()
        for xmp in targets:
            self._rows.setdefault(_norm(xmp), {"SourceFile": str(xmp)})[row_key] = value
            folder = xmp.parent
            if folder not in self._caches:
                self._caches[folder] = SidecarCache(folder)
            self._caches[folder].put_field(xmp, row_key, value)
            touched.add(folder)
        for folder in touched:
            self._caches[folder].save()      # once per folder, not once per file
        return len(targets) + len(deferred)

    def apply_many(self, upn_keys, *, new_stars: int | None = None,
                   new_color: str | None = None, progress=None) -> int:
        """Batch rating/colour for the sidecar viewer — the shape the culling
        grid's bulk dialog looks for, so it gets the same one-call write here."""
        keys = [k for k in upn_keys if k]
        if not keys:
            return 0
        if new_stars is not None:
            self.write_tag_many(keys, "XMP:Rating", str(new_stars), "Rating")
        if new_color is not None:
            self.write_tag_many(keys, "XMP:Label", new_color, "Label")
        if progress:
            progress(len(keys), len(keys))
        return len(keys)

    _CHANNEL_NAME = {"XMP:Label": "colour", "XMP:Rating": "rating"}

    def _note_write_failure(self, stem: str, tag: str, value: str, reason: str) -> None:
        """One place a non-persisted colour/rating edit is recorded and surfaced.
        Logs LOUD (greppable), keeps a record, and calls the host's on_write_error
        so a dialog pops immediately — the edit did not reach disk."""
        what = self._CHANNEL_NAME.get(tag, tag)
        msg = (f"{what} {value!r} for {stem} was NOT saved to disk — {reason}. "
               f"The card shows it, but closing the app now would lose it.")
        logger.error(f"SIDECAR WRITE FAILURE: {msg}")
        self._write_failures.append(
            {"stem": stem, "tag": tag, "value": value, "reason": reason})
        cb = getattr(self, "on_write_error", None)
        if callable(cb):
            try:
                cb(msg)
            except Exception as e:
                logger.warning(f"on_write_error callback raised: {e}")

    def _write_sidecar_tag(self, xmp_path, tag: str, value: str) -> tuple[bool, str]:
        """exiftool_write with a short retry — a .xmp briefly held by PhotoLab or
        mid Dropbox-sync is the common transient failure and clears in a second.
        Returns (ok, reason); reason is '' on success."""
        import time
        last = ""
        for attempt in range(3):
            try:
                exiftool_write(Path(xmp_path), [f"-{tag}={value}"])
                if attempt:
                    logger.info(f"sidecar write for {Path(xmp_path).name} "
                                f"succeeded on attempt {attempt + 1}")
                return True, ""
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                logger.warning(f"sidecar write attempt {attempt + 1}/3 failed for "
                               f"{Path(xmp_path).name}: {last}")
                time.sleep(0.4 * (attempt + 1))
        return False, last

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

        # Same reason for the website 'story': the blog/website read the master, not
        # the sidecar, so an album Description edit must also land in the master DB.
        if (tag == "XMP:Description" and getattr(self, "_master_db", None) is not None
                and stem in self._master_upn):
            self._mirror_description_to_master(stem, value)

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

        grp = self._groups.get(stem, {})
        xmp = grp.get("meta")
        # A self-describing file (HEIC/PNG) IS the channel — the value goes into
        # the picture, which is where PhotoLab and Lightroom look. The .xmp gate
        # below must not apply to it: that gate exists because PL mistreats
        # colour/rating in a RAW's .xmp, and with it on, a HEIC's colour would be
        # kept in memory for the session and then lost, having reached nothing.
        own = xmp is not None and Path(xmp).suffix.lower() in _SELF_DESCRIBING

        # .xmp sidecar: colour/rating is gated (default off); text always writes.
        suppress = is_colorrating and not color_rating_to_xmp_enabled() and not own

        if xmp is None:
            # No sidecar yet (e.g. a JPG-only group) — drop one beside the source.
            src = self._source_file(stem)
            if not src:
                if is_colorrating:
                    self._note_write_failure(
                        stem, tag, value,
                        "no .xmp sidecar and no source file to create one beside")
                return
            xmp = Path(src).with_suffix(".xmp")
            if not suppress:
                grp["xmp"] = xmp
                grp["meta"] = xmp
        if suppress:
            # Colour/rating in .xmp suppressed: keep the value in memory so the
            # current culling session still shows it (it's live in the .dop).
            self._rows.setdefault(_norm(xmp), {"SourceFile": str(xmp)})[row_key] = value
            return
        ok, reason = self._write_sidecar_tag(xmp, tag, value)
        if not ok:
            # Keep the value in memory so the session view stays consistent, but
            # the user must know it never reached disk.
            self._rows.setdefault(_norm(Path(xmp)), {"SourceFile": str(xmp)})[row_key] = value
            if is_colorrating:
                self._note_write_failure(stem, tag, value, reason)
            else:
                logger.error(f"Sidecar write failed for {Path(xmp).name}: {reason}")
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
