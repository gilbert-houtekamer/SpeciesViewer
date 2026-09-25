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
import re
from pathlib import Path

from PyQt6.QtGui import QImage

from gui.picture_provider import (
    PictureItem, PictureCollection, ExifSummary, PixmapCallback, exif_from_rows, sweep_dop)
from gui.image_loader import AsyncImageLoader
from gui.preview_provider import preview_cache_dir_for, cached_jpg_for
from gui.rating_editor import _compute_rate, _safe_int
from engine.exif_writer import exiftool_write
from engine.sidecarlib import push_metadata_to_dop
from utils.sidecar_policy import (
    color_rating_to_xmp_enabled, color_rating_to_dop_enabled,
)
from utils.raw_extensions import RAW, DXO_RAW, bare, is_video, SELF_DESCRIBING

_DXO_RAW_EXTS = bare(DXO_RAW)  # canonical table — see utils/raw_extensions.py

# The camera filter's value for a picture that never had a camera. NOT "" — an empty
# string was silently dropped by both the combo and the filter set, so the 1,421 master
# pictures whose source recorded no body (the waarneming.nl imports) could not be
# selected at all. A real sentinel makes them a bucket you can actually pick.
NO_CAMERA = "\x00no-camera"

logger = logging.getLogger("speciesviewer")

_RAW_EXTS = RAW  # canonical table; was missing .cr3/.dng/.raf — see utils/raw_extensions.py


class RatingEditorItem(PictureItem):
    """Thin view over one UPN row; writes delegate to the collection.

    A trip item is rating/colour-editable. A master item (pulled from the
    master DB as a reference for judging website-worthiness) is read-only — its
    capability flags are False so the card greys the controls."""

    can_edit_headline = False
    can_edit_species = False

    def __init__(self, collection: "RatingEditorCollection", upn_key: str):
        self._c = collection
        self._key = upn_key
        self._master = collection.is_master(upn_key)
        # A whole collection can be view-only (e.g. the Album / virtual-trip
        # viewer, whose pictures ARE the master's shared files — editing would
        # be a master write-back, deferred). Otherwise trip pictures are always
        # editable; master reference pictures only when the master is not in
        # read-only mode (Phase 2).
        # A committed-day trip picture is locked (the day is frozen — changes only
        # via Empty-the-trip). The card stamps a 🔒 and greys its controls, exactly
        # like a master reference card.
        self.frozen = collection.is_frozen_day(upn_key)
        editable = (not collection.view_read_only) and (not self.frozen) and \
                   ((not self._master) or (not collection.master_read_only))
        self.can_edit_rating = editable
        self.can_edit_color = editable
        # Description (the website story) writes back for trip pictures via the
        # per-day CSV routing, and — like rating/colour — for master pictures when
        # master writes are enabled on this machine (master.readonly = no).
        self.can_edit_description = editable
        # Headline (the title of a landscape / context 'blog extra') follows the
        # same write rule as Description; it only matters for blog extras but the
        # field is harmless on any editable picture.
        self.can_edit_headline = editable

    @property
    def from_master(self) -> bool:
        return self._master

    @property
    def from_album(self) -> bool:
        """In the Album viewer's full-master scope, a master picture that also
        belongs to the album (its UPN is one of the album's extracted pictures)
        is tagged 'from Album' instead of 'from master', so the user can see
        which of the species' master pictures came from this trip's range."""
        return self._c.is_album_member(self._key)

    @property
    def has_crop(self) -> bool:
        """This picture carries a crop (ours / PhotoLab / Lightroom) — the card and
        filmstrip stamp a small marker for it. True even when the displayed image
        is the already-cropped JPG render (the crop lives in the RAW's sidecar)."""
        return self._c.crop_recorded(self._key)

    @property
    def crop_rect(self) -> dict | None:
        """The rectangle behind has_crop, for the large viewer's cropped-size
        line. None for the size-cue case (a baked render keeps no rectangle)."""
        return self._c.crop_rect(self._key)

    @property
    def can_set_crop(self) -> bool:
        """A crop suggestion is written to the RAW's DxO .dop / Lightroom .xmp for
        PhotoLab to open with, so it needs a writable trip picture that has a RAW.
        Master reference cards and the read-only Album viewer stay look-only.
        Lazy (one disk stat) so building a species' grid pays nothing — only the
        large viewer, which asks per shown picture, resolves it."""
        if self._c.view_read_only or self._master or self.frozen:
            return False
        return self._c.raw_path(self._key) is not None

    # exposure suggestion — same carve-out as crop.
    @property
    def can_set_exposure(self) -> bool:
        return self.can_set_crop

    @property
    def exposure(self) -> float:
        return self._c.exposure_recorded(self._key) or 0.0

    @property
    def _row(self) -> dict:
        return self._c._upn_row(self._key)

    # identity
    @property
    def key(self) -> str:
        return self._key

    @property
    def display_name(self) -> str:
        # A landscape / context "blog extra" is titled by its Headline (what the
        # website shows). It now carries a synthetic species name (the topic year
        # leaf, e.g. "2026"), so this must win BEFORE species_name, not after.
        if self.is_blog_extra and self.headline:
            return self.headline
        name = self._c.species_name(self._row)
        if name:
            return name
        return self._key

    @property
    def place_fields(self) -> dict:
        """Place columns for a map bubble's tooltip (Location / City / …)."""
        r = self._row
        return {k: str(r.get(k, "") or "")
                for k in ("Location", "City", "Municipality", "State")}

    @property
    def qualifier_suffix(self) -> str:
        from gui.culling_species import (qualifier_suffix_from_fields,
                                         qualifier_suffix_from_subject)
        r = self._row
        # Master/trip picture rows carry the derived fields; fall back to parsing
        # the raw subject when they're absent.
        suffix = qualifier_suffix_from_fields(
            r.get("malefemale", ""), r.get("ageintext", ""), r.get("summerwinter", ""))
        return suffix or qualifier_suffix_from_subject(r.get("subject", "") or "")

    @property
    def sci_name(self) -> str:
        # WetSoortNaam = the scientific name carried on every trip/master row;
        # language-independent, so it's the framed card's stable second line.
        return self._row.get("WetSoortNaam", "") or ""

    @property
    def name_triple(self) -> dict:
        r = self._row
        return {"nl": r.get("NLSoortNaam", "") or "",
                "en": r.get("EngSoortNaam", "") or "",
                "sci": r.get("WetSoortNaam", "") or ""}

    @property
    def display_lines(self) -> list:
        # Full profile rendering: up to three deduped NL/EN/Sci lines, with the
        # sex/age/plumage suffix on the primary line. A blog extra is titled by
        # its Headline, so it keeps a single line (mirrors display_name).
        if self.is_blog_extra and self.headline:
            return [(self.headline, False)]
        lines = self._c.species_lines(self._row)
        if not lines:
            return [((self.display_name or self._key), False)]
        suffix = self.qualifier_suffix or ""
        if suffix:
            text, is_sci = lines[0]
            lines = [(text + suffix, is_sci)] + lines[1:]
        return lines

    # read fields
    @property
    def species(self) -> str:
        return self._c.species_name(self._row)

    @property
    def headline(self) -> str:
        return self._row.get("Headline", "")

    @property
    def is_blog_extra(self) -> bool:
        from engine.speciesclassification import SpeciesClassification
        return SpeciesClassification.isBlogExtra(self._row.get("subject", ""))

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

    # write ops — no-op when this item isn't editable, so a bulk-apply or
    # keyboard path can never write past a read-only (Album) viewer or a
    # read-only master reference card.
    def set_rating(self, value: int) -> None:
        if not self.can_edit_rating:
            return
        self._c.apply(self._key, new_stars=value)

    def set_color(self, value: str) -> None:
        if not self.can_edit_color:
            return
        self._c.apply(self._key, new_color=value)

    def set_description(self, value: str) -> None:
        if not self.can_edit_description:
            return
        self._c.apply_description(self._key, value)

    def set_headline(self, value: str) -> None:
        if not self.can_edit_headline:
            return
        self._c.apply_headline(self._key, value)

    def set_crop(self, crop: dict):
        """Write the suggested crop to this trip picture's RAW sidecars (see the
        collection). No-op past a read-only / master card."""
        if not self.can_set_crop:
            return None
        return self._c.set_crop(self._key, crop)

    def set_exposure(self, value: float) -> None:
        """Write the suggested exposure to this trip picture's RAW sidecars (see
        the collection). No-op past a read-only / master card."""
        if not self.can_set_exposure:
            return None
        return self._c.set_exposure(self._key, value)

    # async image
    def request_thumbnail(self, target_w: int, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, target_w, on_ready)

    def request_full_pixmap(self, on_ready: PixmapCallback) -> None:
        self._c.request_image(self._key, None, on_ready)

    def request_uncropped_pixmap(self, on_ready: PixmapCallback) -> None:
        # The crop tool draws on the full frame, so it needs the RAW's uncropped
        # embedded preview — not the (already-cropped) JPG render that
        # request_full_pixmap would return.
        self._c.request_uncropped_image(self._key, on_ready)


