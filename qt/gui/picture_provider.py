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
    dimensions: str = ""
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

    @property
    def error_msg(self) -> str:         # species/headline warning (preview only)
        return ""

    @property
    def file_exts(self) -> list[str]:   # e.g. ["JPG", "ORF", "XMP"] — a compact
        return []                       # "what files exist" hint on the card

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
