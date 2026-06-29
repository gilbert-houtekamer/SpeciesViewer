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

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtGui import QPixmap

# Async image delivery: providers call this on the GUI thread when a pixmap is
# ready. Loading is cancellable via PictureCollection.cancel_pending_loads().
PixmapCallback = Callable[[QPixmap], None]


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
    def file_exts(self) -> list[str]:   # e.g. ["JPG", "ORF", "XMP"] — a compact
        return []                       # "what files exist" hint on the card

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

    # ── capability flags (UI greys unsupported ops) ──────────────────────────
    # Instances override these; class defaults are the safe read-only case.
    can_edit_species: bool = False
    can_edit_headline: bool = False
    can_edit_description: bool = False
    can_edit_rating: bool = False
    can_edit_color: bool = False


class PictureCollection(ABC):
    """The ordered, already-filtered/sorted sequence the host owns. The UI
    navigates by index (filmstrip + arrow keys + cull auto-advance)."""

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

    def file_directory(self, key: str) -> str:
        """Return the directory path of the representative file, or ''."""
        return ""

    def raw_path(self, key: str):
        """Return the on-disk Path of the UPN's RAW file (for opening it in an
        external RAW editor like DxO PhotoLab), or None if there is no RAW."""
        return None

    def gps(self, key: str):
        """Signed decimal (lat, lon) for the picture, or None if it has no GPS."""
        return None

    def gps_source(self, key: str):
        """The file to read GPS FROM (path only — no read), so a batch can read
        many at once off the GUI thread. None if there's no source."""
        return None

    def location_targets(self, key: str):
        """Writable files (XMP sidecar / JPG) to receive reverse-geocoded location
        text. Empty when there's nowhere LR-style to write it."""
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

    def note_location_written(self, key: str, fields: dict) -> None:
        """Tell the collection that `key` just had these location fields written
        to its XMP, so the cached row (and the card's location line) updates
        without re-reading. No-op by default; overridden where rows are cached."""
        return None