class RatingEditorCollection(PictureCollection):
    def __init__(self, fpr, day_objects, upn_day_map, *,
                 jpg_only: bool = True, species_lang: str = "nl_en_sci",
                 loader: AsyncImageLoader | None = None,
                 view_read_only: bool = False, frozen_days=None):
        self.fpr = fpr
        self._day_objects = day_objects      # dict day->(Files,Pictures), or None in Day scope
        self._upn_day_map = upn_day_map      # upn_key -> day
        # Days that are Frozen/Committed (see gui/day_freeze.py). A TRIP picture
        # from such a day is immutable — its item's edit flags are off and it
        # can't be deleted (a committed day changes only via Empty-the-trip). In
        # Day scope the whole collection is view_read_only instead, so this stays
        # empty there; it drives the per-picture 🔒 lock in Trip/Trip+Master scope.
        self._frozen_days = set(frozen_days or ())
        self._jpg_only = jpg_only
        self._color_filter: set[str] = set()   # lowercased colour names; empty = all
        self._camera_filter: set[str] = set()  # exact CameraModel strings; empty = all
        # Card ordering: "rate" (Rate high→low, the default), "time_asc"/"time_desc"
        # (capture time — groups near-identical burst frames for culling), or
        # "camera" (by body, then capture time).
        self._sort_key = "rate"
        self._species_lang = species_lang
        self._loader = loader or AsyncImageLoader()
        # Whole-collection view-only: every card non-editable (Album viewer).
        self._view_read_only = bool(view_read_only)
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
        # master_full (Album viewer "Master" scope): show the species' COMPLETE
        # master set — every picture, including the album's own in-range ones,
        # and NO trip/album cards (so nothing is shown twice). Plain Trip+Master
        # keeps False (trip cards + master-minus-trip reference).
        self._master_full = False
        self._master_keys: set[str] = set()  # keys currently shown from master
        # An ALBUM is the master seen through a date window. Rather than loading
        # the window's pictures into fpr, the album viewer narrows the SAME
        # per-species master query to the UPNs the window contains: Day scope is
        # one day's set, Album scope the whole range, Master scope None (no
        # restriction — the species everywhere, which is the standalone Master
        # Species Viewer). One query, three scopes, no copy of the data.
        self._master_restrict: set[str] | None = None
        # The album's whole UPN set, for tagging a card "from Album" in Master
        # scope. Independent of the restriction above, which changes per scope.
        self._album_keys: set[str] = set()
        # Lazy: don't scan all trip pictures (each jpg_path() does several
        # Path.exists() calls — thousands of filesystem stats over D:/E: and
        # Dropbox) until a species is actually selected. The dialog shows
        # "select a species" first; _rebuild_cards calls rebuild(filter) then.
        self._order: list[str] = []
        # Per-UPN "has a crop recorded" flag (one .dop/.xmp read each) — cached so
        # a species' whole grid isn't re-reading sidecars; cleared on rebuild and
        # invalidated per key on set_crop.
        self._crop_flag_cache: dict[str, bool] = {}
        # JPGs baked in-session by Create JPG (embedded preview + crop). Not in
        # the trip CSVs until the next Read & Summarize, so jpg_path() consults
        # this so the card can show the new image right away.
        self._blog_jpgs: dict[str, Path] = {}

    # ── master reference wiring ───────────────────────────────────────────
    def set_master(self, master_db, master_root, *, include: bool = True,
                   read_only: bool = True, full: bool = False) -> None:
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
        self._master_full = bool(full)
        self._master_upn = {}
        self._master_files = {}
        self._master_keys = set()

    def set_master_scope(self, restrict_to: set[str] | None,
                         album_keys: set[str] | None = None) -> None:
        """Narrow the per-species master query to `restrict_to` (None = no
        narrowing), and record the album's full UPN set for the "from Album"
        tag. This is the whole of an album scope: Day, Album and Master differ
        only in which set is passed here."""
        self._master_restrict = set(restrict_to) if restrict_to is not None else None
        if album_keys is not None:
            self._album_keys = set(album_keys)

    @property
    def master_read_only(self) -> bool:
        return getattr(self, "_master_read_only", True)

    @property
    def view_read_only(self) -> bool:
        return getattr(self, "_view_read_only", False)

    def is_master(self, key: str) -> bool:
        return key in self._master_keys

    def is_frozen_day(self, key: str) -> bool:
        """True when this TRIP picture belongs to a Frozen/Committed day, so it
        is locked (no edit, no delete). Master pictures are never 'frozen' here —
        they have their own read-only path. See gui/day_freeze.py."""
        if key in self._master_keys:
            return False
        return self._upn_day_map.get(key) in self._frozen_days

    def is_album_member(self, key: str) -> bool:
        """Full-master scope only: True when this master picture's UPN is also
        one of the album's own — i.e. it falls in the album's date range. Empty
        for the standalone Master Species Viewer, which has no album."""
        return self._master_full and key in self._album_keys

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

    def master_upn_moveable(self, key: str) -> bool:
        """A master picture can be moved to another species only when master
        writes are enabled and it has no JPG — same gate as delete (the JPG
        best-pick move isn't implemented)."""
        return self.master_upn_deletable(key)

    def master_species_targets(self) -> list[dict]:
        """Every species in the master (species.csv mirror) as a Move-to target:
        [{"sci","nl","en","target_path"}]. Empty when no master DB is attached."""
        if self._master_db is None:
            return []
        return self._master_db.all_species_rows()

    def move_master_picture(self, key: str, target: dict) -> dict:
        from db.master_mutate import move_picture_to_species
        return move_picture_to_species(self._master_db, self._master_root, key, target)

    def resync_master_metadata(self, keys) -> list[dict]:
        """Re-read XMP:Rating / XMP:Label from disk for these master pictures.

        The master only ever hears about a rating that SpeciesViewer itself wrote. Set
        two stars in PhotoLab or Lightroom and it lands in the .xmp — and Refresh does
        not notice, because Refresh looks for NEW files and for JPGs whose pixels moved,
        and an already-tracked sidecar with a new star inside it is neither.

        The picture's rating is the MAX of XMP:Rating across its files and its label is
        the XMP's (else the JPG's) — mirroring the pipeline (projectlib.py:1052-1055,
        findBestItem), so a re-read here and a full master re-read cannot disagree.

        ONE exiftool call for the whole set, via an argfile: a species can carry 800
        sidecars and JPGs, and per-file spawns would make this unusable. Returns what
        changed."""
        import json as _json
        import os
        import subprocess
        import tempfile
        from gui.rating_editor import _compute_rate, _safe_int

        if self._master_db is None or self._master_root is None:
            return []
        targets: list[tuple[str, str, str]] = []      # (key, fname, abspath)
        for key in keys:
            row = self._master_upn.get(key)
            if not row:
                continue
            rel = (row.get("TargetPath") or row.get("BestJPGPathName") or "").strip()
            if not rel:
                continue
            for fname in (row.get("Files") or []):
                frow = self._master_files.get(fname)
                if not frow:
                    continue
                if frow.get("WindowsExtension", "").lower() not in ("xmp", "jpg", "jpeg"):
                    continue
                p = self._master_root / rel / fname
                if p.exists():
                    targets.append((key, fname, str(p)))
        if not targets:
            return []

        by_src: dict = {}
        af = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             encoding="utf-8") as fh:
                af = fh.name
                for _k, _f, p in targets:
                    fh.write(p + "\n")
            out = subprocess.run(
                ["exiftool", "-j", "-q", "-charset", "filename=utf8",
                 "-XMP:Rating", "-XMP:Label", "-@", af],
                capture_output=True, text=True, encoding="utf-8", timeout=300).stdout
            for r in (_json.loads(out) if out.strip() else []):
                by_src[os.path.normcase(os.path.normpath(r.get("SourceFile", "")))] = r
        except Exception as e:
            logger.warning(f"master metadata resync: exiftool failed: {e}")
            return []
        finally:
            if af:
                try:
                    os.unlink(af)
                except OSError:
                    pass

        # group the fresh values per picture
        per_key: dict = {}
        for key, fname, p in targets:
            r = by_src.get(os.path.normcase(os.path.normpath(p)))
            if r is None:
                continue
            per_key.setdefault(key, []).append((fname, r))

        changed = []
        for key, items in per_key.items():
            row = self._master_upn.get(key)
            file_updates: dict = {}
            for fname, r in items:
                frow = self._master_files.get(fname) or {}
                upd = {}
                disk_rating = r.get("Rating", None)
                if disk_rating not in (None, "") and \
                        str(disk_rating) != str(frow.get("XMP:Rating", "") or ""):
                    upd["XMP:Rating"] = str(disk_rating)
                disk_label = str(r.get("Label", "") or "")
                if disk_label != str(frow.get("XMP:Label", "") or ""):
                    upd["XMP:Label"] = disk_label
                if upd:
                    frow.update(upd)
                    self._master_files[fname] = frow
                    file_updates[fname] = upd
            if not file_updates:
                continue

            # Picture rating = MAX over its files (projectlib.computeRating); label
            # prefers the XMP over the JPG (findBestItem).
            best = 0
            label = ""
            for fname in (row.get("Files") or []):
                frow = self._master_files.get(fname) or {}
                ext = frow.get("WindowsExtension", "").lower()
                if ext not in ("xmp", "jpg", "jpeg"):
                    continue
                best = max(best, _safe_int(frow.get("XMP:Rating", "0") or 0))
                lab = str(frow.get("XMP:Label", "") or "")
                if lab and (not label or ext == "xmp"):
                    label = lab
            old_rating, old_label = (str(row.get("Rating", "") or ""),
                                     str(row.get("Label", "") or ""))
            row["Rating"] = str(best)
            row["Label"] = label
            new_rate = _compute_rate(row, self._master_files)
            row["Rate"] = str(new_rate)
            try:
                self._master_db.apply_rating(
                    key, rating=best, label=label, rate=new_rate,
                    file_updates=file_updates)
            except Exception as e:
                logger.error(f"master metadata resync: DB write failed for {key}: {e}",
                             exc_info=True)
                continue
            changed.append({"key": key, "rating": (old_rating, str(best)),
                            "label": (old_label, label), "rate": new_rate})
            logger.info(f"metadata resync: {key} rating {old_rating or '0'} -> {best}, "
                        f"label {old_label or '-'} -> {label or '-'}, rate {new_rate}")
        return changed

    def delete_master_file(self, key: str, fname: str) -> dict:
        from db.master_mutate import delete_file
        return delete_file(self._master_db, self._master_root, key, fname)

    def add_master_files(self, key: str) -> dict:
        """Ingest any untracked files of this UPN sitting in its species folder
        (e.g. a freshly-rendered DxO JPG + its .dop)."""
        from db.master_mutate import add_upn_files
        return add_upn_files(self._master_db, self._master_root, key)

    def resync_master_jpgs(self, key: str) -> list[dict]:
        """Correct the master where a tracked JPG changed size on disk (a PhotoLab
        re-export over the same name). Returns [{fname, old, new}, …]."""
        from db.master_mutate import resync_jpg_sizes
        return resync_jpg_sizes(self._master_db, self._master_root, key)

    def refresh_master_species(self, pictures: list[dict]) -> dict:
        """Ingest new files + correct changed JPG sizes for a whole species — ONE folder
        listing and ONE exiftool call, not one of each per picture."""
        from db.master_mutate import refresh_species
        return refresh_species(self._master_db, self._master_root, pictures)

    def master_taxon_lineage(self, key: str) -> dict:
        """{'kingdom': …, 'phylum': …} for this master picture's species — so the picker
        offers only the qualifiers that mean something for it."""
        if self._master_db is None:
            return {}
        row = self._master_upn.get(key) or {}
        sci = (row.get("WetSoortNaam") or "").strip()
        return self._master_db.taxon_lineage(sci) if sci else {}

    def master_qualifiers(self, key: str) -> dict:
        """{group index: canonical token} currently on this master picture."""
        from gui.culling_species import split_species_qualifiers
        row = self._master_upn.get(key) or {}
        _name, selected = split_species_qualifiers(row.get("subject", "") or "")
        return selected

    def apply_master_qualifiers(self, key: str, selected: dict) -> bool:
        """Write sex / age / plumage onto a master picture.

        The SPECIES NAME IS NEVER TOUCHED. The subject is split, the qualifier tokens are
        replaced, and the same name is put back — so this cannot silently re-determine a
        picture, which is a different act with different consequences.

        The three derived columns come from specieslib.getBirdInfo, the parser of record,
        rather than a second copy of its vocabulary: what we store must be exactly what a
        full master re-read would produce."""
        from gui.culling_species import (split_species_qualifiers,
                                         build_species_subject, qualifier_fields)
        row = self._master_upn.get(key)
        if not row or self._master_db is None or self._master_root is None:
            return False
        name, _old = split_species_qualifiers(row.get("subject", "") or "")
        if not name:
            # No subject stored (older master rows) — fall back to the species name, so
            # the keyword we write still resolves to the right taxon on a re-read.
            name = (row.get("WetSoortNaam") or "").strip()
        if not name:
            logger.warning(f"master qualifiers: {key} has no species to attach them to.")
            return False
        subject = build_species_subject(name, selected)
        mf, age, sw = qualifier_fields(subject)

        rel = (row.get("TargetPath") or row.get("BestJPGPathName") or "").strip()
        file_updates: dict = {}
        for fname in (row.get("Files") or []):
            frow = self._master_files.get(fname)
            if not frow or not rel:
                continue
            if frow.get("WindowsExtension", "").lower() not in ("xmp", "jpg", "jpeg"):
                continue
            fpath = self._master_root / rel / fname
            if not fpath.exists():
                continue
            try:
                # A list-type XMP tag is REPLACED by assignment, so the sidecar ends up
                # carrying exactly this keyword — the same contract as the culling picker.
                exiftool_write(fpath, [f"-XMP:Subject={subject}"])
            except Exception as e:
                logger.error(f"master qualifiers: exiftool write failed for "
                             f"{fpath.name}: {e}")
                continue
            frow["XMP:Subject"] = subject
            file_updates[fname] = {"XMP:Subject": subject}

        row["subject"] = subject
        row["malefemale"], row["ageintext"], row["summerwinter"] = mf, age, sw
        try:
            return self._master_db.apply_qualifiers(
                key, subject=subject, malefemale=mf, ageintext=age, summerwinter=sw,
                file_updates=file_updates)
        except Exception as e:
            logger.error(f"master qualifiers: DB write failed for {key}: {e}",
                         exc_info=True)
            return False

    def master_jpgs(self, key: str) -> list[dict]:
        """The picture's real JPGs (thumbnails excluded), biggest first, each flagged
        `best` (publishes today) and `pinned` (a human chose it)."""
        if self._master_db is None or key not in self._master_keys:
            return []
        return self._master_db.jpgs_for_upn(key)

    def pin_master_jpg(self, key: str, fname: str) -> bool:
        """Choose which JPG is this picture's public face — or fname="" to hand the
        choice back to the code (biggest wins)."""
        if self._master_db is None:
            return False
        ok = self._master_db.apply_pin_jpg(key, fname)
        if ok:                      # keep the in-memory row the cards render from in step
            row = self._master_upn.get(key)
            if row is not None:
                fresh = self._master_db.picture(key) or {}
                row["BestJPG"] = fresh.get("BestJPG", "")
                row["JPGSize"] = fresh.get("JPGSize", "")
                row["BestJPGPinned"] = fresh.get("BestJPGPinned", "")
        return ok

    def delete_master_jpg(self, key: str, fname: str) -> dict:
        """Trash ONE JPG (the never-trash-a-JPG carve-out); the picture survives on
        its RAW or its other renders."""
        from db.master_mutate import delete_jpg
        return delete_jpg(self._master_db, self._master_root, key, fname)

    def _upn_row(self, key: str) -> dict:
        if key in self._master_keys:
            return self._master_upn.get(key, {})
        return self.fpr.upn.UniquePictureName_dict.get(key, {})

    def _files_dict_for(self, key: str) -> dict:
        return self._master_files if key in self._master_keys else self.fpr.fi.files_dict

    # ── ordering / filtering ──────────────────────────────────────────────
    def rebuild(self, filter_name: str | None = None, *,
                forced_sci: str | None = None) -> None:
        # Resolve the scientific name of the selected species (from the trip
        # rows) so master pictures can be matched on WetSoortNaam — robust
        # across the NL/Eng/Sci display languages. The Master Species Viewer has
        # no trip rows to resolve from, so it passes the sci in directly.
        self._crop_flag_cache.clear()   # sidecars may have changed since last build
        sci = (forced_sci or "").strip().casefold() or None
        if sci is None and filter_name and self._include_master and self._master_db is not None:
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
        # `sci` used to be resolvable only when the master was already attached;
        # with forced_sci a caller can name a species before that (an album
        # rebuilding on the way to attaching), so check the DB, not just the name.
        if sci and self._include_master and self._master_db is not None:
            pics = self._master_db.pictures_for_species(sci)
            # An album scope narrows that to its date window (see
            # set_master_scope). The query is the same one the Master Species
            # Viewer runs; the window is the only difference between them.
            if self._master_restrict is not None:
                pics = [p for p in pics
                        if p["UniquePictureName"] in self._master_restrict]
            # "Master" (full) scope keeps every master picture of the species;
            # Trip+Master drops the ones the trip already owns to avoid showing
            # a picture twice.
            if not self._master_full:
                trip_keys = self.fpr.upn.UniquePictureName_dict
                pics = [p for p in pics if p["UniquePictureName"] not in trip_keys]
            self._master_upn = {p["UniquePictureName"]: p for p in pics}
            if self._master_upn:
                self._master_files = self._master_db.files_for_upns(
                    self._master_upn.keys())
            self._master_keys = set(self._master_upn.keys())

        rows = []   # (key, row) — the sort key is derived in _sorted_keys
        # In full-master scope the master set already contains the album's own
        # pictures, so don't also add the trip/album rows (no duplicates).
        if not self._master_full:
            for key, row in self.fpr.upn.UniquePictureName_dict.items():
                if filter_name and self.species_name(row) != filter_name:
                    continue
                if self._jpg_only and self.jpg_path(key) is None:
                    continue
                if self._color_filter and self._row_color(row) not in self._color_filter:
                    continue
                if self._camera_filter and self._camera_key(row) not in self._camera_filter:
                    continue
                rows.append((key, row))
        for key in list(self._master_keys):
            if self._jpg_only and self.jpg_path(key) is None:
                self._master_keys.discard(key)   # not shown → not a master key
                continue
            if (self._color_filter
                    and self._row_color(self._master_upn[key]) not in self._color_filter):
                continue  # view filter only — keep it a master key for routing
            if (self._camera_filter
                    and self._camera_key(self._master_upn[key]) not in self._camera_filter):
                continue
            rows.append((key, self._master_upn[key]))

        self._order = self._sorted_keys(rows)

    def _sorted_keys(self, rows) -> list[str]:
        """Order (key, row) pairs by the active sort key and return just the keys.
        Capture time uses DateTimeOriginal, whose 'YYYY:MM:DD HH:MM:SS' form sorts
        chronologically as a plain string; the UPN key is the stable tie-break."""
        sk = self._sort_key
        if sk == "time_asc":
            rows.sort(key=lambda kr: ((kr[1].get("DateTimeOriginal", "") or ""), kr[0]))
        elif sk == "time_desc":
            rows.sort(key=lambda kr: ((kr[1].get("DateTimeOriginal", "") or ""), kr[0]),
                      reverse=True)
        elif sk == "camera":
            # Unknown-camera pictures sort last ("~"), then by capture time.
            rows.sort(key=lambda kr: ((self._row_camera(kr[1]) or "~~~").casefold(),
                                      (kr[1].get("DateTimeOriginal", "") or ""), kr[0]))
        else:   # "rate" — Rate high→low (default)
            rows.sort(key=lambda kr: _safe_int(kr[1].get("Rate", 0) or 0), reverse=True)
        return [k for k, _ in rows]

    def set_jpg_only(self, value: bool) -> None:
        self._jpg_only = value

    def set_color_filter(self, colors) -> None:
        """Restrict the shown cards to these colour labels (empty = all).
        'grey' matches uncoloured pictures — same convention as the culling
        grid's ColorFilterBar."""
        self._color_filter = {c.lower() for c in (colors or set())}

    @staticmethod
    def _row_color(row: dict) -> str:
        return (row.get("Label", "") or "grey").lower()

    @staticmethod
    def _row_camera(row: dict) -> str:
        return (row.get("CameraModel", "") or "").strip()

    @staticmethod
    def _camera_key(row: dict) -> str:
        """The value the camera FILTER matches on. A picture with no CameraModel gets
        the NO_CAMERA sentinel rather than "", so it can be selected — 1,421 master
        pictures (the waarneming.nl imports, whose source never recorded a body) have
        no camera, and with "" they matched nothing and the filter could not reach
        them at all."""
        return RatingEditorCollection._row_camera(row) or NO_CAMERA

    def set_camera_filter(self, models) -> None:
        """Restrict the shown cards to these camera bodies (empty = all).
        Matched exactly against each picture's CameraModel field, or NO_CAMERA for
        the pictures that never had one."""
        self._camera_filter = {m for m in (models or set()) if m}

    def set_sort(self, key: str) -> None:
        self._sort_key = (key if key in ("rate", "time_asc", "time_desc", "camera")
                          else "rate")

    def available_cameras(self, filter_name: str | None = None) -> list[str]:
        """Distinct camera bodies among the loaded pictures, for the filter combo.
        Scoped to `filter_name` (the selected species) when given, so the list
        only offers bodies actually present in what's on screen — a species shot
        only on the OM-1 won't also list the phone. Scans both the trip rows and
        the currently-loaded master rows, so it serves the Master Species Viewer
        (trip rows empty there) as well as the Trip viewer."""
        models = set()
        cameraless = False
        for source in (self.fpr.upn.UniquePictureName_dict, self._master_upn):
            for row in source.values():
                if filter_name and self.species_name(row) != filter_name:
                    continue
                m = self._row_camera(row)
                if m:
                    models.add(m)
                else:
                    cameraless = True
        out = sorted(models, key=str.casefold)
        if cameraless:
            out.append(NO_CAMERA)     # last: it is the leftovers bucket, not a body
        return out

    def set_species_lang(self, lang: str) -> None:
        self._species_lang = lang

    # ── PictureCollection ─────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._order)

    def item(self, index: int) -> RatingEditorItem:
        return RatingEditorItem(self, self._order[index])

    def index_of(self, key: str) -> int:
        return self._order.index(key)

    # ── map support (read-only plotting) ──────────────────────────────────────
    def all_keys(self) -> list[str]:
        return list(self._order)

    def species_keys(self) -> list[str]:
        # The viewer shows one species at a time, so everything plotted is that
        # species — the map's all/species scope buttons resolve to the same set.
        return list(self._order)

    def _files(self, upn_key: str) -> list[str]:
        files = self._upn_row(upn_key).get("Files", [])
        return files if isinstance(files, list) else []

    def gps_cached(self, upn_key: str):
        """(lat, lon) from ALREADY-LOADED rows — the UPN's GPSPosition, else a file
        row's Composite:GPSPosition — so the map plots a whole species without an
        exiftool spawn per picture. Master-aware (reads the master row for master
        reference keys). None when there's no in-memory coordinate."""
        from engine.geo import parse_gps_dms
        row = self._upn_row(upn_key)
        pos = (row.get("GPSPosition") or "").strip()
        if pos:
            ll = parse_gps_dms(pos)
            if ll:
                return ll
        files_dict = self._files_dict_for(upn_key)
        for fname in self._files(upn_key):
            frow = files_dict.get(fname)
            if frow:
                pos = (frow.get("Composite:GPSPosition") or "").strip()
                if pos:
                    ll = parse_gps_dms(pos)
                    if ll:
                        return ll
        return None

    def cancel_pending_loads(self) -> None:
        self._loader.cancel_all()

    # ── helpers shared with items ─────────────────────────────────────────
    def species_name(self, row: dict) -> str:
        from utils.name_profile import name_for
        return name_for(row, self._species_lang)

    def species_lines(self, row: dict) -> list:
        """The row's display names as (text, is_sci) lines for the active
        profile — the multi-line counterpart of species_name()."""
        from utils.name_profile import name_lines
        return name_lines(row, self._species_lang)

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
        baked = self._blog_jpgs.get(upn_key)
        if baked is not None and baked.exists():
            return baked
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

    def rep_full_path(self, upn_key: str) -> Path | None:
        """Representative RAW (else first non-XMP, else XMP) file Path for a TRIP
        UPN — the file SpeciesInfoPanel edits. None for master keys (read-only)."""
        if upn_key in self._master_keys:
            return None
        row = self.fpr.upn.UniquePictureName_dict.get(upn_key, {})
        files = row.get("Files", [])
        if not isinstance(files, list):
            return None
        fallback = None
        for fname in files:
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            p = Path(frow.get("WindowsFileName", ""))
            if not p.exists():
                continue
            if ("." + frow.get("WindowsExtension", "").lower()) in _RAW_EXTS:
                return p
            if fallback is None:
                fallback = p
        return fallback

    def species_editor_context(self, key: str):
        """Enable the large viewer's embedded species editor for TRIP pictures.
        Master reference pictures and whole-collection view-only mode (Album
        viewer) stay read-only, matching the grid's edit rules."""
        if self._view_read_only or key in self._master_keys:
            return None
        p = self.rep_full_path(key)
        return (self.fpr, [p]) if p else None

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
            cache_dir = preview_cache_dir_for(raw_path.parent)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / (raw_path.stem + ".jpg")
            if cache_path.exists():
                return cache_path
            # One shared, serialised exiftool — never a per-RAW process storm
            # (which intermittently yielded an empty preview → blank cards).
            from gui.preview_extract import extract_preview_to
            if extract_preview_to(raw_path, cache_path):
                return cache_path
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

    def self_describing_path(self, upn_key: str) -> Path | None:
        """Absolute path of the UPN's first HEIC/HEIF/PNG (master-aware).

        These are displayable pictures that are neither a JPG nor a RAW, so
        without this they fell through jpg_path -> _raw_thumb_path ->
        _video_thumb_path and the card read "(no image)" — a HEIC or a PNG simply
        had no display source in this viewer at all, however loadable the file was.
        Mirrors raw_path's master/trip path handling exactly."""
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
            if ("." + frow.get("WindowsExtension", "").lower()) not in SELF_DESCRIBING:
                continue
            if master and self._master_root is not None and rel_dir:
                p = self._master_root / rel_dir / fname
            else:
                p = Path(frow.get("WindowsFileName", ""))
            if p.exists():
                return p
        return None

    def _self_thumb_path(self, upn_key: str) -> Path | None:
        """Cached JPEG for the UPN's HEIC/PNG — the same cached_jpg_for() the
        culling grid and the day viewer use, into the same preview cache, so a
        picture decoded in one viewer is already decoded for the others."""
        s = self.self_describing_path(upn_key)
        if s is None:
            return None
        return cached_jpg_for(s, preview_cache_dir_for(s.parent))

    def video_path(self, upn_key: str) -> Path | None:
        """Absolute path of the UPN's movie file, if it has one — master-aware
        exactly like raw_path(). A day's videos come in as their own UPNs, so
        this is normally the UPN's only camera file."""
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
            if not is_video(frow.get("WindowsExtension", "")):
                continue
            if master and self._master_root is not None and rel_dir:
                p = self._master_root / rel_dir / fname
            else:
                p = Path(frow.get("WindowsFileName", ""))
            if p.exists():
                return p
        return None

    def _video_thumb_path(self, upn_key: str) -> Path | None:
        """Cached JPEG frame grabbed from the UPN's movie, or None. Goes through
        the same cached_jpg_for() the culling grid uses, into the same preview
        cache, so a movie decoded in one viewer is already decoded for the other
        — and the frame ages out under the same LRU as every other preview."""
        v = self.video_path(upn_key)
        if v is None:
            return None
        return cached_jpg_for(v, preview_cache_dir_for(v.parent))

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
        # Video LAST, and only once the stills have come up empty: a video UPN
        # has neither a JPG nor a RAW, and a mixed one should still show the
        # photograph. Without this a movie card was simply blank — the culling
        # grid grabbed a frame, this viewer did not (backlog: MOV in the
        # Species Viewer, 2026-09-23).
        # RAW, then a self-describing picture (HEIC/HEIF/PNG), then video last:
        # a UPN with any still at all shows the still, and a frame grab only
        # stands in for a picture that is otherwise blank.
        return (self._raw_thumb_path(upn_key)
                or self._self_thumb_path(upn_key)
                or self._video_thumb_path(upn_key))

    def display_path(self, upn_key: str) -> Path | None:
        """Synchronous best-display path (JPG or extracted RAW thumb) — used by
        the large view until its loading is made async (Phase 2)."""
        return self._image_path(upn_key)

    def request_image(self, upn_key: str, target_w, on_ready: PixmapCallback) -> None:
        def produce():
            p = self._image_path(upn_key)   # may extract RAW — off the GUI thread
            if not (p and p.exists()):
                return None
            img = QImage(str(p))
            if img.isNull():
                return None
            # When we're showing the RAW's own embedded preview (no JPG render
            # yet), overlay any PhotoLab/Lightroom crop so the composition matches
            # the edit — the same as the culling viewer. A JPG render is already
            # cropped, so it's shown as-is.
            if self.jpg_path(upn_key) is None:
                from gui.picture_provider import crop_for, apply_crop
                crop = crop_for(self.raw_path(upn_key))
                if crop:
                    img = apply_crop(img, crop)
            return img
        self._loader.request(produce, on_ready, target_w=target_w)

    def request_uncropped_image(self, upn_key: str, on_ready: PixmapCallback) -> None:
        """The RAW's full, UNCROPPED embedded preview — what the crop tool draws a
        fresh rectangle over (request_image would hand back the cropped render)."""
        def produce():
            p = self._raw_thumb_path(upn_key)   # may extract RAW — off the GUI thread
            if p and p.exists():
                img = QImage(str(p))
                return img if not img.isNull() else None
            return None
        self._loader.request(produce, on_ready, target_w=None)

    def set_crop(self, upn_key: str, crop: dict):
        """Write a suggested crop to a TRIP picture's DxO .dop / Lightroom .xmp so
        the next PhotoLab/Lightroom edit opens with it (creating a sidecar if
        missing). Returns sidecarlib's result dict, or None when there's no RAW.
        Master cards are read-only here."""
        if upn_key in self._master_keys:
            return None
        raw = self.raw_path(upn_key)
        if not raw:
            return None
        from engine.sidecarlib import suggest_crop
        res = suggest_crop(str(raw), crop)
        self._crop_flag_cache.pop(upn_key, None)   # crop marker must re-evaluate
        from gui.picture_provider import verify_crop_written
        verify_crop_written(raw, crop, res, who="RatingEditorCollection")
        return res

    def set_exposure(self, upn_key: str, exposure: float):
        """Write a suggested exposure (EV) to a TRIP picture's DxO .dop /
        Lightroom .xmp — the exposure mirror of set_crop. Master cards are
        read-only here. Returns sidecarlib's result dict, or None."""
        if upn_key in self._master_keys:
            return None
        raw = self.raw_path(upn_key)
        if not raw:
            return None
        from engine.sidecarlib import suggest_exposure
        return suggest_exposure(str(raw), exposure)

    def exposure_recorded(self, upn_key: str):
        """Current suggested exposure (EV) from the RAW's DxO .dop, or None.
        Mirrors crop_recorded's own (deliberately .dop-only, no .xmp fallback)
        lookup exactly, for consistency within this provider."""
        from gui.picture_provider import exposure_for
        return exposure_for(self.raw_path(upn_key))

    def crop_rect(self, upn_key: str):
        """The crop rectangle {left,top,width,height} (0-1) from the RAW's DxO
        .dop / Lightroom .xmp, or None — crop_recorded's readable half, read the
        same (.dop-preferred) way."""
        from gui.picture_provider import crop_for
        return crop_for(self.raw_path(upn_key))

    def crop_recorded(self, upn_key: str) -> bool:
        """True when this picture has a crop rectangle in its RAW's DxO .dop /
        Lightroom .xmp — the same crop request_image overlays, and the flag the
        card/filmstrip stamp a marker from. Known even when the card is showing
        the baked JPG (the crop lives in the RAW's sidecar, which the UPN still
        references). Cached per UPN; rebuild() and set_crop() invalidate it."""
        cache = self._crop_flag_cache
        if upn_key not in cache:
            from gui.picture_provider import crop_for, jpg_is_crop_by_size
            real = crop_for(self.raw_path(upn_key)) is not None
            if real:
                cache[upn_key] = True
            else:
                # No readable crop rectangle (a baked DxO render or an old master
                # JPG) — fall back to the size cue: a JPG much smaller than its RAW.
                ex = self.exif_for(upn_key)
                cache[upn_key] = jpg_is_crop_by_size(ex.dimensions, ex.jpg_dimensions)
        return cache[upn_key]

    def exif_for(self, upn_key: str) -> ExifSummary:
        upn = self._upn_row(upn_key)
        return exif_from_rows(upn, self._rep_file_row(upn_key))

    def create_blog_jpg(self, upn_key: str) -> dict:
        """Bake a website-ready JPG beside the RAW from the camera's embedded
        preview + the sidecar crop (NO scaling) — the quick trip-blog picture,
        same as the culling viewer's Create JPG. `<stem>.jpg` folds onto the UPN
        on the next Read & Summarize.

        Returns {'status': 'created'|'skipped'|'no_raw'|'failed', ...}. Skips a
        picture that already has a tracked JPG (camera / DxO) so nothing is
        clobbered; our own bake is untracked, so a re-run overwrites it (e.g.
        after re-cropping). Master reference cards are read-only here."""
        if upn_key in self._master_keys:
            return {"status": "skipped"}
        # A HEIC/PNG is as valid a source as a RAW (see preview_extract.bake_blog_jpg);
        # without the fallback Create JPG refused every phone picture as "no_raw".
        raw = self.raw_path(upn_key) or self.self_describing_path(upn_key)
        if raw is None:
            # Its only file IS a JPG: nothing to bake, and saying "no RAW"
            # about it was true and useless. Never an in-place resize — a derived
            # file must not overwrite its source, and <stem>.jpg IS the source here
            # (Windows ignores the case difference).
            j = self.jpg_path(upn_key)
            if j is not None:
                return {"status": "already_jpg", "jpg": str(j)}
            return {"status": "no_raw"}
        if self.jpg_path(upn_key) is not None and upn_key not in self._blog_jpgs:
            return {"status": "skipped", "jpg": str(self.jpg_path(upn_key))}
        out = raw.with_suffix(".jpg")
        from gui.preview_extract import bake_blog_jpg
        from gui.picture_provider import crop_for
        xmp = raw.with_suffix(".xmp")
        crop = crop_for(raw, xmp if xmp.exists() else None)
        try:
            baked_size = bake_blog_jpg(raw, out, crop)
        except Exception as e:
            logger.warning(f"Create JPG failed for {raw.name}: {e}")
            return {"status": "failed", "reason": str(e)}
        self._blog_jpgs[upn_key] = out
        self._crop_flag_cache.pop(upn_key, None)   # image is now the baked JPG
        logger.info(f"Blog JPG created → {out.name} "
                    f"({baked_size[0]}×{baked_size[1]}, "
                    f"{'cropped' if crop else 'full frame'}).")
        return {"status": "created", "jpg": str(out), "size": baked_size,
                "cropped": bool(crop)}

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
        any_landed = False      # did the value reach at least one file?
        had_target = False      # was there any file we could have written to?
        raw_path = None
        for fname in files:
            frow = self.fpr.fi.files_dict.get(fname)
            if not frow:
                continue
            ext = frow.get("WindowsExtension", "").lower()
            if ext in _DXO_RAW_EXTS and raw_path is None:
                raw_path = frow.get("WindowsFileName", "")  # for the .dop write
            if ext not in ("xmp", "jpg", "jpeg"):
                continue
            fpath = Path(frow.get("WindowsFileName", ""))
            if not fpath.exists():
                continue
            had_target = True
            args = []
            if new_stars is not None:
                args.append(f"-XMP:Rating={new_stars}")
            if new_color is not None:
                args.append(f"-XMP:Label={new_color}")
            # Colour/rating in the .xmp can be suppressed (Sidecar Behaviour) since
            # PhotoLab mistreats it; internal state below still updates.
            suppress = ext == "xmp" and not color_rating_to_xmp_enabled()
            landed = True
            if args and not suppress:
                try:
                    exiftool_write(fpath, args)
                    wrote_file = True
                except Exception as e:
                    logger.error(f"exiftool write failed for {fpath.name}: {e}")
                    landed = False
            # Only record what actually reached the file. Updating the row anyway
            # is what let the CSVs claim a colour the sidecar never got: the row
            # is flushed wholesale by write_project_files, so one later success
            # persisted every earlier failure too, and the grid then showed a
            # colour that PhotoLab, Lightroom and the next Read & Summarize could
            # not see. A suppressed write (Sidecar Behaviour) is not a failure —
            # the value is deliberately kept in the index only.
            if not landed:
                continue
            if new_stars is not None:
                frow["XMP:Rating"] = str(new_stars)
            if new_color is not None:
                frow["XMP:Label"] = new_color
            any_landed = True
        # Colour/rating into the DxO .dop (PhotoLab reads it on 'import sidecars').
        if raw_path and color_rating_to_dop_enabled():
            push_metadata_to_dop(raw_path, color=new_color, rating=new_stars)

        # Same rule for the picture row. `had_target and not any_landed` is the
        # one case we refuse: there WAS a file to write and the write failed, so
        # recording the value here would put it in data_pictures.csv — and the
        # Trip scope reads that table, which is how "it shows in Day and is gone
        # in Trip" looked like a viewer bug rather than a lost write.
        if had_target and not any_landed:
            logger.error(
                f"{upn_key}: colour/rating not written to any file — leaving the "
                f"index unchanged rather than recording a value the sidecars "
                f"do not have.")
            return _compute_rate(upn_row, self.fpr.fi.files_dict)

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

    def persist_after_species_change(self, upn_keys) -> None:
        """Write the CSVs for a set of UPNs after their species was changed via
        fpr.updateSubjects (Set-species / Move-to-species). In Trip scope fpr.fi /
        fpr.upn are merged, path-less objects, so updateSubjects' own CSV write is
        a no-op there — the real files are the per-day objects, which share the
        same row dicts (so they already reflect the edit). Persist each affected
        day once. In Day scope fpr IS the single day's object with a real path, so
        persist it directly."""
        if self._day_objects is None:
            try:
                self.fpr.upn.write_project_summary()
                self.fpr.fi.write_project_files()
            except Exception as e:
                logger.error(f"persist_after_species_change (day scope) failed: {e}")
            return
        days = {self._upn_day_map.get(k) for k in upn_keys}
        days.discard(None)
        for day in days:
            if day not in self._day_objects:
                continue
            fi_day, upn_day = self._day_objects[day]
            try:
                upn_day.write_project_summary()
                fi_day.write_project_files()
            except Exception as e:
                logger.error(f"persist_after_species_change failed for {day}: {e}")

    # ── add newly-rendered files to a trip picture (scan its folder) ──────────
    def add_trip_files(self, upn_key: str) -> dict:
        """Scan a TRIP picture's folder for files that belong to its UPN but the
        trip doesn't track yet — the typical case being a DxO render dropped
        beside the RAW (`..._DxO.jpg`, and its `.dop` if not already tracked) —
        add them to the day's data_files / data_pictures, re-distil the picture's
        BestJPG / JPGSize / Rate, and persist. This is the trip-side sibling of
        the master's add_upn_files (which writes the master DB); here the day CSVs
        are the store. Master reference cards go through add_master_files, so this
        no-ops on a master key.

        Returns {"added": [...], "jpg": n, "meta": n}; "added" empty when the
        folder held nothing new.
        """
        if upn_key in self._master_keys:
            return {"added": [], "jpg": 0, "meta": 0}
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key)
        if not upn_row:
            return {"added": [], "jpg": 0, "meta": 0}
        folder = self.file_directory(upn_key)
        if not folder:
            return {"added": [], "jpg": 0, "meta": 0}
        folder_p = Path(folder)
        if not folder_p.exists():
            return {"added": [], "jpg": 0, "meta": 0}

        from db.master_mutate import _belongs_to_upn, _read_exif_row

        files_list = upn_row.get("Files")
        if not isinstance(files_list, list):
            files_list = []
            upn_row["Files"] = files_list
        # "Tracked" = a file row already exists (case-insensitive) — the same
        # truth the master's add uses; guards against re-adding the RAW/.xmp/.dop
        # the day already has, whatever their disk casing.
        tracked = {f.lower() for f in self.fpr.fi.files_dict}
        tracked |= {f.lower() for f in files_list}

        # Which day's CSV objects to write the new file rows into. In Day scope
        # _day_objects is None and fpr.fi IS the single day — nothing extra to do.
        day = self._upn_day_map.get(upn_key)
        day_fi = (self._day_objects[day][0]
                  if self._day_objects and day in self._day_objects else None)

        added: list[str] = []
        for p in sorted(folder_p.iterdir()):
            if not p.is_file():
                continue
            if not _belongs_to_upn(p.name, upn_key):
                continue
            if p.name.lower() in tracked:
                continue
            # master_root / rel are unused by _read_exif_row (it stores the file's
            # own absolute path); pass the folder / "" to satisfy the signature.
            row = _read_exif_row(p, folder_p, "", upn_key)
            self.fpr.fi.files_dict[p.name] = row
            if day_fi is not None:
                day_fi.files_dict[p.name] = row
            files_list.append(p.name)
            tracked.add(p.name.lower())
            added.append(p.name)

        if not added:
            return {"added": [], "jpg": 0, "meta": 0}

        self._redistill_upn(upn_key)
        self._persist_upn(upn_key, wrote_file=True)
        self._crop_flag_cache.pop(upn_key, None)   # a new render/crop may have arrived

        jpgs = [n for n in added if n.rsplit(".", 1)[-1].lower() in ("jpg", "jpeg")]
        metas = [n for n in added if n not in jpgs]
        logger.info(f"trip add: {upn_key} gained {len(jpgs)} JPG(s) + "
                    f"{len(metas)} metafile(s): {added}")
        return {"added": added, "jpg": len(jpgs), "meta": len(metas)}

    def _redistill_upn(self, upn_key: str) -> None:
        """Recompute the fields a newly-added JPG affects: BestJPG /
        BestJPGPathName (what the card displays), JPGSize, and Rate — the slice of
        projectlib.UpdatePicture / computeRating the trip viewer reads. The RAW's
        ImageSize is the crop-reward baseline, so it's only filled when absent (a
        JPG-only picture), never overwritten with the smaller render's size."""
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key)
        if not upn_row:
            return
        files_dict = self.fpr.fi.files_dict
        files = upn_row.get("Files", [])
        if not isinstance(files, list):
            files = []

        # Best JPG = the largest real render (never a '-small' web thumbnail);
        # tie-break the longer filename — PureRaw/DxO output has the longer name
        # and is preferred over a plain Lightroom export (mirrors upnBestJPG).
        best_name, best_path, best_area, best_size = "", "", -1, ""
        for fname in files:
            frow = files_dict.get(fname)
            if not frow or frow.get("WindowsExtension", "").lower() not in ("jpg", "jpeg"):
                continue
            if "-small" in fname.lower():
                continue
            size = frow.get("ImageSize", "") or ""
            try:
                w, h = re.split(r"[x ]", size)[:2]
                area = int(w) * int(h)
            except Exception:
                area = -1
            if area > best_area or (area == best_area and len(fname) > len(best_name)):
                best_area, best_name, best_size = area, fname, size
                best_path = frow.get("WindowsFileName", "")
        if best_name:
            upn_row["BestJPG"] = best_name
            upn_row["BestJPGPathName"] = best_path
            upn_row["JPGSize"] = best_size
            if not (upn_row.get("ImageSize") or "").strip():
                upn_row["ImageSize"] = best_size
        upn_row["Rate"] = str(_compute_rate(upn_row, files_dict))

    # ── large-viewer hook: right-click "Add new files (scan folder)" ──────────
    def add_files_supported(self, key: str) -> bool:
        """The large viewer offers "Add new files" only for an editable TRIP
        picture — never a master reference card or a read-only (Album) view. The
        Master Species Viewer keeps its own add on the card context menu."""
        return ((not self.view_read_only) and (key not in self._master_keys)
                and not self.is_frozen_day(key))

    def add_files(self, key: str) -> dict:
        """Large-viewer entry point → the trip-side folder scan/add. Returns
        {"added", "jpg", "meta"}; "added" empty when nothing new was found."""
        if key in self._master_keys:
            return {"added": [], "jpg": 0, "meta": 0}
        return self.add_trip_files(key)

    def apply_description(self, upn_key: str, value: str) -> None:
        if upn_key in self._master_keys:
            if not self._master_read_only:
                self._apply_master_description(upn_key, value)
            return
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key)
        if not upn_row:
            return
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
            try:
                exiftool_write(fpath, [f"-XMP:Description={value}"])
                wrote_file = True
            except Exception as e:
                logger.error(f"exiftool write failed for {fpath.name}: {e}")
            frow["XMP:Description"] = value
        upn_row["Description"] = value
        self._persist_upn(upn_key, wrote_file)

    def apply_headline(self, upn_key: str, value: str) -> None:
        """Write XMP:Headline (the landscape / context-photo title) to the trip
        picture's sidecar + JPGs, mirroring apply_description. A blog extra never
        reaches the master, so master keys have no headline — no-op there."""
        if upn_key in self._master_keys:
            return
        upn_row = self.fpr.upn.UniquePictureName_dict.get(upn_key)
        if not upn_row:
            return
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
            try:
                exiftool_write(fpath, [f"-XMP:Headline={value}"])
                wrote_file = True
            except Exception as e:
                logger.error(f"exiftool write failed for {fpath.name}: {e}")
            frow["XMP:Headline"] = value
        upn_row["Headline"] = value
        self._persist_upn(upn_key, wrote_file)

    def delete_by_color(self, color: str, trash_root: Path,
                        day_hint: str = "", progress_cb=None) -> tuple[int, int, int]:
        """Move all trip UPNs with the given color label to trash.

        Returns (total_matching, protected_count, deleted_count).
        Skips master keys and JPG-bearing UPNs (HARD INVARIANT).
        trash_root should be <trip_root>/.trash; files land at
        trash_root/<day>/<key>/<file> with day from _upn_day_map or day_hint.
        """
        matching, _ = self.color_matches(color)
        return self.delete_trip_keys(matching, trash_root, day_hint, progress_cb)

    def color_matches(self, color: str, species: str | None = None,
                      day: str | None = None) -> tuple[list[str], list[str]]:
        """UPNs labelled `color`, split into (trip_keys, master_keys).

        Scoping (WP: species-view delete): when `species` is given, only that
        species' pictures match. `day` only gates master inclusion — the trip
        set is already day-limited by what day scope loads (readTripDay loads a
        single day and leaves _upn_day_map empty), so it must NOT be re-filtered
        here. Master pictures are included only outside day scope, and are
        already limited to the loaded species (see rebuild) — so a species view
        can blue-delete master pictures too. With neither, it is trip-wide (the
        culling default).

        An ALBUM passes day=None even in Day scope: there the master pictures
        are the day (set_master_scope already narrowed them), so the gate would
        exclude exactly what the user is looking at."""
        wanted = (color or "").lower()
        trip_keys = []
        for k, row in self.fpr.upn.UniquePictureName_dict.items():
            if (row.get("Label", "") or "").lower() != wanted:
                continue
            if k in self._master_keys:
                continue
            if species and self.species_name(row) != species:
                continue
            trip_keys.append(k)
        master_keys = []
        if not day:
            for k in self._master_keys:
                row = self._master_upn.get(k, {})
                if (row.get("Label", "") or "").lower() == wanted:
                    master_keys.append(k)
        return trip_keys, master_keys

    def delete_trip_keys(self, keys, trash_root: Path,
                         day_hint: str = "", progress_cb=None) -> tuple[int, int, int]:
        """Move the given TRIP UPNs to the trip trash. Returns
        (total, protected, deleted). JPG-bearing UPNs are skipped (HARD
        INVARIANT). Master keys must NOT be passed here — delete those via
        delete_master_picture.

        `progress_cb(done, total)`, if given, is called after every key — same
        contract as PreviewCollection.delete_keys. Returning False stops the
        loop early; work already moved stays moved."""
        import shutil
        matching = list(keys)
        protected = 0
        deleted = 0
        srcs = []
        total = len(matching)
        days_to_persist: set[str] = set()
        for i, key in enumerate(matching):
            if self.jpg_path(key):
                protected += 1
                if progress_cb and progress_cb(i + 1, total) is False:
                    break
                continue
            if self.is_frozen_day(key):
                # A committed day is immutable — never trash its files (the caller
                # already filters, this is the last-line guard). See day_freeze.py.
                protected += 1
                if progress_cb and progress_cb(i + 1, total) is False:
                    break
                continue
            upn_row = self.fpr.upn.UniquePictureName_dict.get(key, {})
            files = upn_row.get("Files", [])
            if not isinstance(files, list):
                files = []
            day = self._upn_day_map.get(key) or day_hint or "unknown"
            upn_dir = trash_root / day / key
            upn_dir.mkdir(parents=True, exist_ok=True)
            raw_src = None
            for fname in files:
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
                # Also clean the per-day files dict (shared reference won't auto-reflect pops)
                if self._day_objects and day in self._day_objects:
                    self._day_objects[day][0].files_dict.pop(fname, None)
            sweep_dop(raw_src, upn_dir)
            self.fpr.upn.UniquePictureName_dict.pop(key, None)
            if self._day_objects and day in self._day_objects:
                self._day_objects[day][1].UniquePictureName_dict.pop(key, None)
            if self._day_objects and day in self._day_objects:
                days_to_persist.add(day)
            deleted += 1
            if progress_cb and progress_cb(i + 1, total) is False:
                break
        from utils.preview_cache import drop_for_files
        drop_for_files(srcs)     # the RAWs are gone; their cached previews are junk
        for day in days_to_persist:
            fi_day, upn_day = self._day_objects[day]
            try:
                upn_day.write_project_summary()
            except Exception as e:
                logger.error(f"delete_by_color: pictures CSV write failed for {day}: {e}")
            try:
                fi_day.write_project_files()
            except Exception as e:
                logger.error(f"delete_by_color: files CSV write failed for {day}: {e}")
        if not days_to_persist and deleted:
            # Day scope — fpr.fi / fpr.upn ARE the single day's objects
            try:
                self.fpr.upn.write_project_summary()
                self.fpr.fi.write_project_files()
            except Exception as e:
                logger.error(f"delete_by_color: CSV write failed (day scope): {e}")
        return len(matching), protected, deleted

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
        raw_path = None
        for fname in files:
            frow = self._master_files.get(fname)
            if not frow:
                continue
            ext = frow.get("WindowsExtension", "").lower()
            if ext in _DXO_RAW_EXTS and raw_path is None and rel_dir:
                raw_path = str(self._master_root / rel_dir / fname)  # for the .dop write
            if ext not in ("xmp", "jpg", "jpeg"):
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
            # Colour/rating in the .xmp can be suppressed (Sidecar Behaviour) since
            # PhotoLab mistreats it; the CSV/DB update below still records the value.
            suppress = ext == "xmp" and not color_rating_to_xmp_enabled()
            if not suppress:
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
        # Colour/rating into the DxO .dop (PhotoLab reads it on 'import sidecars').
        if raw_path and color_rating_to_dop_enabled():
            push_metadata_to_dop(raw_path, color=new_color, rating=new_stars)

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

    def _apply_master_description(self, upn_key: str, value: str) -> None:
        """Write a master picture's website description to its XMP/JPG files, then
        record it durably via MasterDB.apply_description. Mirrors
        _apply_master_rating; master file paths are RELATIVE → rebuilt from
        master_root. Publishing re-reads Description at render, so no rollup."""
        upn_row = self._master_upn.get(upn_key)
        if not upn_row or self._master_db is None or self._master_root is None:
            return
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
            try:
                exiftool_write(fpath, [f"-XMP:Description={value}"])
            except Exception as e:
                logger.error(f"master description write failed for {fpath.name}: {e}")
                continue
            frow["XMP:Description"] = value
            file_updates[fname] = {"XMP:Description": value}
        upn_row["Description"] = value
        try:
            self._master_db.apply_description(upn_key, value, file_updates=file_updates)
        except Exception as e:
            logger.error(f"master DB description write failed for {upn_key}: {e}",
                         exc_info=True)
