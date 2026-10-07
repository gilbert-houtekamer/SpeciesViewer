"""Provider contract — the single data seam between the picture engines and
the picture UI.

Two engines back the same UI:
  - Species preview  : per-file FilePicture, one day (still per-file today; to
                       be UPN-converged).
  - Rating Editor    : per-UPN, trip/master scope, computed Rate.

Both implement PictureItem + PictureCollection. The grid card, the filmstrip,
and the large-view right-hand panel render ONLY from these, so one place
defines the field set and the write operations, three places render them.

Capability flags let the UI grey what an engine can't do — master pictures are
read-mostly (but rating/colour stay editable), the preview engine has no rating
writer yet, etc. — the same pattern as the Option-C colour greying. So we can
ship before every editability gap is closed.

Design + decisions: memory rating-editor-and-preview-convergence. The compact
EXIF set is deliberately small (DxO-style), not the full PhotoMechanic dump.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtGui import QPixmap

logger = logging.getLogger("speciesviewer")


def _parse_dims(s: str):
    """('5220x3912' | '5220 3912' | '') → (5220, 3912) or None."""
    parts = re.split(r"[x× ]+", (s or "").strip())
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None


def jpg_is_crop_by_size(raw_dims: str, jpg_dims: str, frac: float = 0.75) -> bool:
    """Heuristic 'this picture is (probably) cropped' for the card's # marker, used
    when no .dop/.xmp crop rectangle can be read — a baked DxO/Lightroom render or
    an old master JPG keeps its crop only in the pixels. True when the JPG's long
    edge is ≤ frac of the RAW's long edge (default 25% smaller). A downscaled full
    frame trips it too, but in this workflow almost every developed JPG is a crop,
    so it's a good-enough cue (and doubles as a 'has a developed JPG' hint). Needs
    both sizes; returns False if either is missing/unparseable."""
    r = _parse_dims(raw_dims)
    j = _parse_dims(jpg_dims)
    if not r or not j:
        return False
    rl, jl = max(r), max(j)
    return rl > 0 and 0 < jl <= frac * rl

# Async image delivery: providers call this on the GUI thread when a pixmap is
# ready. Loading is cancellable via PictureCollection.cancel_pending_loads().
PixmapCallback = Callable[[QPixmap], None]


def apply_crop(img, crop):
    """Return the sub-rectangle of a QImage for a normalised crop
    {left,top,width,height} (fractions 0–1). Rect only — crop straighten (the
    angle field) is not applied. Shared by every viewer that shows the edit crop."""
    w, h = img.width(), img.height()
    if not w or not h:
        return img
    x = max(0, min(w - 1, round(crop["left"] * w)))
    y = max(0, min(h - 1, round(crop["top"] * h)))
    cw = max(1, min(w - x, round(crop["width"] * w)))
    ch = max(1, min(h - y, round(crop["height"] * h)))
    return img.copy(int(x), int(y), int(cw), int(ch))


def _sidecar_edit(raw_path, xmp_path, field):
    """One edit field ("crop" or "exposure") from a RAW's sidecars: the .xmp
    FIRST, the DxO .dop as fallback, or None.

    Why the .xmp leads (2026-09-29): while PhotoLab runs, SV holds its .dop
    writes back (sidecarlib._hold_dop_for_photolab), so the .xmp is the only file
    that has the newest crop/exposure — the .dop still has the old one until
    "Open in PhotoLab" hands it over. PhotoLab writes its own crop into the .xmp
    too (with XMP sync on), so an edit made there reads the same. The .dop
    remains the answer for a picture whose .xmp carries no edit at all.

    `xmp_path` defaults to the RAW's own `<stem>.xmp` (either spelling). It used
    to default to None, which silently made the read .dop-only — that bit both
    crop_for and exposure_for on 2026-09-25. The default belongs here."""
    from pathlib import Path
    if xmp_path is None and raw_path:
        for cand in (Path(str(raw_path)).with_suffix(".xmp"),
                     Path(str(raw_path)).with_suffix(".XMP")):
            if cand.exists():
                xmp_path = cand
                break
    value = None
    if xmp_path and Path(xmp_path).exists():
        try:
            from engine.sidecarlib import read_xmp_edits
            value = read_xmp_edits(str(xmp_path)).get(field)
        except Exception:
            value = None
    if value is None and raw_path:
        dop = Path(str(raw_path) + ".dop")     # DxO names the recipe <rawname>.dop
        if dop.exists():
            try:
                from engine.sidecarlib import read_dop_overrides
                value = read_dop_overrides(str(dop)).get(field)
            except Exception:
                value = None
    return value


def exposure_for(raw_path, xmp_path=None):
    """Current suggested exposure (EV, Lightroom's Exposure2012 scale) from a
    RAW's .xmp, else its DxO .dop, or None. See _sidecar_edit for the order."""
    return _sidecar_edit(raw_path, xmp_path, "exposure")


def crop_for(raw_path, xmp_path=None):
    """Normalised crop {left,top,width,height} (0–1) from a RAW's .xmp, else its
    DxO .dop, or None. See _sidecar_edit for the order."""
    return _sidecar_edit(raw_path, xmp_path, "crop")


def jpg_ignores_crop(jpg_path, preview_path, crop) -> bool:
    """True when the JPG shows the FULL frame, not `crop` — it was made before
    the crop reached the editor, so it is the old framing.

    A JPG beside the RAW (a DxO render, a Make JPG, a camera JPEG) is normally
    shown as-is: it is taken to be the finished, already-cropped picture. On
    2026-09-30 (Brazil n9074067) that showed the full frame while PhotoLab showed
    the crop: the _DxO.jpg had been exported while the crop was still held out of
    the .dop (PhotoLab was running), so PhotoLab rendered it uncropped.

    Only the PIXELS can say so. Timestamps cannot: PhotoLab rewrites the RAW's
    .dop ~3 s after every export and often hours later (324 of 379 Brazil
    renders have a newer .dop). The JPG's own metadata cannot either: this very
    export carries the crs:Crop of the crop it does NOT show — PhotoLab copies the
    .xmp across without applying it.

    So compare a tiny grey version of the JPG with the RAW preview whole and
    with the preview cropped. Stale only when the whole frame is a near-perfect
    match AND clearly beats the crop — on the Brazil trip (292 cropped renders)
    that is n9074067 alone (0.99 vs 0.27); the near-ties are JPGs that match
    neither (cropped differently in PhotoLab) and are left alone.

    Cached per (jpg, its mtime, crop): decodes two images, so run it off the GUI
    thread (a provider's produce()). Unreadable input → False (trust the JPG)."""
    from pathlib import Path
    try:
        key = (str(jpg_path), Path(str(jpg_path)).stat().st_mtime_ns,
               str(preview_path),
               tuple(round(float(crop[k]), 6) for k in ("left", "top", "width", "height")))
    except (OSError, KeyError, TypeError, ValueError):
        return False
    hit = _JPG_VERDICTS.get(key)
    if hit is not None:
        return hit
    try:
        from PIL import Image, ImageOps
        import numpy as np
        jpg = Image.open(str(jpg_path))
        jpg.draft("L", (400, 400))          # JPEG decode at 1/2..1/8 scale: cheap
        jpg = ImageOps.grayscale(jpg)
        pv = Image.open(str(preview_path))
        pv.draft("L", (1200, 1200))
        pv = ImageOps.grayscale(pv)
        w, h = pv.size
        box = (round(crop["left"] * w), round(crop["top"] * h),
               round((crop["left"] + crop["width"]) * w),
               round((crop["top"] + crop["height"]) * h))
        size = (48, max(8, round(48 * jpg.size[1] / jpg.size[0])))

        def vec(im):
            v = np.asarray(im.resize(size, Image.BILINEAR), dtype=float).ravel()
            v -= v.mean()
            n = np.linalg.norm(v)
            return v / n if n else v
        j = vec(jpg)
        full, cropped = float(j @ vec(pv)), float(j @ vec(pv.crop(box)))
        verdict = full >= 0.9 and full - cropped >= 0.2
    except Exception as e:
        logger.debug(f"jpg_ignores_crop: could not compare {jpg_path}: {e}")
        verdict = False
    if verdict:
        logger.info(f"{Path(str(jpg_path)).name} shows the full frame, not the "
                    f"crop (match {full:.2f} vs {cropped:.2f}): showing the crop instead")
    _JPG_VERDICTS[key] = verdict
    return verdict


_JPG_VERDICTS: dict = {}


JPG_OUT_OF_DATE_TIP = (
    "This picture's JPG shows the whole frame, not the crop, so it was made before "
    "the crop. The picture here shows the crop. Export the JPG again (PhotoLab or "
    "Make JPG) before it goes to the website.")


def verify_crop_written(raw_path, crop, res=None, who="") -> bool:
    """Read the crop straight back out of the files and log what happened.

    A crop is written to two channels (.dop when PhotoLab authored one, .xmp
    always) and read back from whichever answers first — the .dop, else the .xmp.
    Those are enough moving parts that "I wrote it" and "the app sees it" can
    disagree, which is exactly what was reported on 2026-09-25: a crop confirmed
    present in the .xmp, with the viewer still showing the frame uncropped, on a
    picture PhotoLab had never opened (so no .dop existed). It stopped
    reproducing the moment a .dop appeared, which is the worst kind of bug to
    chase from memory.

    So state it in the log at the moment of the write, while both the intent and
    the file are in hand: which channels took it, and what a fresh read returns.
    Cheap — it runs once per crop, not per repaint — and it turns "somehow it
    didn't apply" into a line naming the picture and the channel that lied.

    Returns True when the read-back matches what we asked for.
    """
    from pathlib import Path
    got = crop_for(str(raw_path), str(Path(str(raw_path)).with_suffix(".xmp")))
    channels = ""
    if isinstance(res, dict):
        channels = " ".join(k for k in ("dop", "xmp") if res.get(k)) or "NONE"
        created = " ".join(k for k in ("dop_created", "xmp_created") if res.get(k))
        if created:
            channels += f" (created {created})"
    ok = bool(got) and all(
        abs(float(got.get(k, -9)) - float(crop.get(k, -9))) < 1e-6 for k in
        ("left", "top", "width", "height"))
    name = Path(str(raw_path)).name
    if ok:
        logger.info(f"crop written [{who}] {name}: channels={channels}, reads back OK")
    else:
        logger.error(
            f"crop NOT readable after writing [{who}] {name}: channels={channels}, "
            f"wanted={crop}, read back={got}. The viewer will show the OLD frame.")
    return ok


def sweep_dop(raw_src, dest_dir, moved=()) -> None:
    """Move the DxO recipes of a deleted picture into dest_dir — the RAW's
    (``<rawname>.dop``) AND one beside every other file the delete moved
    (`moved`, e.g. ``<name>_DxO.jpg.dop``) — so no orphan recipe is left behind:
    Read & Summarize would otherwise synthesise a phantom, image-less picture
    from a lone .dop.

    Why every file, not just the RAW: PhotoLab writes a .dop for its own exported
    JPG too, and does so whenever that JPG changes — a colour click on it is
    enough (2026-09-30: 32 `_DxO.jpg.dop` left behind by a Blue delete, all
    stamped 13 s after the Blue click). Such a .dop appears after the last read,
    so it is not in the index and nothing else moves it.

    No-op for a file without a .dop; idempotent (a .dop already trashed with the
    files is simply gone). Shared by every delete path so they stay consistent."""
    import shutil
    from pathlib import Path
    sources = [s for s in [raw_src, *moved] if s]
    seen = set()
    for src in sources:
        dop = Path(str(src) + ".dop")
        if dop.name.lower() in seen or not dop.exists():
            continue
        seen.add(dop.name.lower())
        try:
            shutil.move(str(dop), str(Path(dest_dir) / dop.name))
        except Exception as e:
            logger.warning(f"Trash move failed for {dop.name}: {e}")


@dataclass
class ExifSummary:
    """Compact, DxO-style read-only EXIF for the info panel — intentionally
    small. Empty strings render as blank / a dash. Mirrors the kind of caption
    line the website uses, e.g. "OM-1 Mark II met OLYMPUS M.150-400mm F4.5,
    1/2500 sec bij F5.0, ISO 200, afstand 85.0 m"."""
    iso: str = ""
    aperture: str = ""
    shutter: str = ""
    ev_comp: str = ""           # not currently captured in the CSV → usually blank
    focal_length: str = ""
    model: str = ""             # camera body, e.g. "OM-1 Mark II"
    lens: str = ""              # e.g. "OLYMPUS M.150-400mm F4.5"
    distance: str = ""          # subject/focus distance, e.g. "85.0 m"
    dimensions: str = ""        # the master/RAW image size, e.g. "5600x3728"
    jpg_dimensions: str = ""    # the displayed/web JPG size, e.g. "800x533"
    gps: str = ""
    altitude: str = ""          # GPSAltitude as stored, e.g. "156.3505028 m"


# altitude_text moved to engine/geo.py so the website can share it (engine never
# imports gui); re-exported here for the existing GUI callers.
from engine.geo import altitude_text  # noqa: E402,F401


def exif_from_rows(upn_row: dict, file_row: dict) -> ExifSummary:
    """Build the compact summary from a UPN row (camera/lens/dims/gps) plus a
    representative file row (the discrete shot settings). Shared by the engine
    providers so both map the CSV columns identically."""
    f = file_row or {}
    u = upn_row or {}
    return ExifSummary(
        iso=f.get("EXIF:ISO", ""),
        aperture=f.get("EXIF:FNumber", "") or f.get("Composite:Aperture", ""),
        shutter=f.get("EXIF:ExposureTime", ""),
        ev_comp="",  # not captured in the CSV
        focal_length=f.get("EXIF:FocalLength", ""),
        model=u.get("CameraModel", "") or f.get("CameraModel", ""),
        lens=(u.get("LensLongName", "") or u.get("LensShortName", "")
              or f.get("EXIF:LensModel", "") or f.get("Composite:LensID", "")),
        distance=f.get("MakerNotes:FocusDistance", "") or f.get("EXIF:SubjectDistance", ""),
        dimensions=u.get("ImageSize", "") or f.get("ImageSize", ""),
        jpg_dimensions=u.get("JPGSize", ""),
        gps=u.get("GPSPosition", "") or f.get("Composite:GPSPosition", ""),
        altitude=u.get("GPSAltitude", "") or f.get("EXIF:GPSAltitude", ""),
    )


class PictureItem(ABC):
    """One picture (UPN). The website pairs species + headline; both are here
    as visually-similar fields. Write ops raise NotImplementedError by default;
    the matching can_edit_* flag tells the UI whether to offer the control."""

    # ── identity ────────────────────────────────────────────────────────────
    @property
    @abstractmethod
    def key(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @property
    def qualifier_suffix(self) -> str:
        """Compact sex/age/plumage suffix shown after the species name on the
        card (' ♀ br.'). Empty by default; species-carrying providers override."""
        return ""

    @property
    def sci_name(self) -> str:
        """Scientific species name, for the framed card's second meta line.
        Empty by default; species-carrying providers override."""
        return ""

    @property
    def name_triple(self) -> dict:
        """{'nl':.., 'en':.., 'sci':..} for the identity surfaces that show all
        three names at once (large image viewer, species page). Best-effort here
        (only the scientific name is known to the base); species-carrying
        providers override with the full NL/EN/Sci row."""
        return {"sci": (self.sci_name or "")}

    @property
    def name_profile(self) -> str:
        """The display profile in force for this item — its collection's, else the
        global default. Lets a detail surface order the three names the same way
        the grid did."""
        from utils.name_profile import effective_profile
        c = getattr(self, "_c", None)
        return (getattr(c, "_species_lang", "") or getattr(c, "_name_profile", "")
                or effective_profile())

    @property
    def display_lines(self) -> list:
        """Ordered (text, is_scientific) meta lines for the framed card — up to
        three, de-duplicated, the scientific one flagged so the card italicises
        it. The sex/age/plumage suffix rides the FIRST (primary) line.

        Default = the classic two-line shape (display name, then the scientific
        name if it differs). Species-carrying providers that hold the full
        NL/EN/Sci triple override this to render the chosen name profile."""
        primary = (self.display_name or "").strip()
        sci = (self.sci_name or "").strip()
        suffix = self.qualifier_suffix or ""
        lines: list = []
        if primary:
            lines.append((primary + suffix, primary.casefold() == sci.casefold()))
        if sci and sci.casefold() != primary.casefold():
            lines.append((sci, True))
        return lines

    # ── display (async + cancellable) ────────────────────────────────────────
    # A trip can be hundreds of pictures; the user changes the view long before
    # all of them are drawn. So loading is asynchronous (off the GUI thread) and
    # cancellable: the host calls PictureCollection.cancel_pending_loads() when
    # the visible set changes, and stale results are dropped. on_ready is always
    # invoked on the GUI thread; it may be called synchronously if cached.
    @abstractmethod
    def request_thumbnail(self, target_w: int, on_ready: PixmapCallback) -> None:
        """Load the small grid/filmstrip thumbnail; deliver via on_ready."""

    @abstractmethod
    def request_full_pixmap(self, on_ready: PixmapCallback) -> None:
        """Load the large image (extracted embedded preview for RAW/HEIC)."""

    # ── read fields (these are the panel's fields) ───────────────────────────
    @property
    @abstractmethod
    def species(self) -> str: ...

    @property
    @abstractmethod
    def headline(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def rating(self) -> int: ...        # 0–5

    @property
    @abstractmethod
    def color(self) -> str: ...         # "" | Red | Yellow | Green | Blue | Purple

    @property
    def rate(self) -> int | None:       # computed Rate score, display only
        return None

    @property
    def date(self) -> str:
        return ""

    @property
    def exif(self) -> ExifSummary:
        return ExifSummary()

    @property
    def has_jpg(self) -> bool:          # Option C / deletion eligibility
        return False

    @property
    def has_gps(self) -> bool:          # False → preview shows the missing-GPS badge
        return True

    @property
    def has_crop(self) -> bool:         # True → card shows a ⌗ (crop present: ours or PhotoLab/LR)
        return False

    @property
    def crop_rect(self) -> dict | None:
        """The crop rectangle itself, normalised {left,top,width,height} (0-1), so
        the large viewer can print the cropped pixel size next to the ⌗ marker.
        None when no rectangle is readable — has_crop can still be True then (a
        baked render is recognised by its size alone)."""
        return None

    @property
    def jpg_out_of_date(self) -> bool:
        """True when the picture's JPG shows the full frame, not its crop (see
        jpg_ignores_crop): the viewer shows the RAW + crop, the JPG is stale.
        Known once the image has been loaded — False until then."""
        return False

    # ── location line (grid card, opt-in) ────────────────────────────────────
    # show_location_line → the card draws a place line under the species name.
    # The data must be free (already loaded), so only providers that carry it
    # cheaply turn it on (e.g. the sidecar/culling collection from its cache).
    show_location_line: bool = False

    @property
    def place_fields(self) -> dict:     # {Location, City, Municipality, State}
        return {}                       # already-read place text; {} = none

    @property
    def gps_text(self) -> str:          # compact coordinates, e.g. "51.32989, 3.72910"
        return ""

    def location_line(self) -> str:
        """One line for the card, by preference: Location → 'City · Municipality ·
        State' → GPS coordinates → 'No GPS data available'. (Country is left off —
        too long for the caption, and the species grid is usually one country.)"""
        f = self.place_fields
        loc = (f.get("Location") or "").strip()
        if loc:
            return loc
        admin = " · ".join(p for p in (f.get("City"), f.get("Municipality"),
                                       f.get("State")) if p and str(p).strip())
        if admin:
            return admin
        gps = (self.gps_text or "").strip()
        if gps:
            return gps
        return "No GPS data available"

    @property
    def error_msg(self) -> str:         # species/headline warning (preview only)
        return ""

    @property
    def is_blog_extra(self) -> bool:
        """True → a landscape / context 'blog extra' (marker keyword 'landscape'
        / 'landschap', no species). It belongs in the day blog under its Headline,
        not in the species website or the master. Providers that carry a subject
        override this; the safe default is 'ordinary species'."""
        return False

    @property
    def file_exts(self) -> list[str]:   # e.g. ["JPG", "ORF", "XMP"] — a compact
        return []                       # "what files exist" hint on the card

    @property
    def is_video(self) -> bool:
        """True when what this card shows is a MOVIE: the UPN carries a video file
        and no still. Derived from file_exts here so every provider gets the same
        answer without each one re-deciding it.

        The "and no still" half matters. A day's videos arrive as their own UPNs,
        so in practice a video UPN is video-only — but a mixed UPN would display
        its camera original (projectlib orders stills ahead of MOV/MP4), and
        marking that card as a movie would be a lie about the picture on it."""
        from utils.raw_extensions import is_video, is_picture
        exts = self.file_exts or []
        return (any(is_video(e) for e in exts)
                and not any(is_picture(e) for e in exts))

    @property
    def from_master(self) -> bool:      # True → reference picture pulled from the
        return False                    # master DB (shown greyed, read-only)

    # ── write ops (gated by the capability flags below) ──────────────────────
    def set_species(self, value: str) -> None:
        raise NotImplementedError

    def set_headline(self, value: str) -> None:
        raise NotImplementedError

    def set_description(self, value: str) -> None:
        raise NotImplementedError

    def set_rating(self, value: int) -> None:
        raise NotImplementedError

    def set_color(self, value: str) -> None:
        raise NotImplementedError

    # ── crop suggestion (RAW only; seeds PhotoLab/Lightroom) ─────────────────
    # The large viewer's crop tool draws on the UNCROPPED frame, so it asks for
    # an uncropped pixmap; engines that don't apply a crop on display just return
    # the normal full image. set_crop writes the normalised {left,top,width,
    # height} to the sidecar(s). Gated by can_set_crop.
    def request_uncropped_pixmap(self, on_ready: PixmapCallback) -> None:
        self.request_full_pixmap(on_ready)

    def set_crop(self, crop: dict) -> None:
        raise NotImplementedError

    # ── exposure suggestion (RAW only; seeds PhotoLab/Lightroom) ─────────────
    # Same shape as crop: a suggestion written to the sidecar(s), not a pixel
    # edit this app itself develops. `exposure` is the current EV (0.0 = none
    # set), used to pre-fill the large viewer's control and to render its live
    # preview (see gui/exposure_render.py). Gated by can_set_exposure.
    @property
    def exposure(self) -> float:
        return 0.0

    def set_exposure(self, value: float) -> None:
        raise NotImplementedError

    # ── capability flags (UI greys unsupported ops) ──────────────────────────
    # Instances override these; class defaults are the safe read-only case.
    can_edit_species: bool = False
    can_edit_headline: bool = False
    can_edit_description: bool = False
    can_edit_rating: bool = False
    can_edit_color: bool = False
    can_set_crop: bool = False
    can_set_exposure: bool = False


class PictureCollection(ABC):
    """The ordered, already-filtered/sorted sequence the host owns. The UI
    navigates by index (filmstrip + arrow keys + cull auto-advance)."""

    # LOOK, don't touch: this collection's files belong to something else (a
    # virtual trip points at the MASTER's originals, it does not copy them), so
    # every viewer over it — grid, large view, map — must show and never write.
    # Collections that can write set it False; anything that offers a write
    # affordance is expected to consult it.
    read_only: bool = False

    @abstractmethod
    def __len__(self) -> int: ...

    @abstractmethod
    def item(self, index: int) -> PictureItem: ...

    @abstractmethod
    def index_of(self, key: str) -> int: ...

    def item_for_key(self, key: str) -> PictureItem:
        """The item for a key regardless of the current sort order. The large
        viewer navigates a fixed snapshot of keys (so its order is stable across
        a culling session), but always reads the live item via this."""
        return self.item(self.index_of(key))

    def reference_count(self, item: PictureItem) -> int:
        """How many pictures of this species already exist in the master —
        the cull-against-history signal. 0 when the host has no master view."""
        return 0

    def cancel_pending_loads(self) -> None:
        """Invalidate outstanding async image loads — call when the visible set
        changes (species/day switch, filter, column change) so stale results
        don't paint onto reused widgets. Default: no-op (trivial providers)."""

    def species_editor_context(self, key: str):
        """Return (fpr, [file_paths]) for SpeciesInfoPanel, or None if
        the engine doesn't support species editing (e.g. Rating Editor)."""
        return None

    def no_species_keys(self) -> list[str]:
        """UPNs with NO species attempt at all — neither a resolved name nor a
        flagged one. The complement of species_keys(); used by the ⚠ button's
        right-click 'No species yet' filter. Requires species_keys()."""
        if not (hasattr(self, "species_keys") and hasattr(self, "all_keys")):
            return []
        tried = set(self.species_keys())
        return [k for k in self.all_keys() if k not in tried]

    def file_directory(self, key: str) -> str:
        """Return the directory path of the representative file, or ''."""
        return ""

    def raw_path(self, key: str):
        """Return the on-disk Path of the UPN's RAW file (for opening it in an
        external RAW editor like DxO PhotoLab), or None if there is no RAW."""
        return None

    def photolab_path(self, key: str):
        """The file the PL button hands to DxO PhotoLab: the RAW, else a HEIC
        (PhotoLab 9 opens one like a JPG — e.g. to straighten a horizon), else
        None. Collections that keep a HEIC apart from raw_path() override this."""
        return self.raw_path(key)

    def gps(self, key: str):
        """Signed decimal (lat, lon) for the picture, or None if it has no GPS."""
        return None

    def gps_cached(self, key: str):
        """Signed decimal (lat, lon) from ALREADY-LOADED memory — no file read —
        or None when this collection has no in-memory GPS for the key. The map
        uses this to plot a whole day/trip without an exiftool spawn per picture
        (gps() reads the file and is fine for a single lookup, ruinous for N).
        Default falls through to the cheap-by-nature default of gps() (None);
        collections that cache GPS override this."""
        return None

    def gps_source(self, key: str):
        """The file to read GPS FROM (path only — no read), so a batch can read
        many at once off the GUI thread. None if there's no source."""
        return None

    def location_targets(self, key: str):
        """Writable files (XMP sidecar / JPG) to receive reverse-geocoded location
        text. Empty when there's nowhere LR-style to write it — including when the
        collection is read_only, which is what makes a viewer over someone else's
        files (the master's, via a virtual trip) safe to hand to the map."""
        return []

    def read_location(self, key: str) -> dict:
        """Location text currently in the picture's XMP (Location/City/State/
        Country/CountryCode), for display without the CSV. {} if none."""
        return {}

    def note_gps_written(self, key: str) -> None:
        """Tell the collection that `key` just had GPS written to its XMP (e.g. a
        paste-GPS), so any cached has-GPS state updates without a full rescan.
        No-op by default; overridden where has_gps is cached."""
        return None

    def note_location_written(self, key: str, fields: dict, protect=()) -> None:
        """Tell the collection that `key` just had these location fields written
        to its XMP, so the cached row (and the card's location line) updates
        without re-reading. `protect` names fields that were fill-only on disk (a
        pre-existing value was kept), so the cache must not adopt the new value for
        them either. No-op by default; overridden where rows are cached."""
        return None

    def note_locations_written(self, fields_by_key: dict, protect=()) -> None:
        """Bulk twin of note_location_written: {key: fields} for MANY pictures,
        persisted ONCE. Collections that save per picture override this — one save
        per picture rewrote the whole day CSVs 643 times for one Edit location."""
        for key, fields in fields_by_key.items():
            self.note_location_written(key, fields, protect)

    # ── external-edit refresh ─────────────────────────────────────────────────
    # True → the host offers a Refresh action + auto-refreshes on window focus, so
    # colour/rating/etc. changed by another tool (PhotoLab, Lightroom) writing the
    # same sidecars show up without reopening. Only collections backed by on-disk
    # sidecars set this.
    supports_external_refresh: bool = False

    def refresh(self) -> set:
        """Re-read metadata from disk for files an external tool changed, and
        return the set of keys whose displayed values changed (so the host
        refreshes just those cards). Default: no-op."""
        return set()

    def sidecar_paths(self) -> list:
        """On-disk sidecar files to watch for external edits (so the host can
        refresh the instant another tool writes). Only sidecar-backed
        collections return any; default empty."""
        return []
