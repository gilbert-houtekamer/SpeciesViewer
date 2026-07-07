# utils/trip_identity.py
#
# One resolver for the three identities a (virt)trip carries, so nothing has to
# re-derive a key from "whichever identifier the code path happened to pick":
#
#   trip_id   — the on-disk repo basename (e.g. spanjeebrodb). Stable internal id.
#   web_slug  — the URL segment + master trip_admin.csv key (e.g. ebro → /trips/ebro).
#   title_nl / title_en — the display title.
#
# Plus, for a virttrip, an explicit link back to the trip it was retired from:
#
#   source_trip_id — the origin trip's trip_id (e.g. spanjeebrodb).
#
# The ebro bug is exactly the case trip_id != web_slug. When the explicit keys
# are absent (every trip created before WP4) the fallbacks reproduce today's
# behaviour byte-for-byte — trip_id from the folder basename, web_slug from
# trip_name — so old trips resolve unchanged (lazy migration).

import os
from dataclasses import dataclass

from utils.trip_config import read_trip_ini
from utils.virttrip_config import read_virttrip_ini


import re as _re

_DAY_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _slug_of(trip_name: str) -> str:
    """A bare web slug from a trip_name that may be a bare slug ('ebro') or,
    occasionally, carry a '/trips/' prefix. Mirrors the derivation in
    retire_trip_to_virttrip."""
    return (trip_name or "").strip("/").rsplit("/", 1)[-1]


def _has_day_subdirs(base: str) -> bool:
    """True if `base` directly contains YYYY-MM-DD folders — i.e. it is a trip's
    picture root, where each day's blog.ini lives."""
    try:
        return os.path.isdir(base) and any(
            _DAY_RE.match(d) and os.path.isdir(os.path.join(base, d))
            for d in os.listdir(base))
    except OSError:
        return False


@dataclass
class TripIdentity:
    root: str                 # the (virt)trip folder on disk
    kind: str                 # "trip" | "virttrip"
    trip_id: str              # folder-basename identity (explicit key or derived)
    web_slug: str             # URL / master-registry key (explicit key or derived)
    title_nl: str = ""
    title_en: str = ""
    source_trip_id: str = ""  # virttrip → its origin trip's trip_id ("" if unknown)
    picture_root: str = ""    # WP8: the trip's source-picture folder, RELATIVE to
                              # the per-machine CurrentPictures root ("" if unset)

    @property
    def web_root(self) -> str:
        """The canonical /trips/<slug> web root for this (virt)trip."""
        return f"/trips/{self.web_slug}" if self.web_slug else ""


def _resolve_from_dict(root: str, kind: str, data: dict) -> TripIdentity:
    data = data or {}
    basename = os.path.basename(os.path.normpath(root)) if root else ""
    trip_id = (data.get("trip_id") or "").strip() or basename
    web_slug = (data.get("web_slug") or "").strip() or _slug_of(data.get("trip_name", ""))
    return TripIdentity(
        root=root,
        kind=kind,
        trip_id=trip_id,
        web_slug=web_slug,
        title_nl=data.get("title_nl", ""),
        title_en=data.get("title_en", ""),
        source_trip_id=(data.get("source_trip_id") or "").strip(),
        picture_root=(data.get("picture_root") or "").strip(),
    )


def resolve(root: str, kind: str = None) -> TripIdentity:
    """Resolve the identity of the (virt)trip rooted at `root`.

    `kind` is "trip" or "virttrip"; if omitted it is detected from which ini
    file is present (virttrip.ini wins, since a virttrip folder never also holds
    a trip.ini). Falls back to a folder-basename identity when no ini is found,
    so callers always get a usable object."""
    if kind is None:
        if root and os.path.isfile(os.path.join(root, "virttrip.ini")):
            kind = "virttrip"
        else:
            kind = "trip"
    data = read_virttrip_ini(root) if kind == "virttrip" else read_trip_ini(root)
    return _resolve_from_dict(root, kind, data)


def find_linked_virttrip(trip_id: str, virttrips_root: str):
    """Return the virttrip folder whose explicit source_trip_id == trip_id, or
    None. This is the authoritative trip→virttrip matcher: it works even when
    the virttrip folder name differs from the trip's (the ebro case). Callers
    should fall back to the basename convention only when this returns None
    (legacy virttrips written before WP4 carry no source_trip_id)."""
    if not (trip_id and virttrips_root and os.path.isdir(virttrips_root)):
        return None
    for name in sorted(os.listdir(virttrips_root)):
        cand = os.path.join(virttrips_root, name)
        if not os.path.isfile(os.path.join(cand, "virttrip.ini")):
            continue
        ident = resolve(cand, kind="virttrip")
        if ident.source_trip_id and ident.source_trip_id == trip_id:
            return cand
    return None


def resolve_trip_picture_root(root: str, kind: str = None,
                              active_picturedirectory: str = None) -> str:
    """Absolute path to the trip's picture root — the folder that holds its
    YYYY-MM-DD day subdirs (WP8). This replaces the scattered
    `Path(picturedirectory).parent` derivations with one explicit resolution:

      1. trip.ini `picture_root`, joined onto the per-machine CurrentPictures
         root when it is relative (the portable, stored-once case);
      2. `picture_root` as-is if it was stored absolute (edge/back-compat);
      3. FALLBACK — the parent of the active [project] picturedirectory (a day
         folder), i.e. exactly today's behaviour, so un-migrated trips and
         days-mode are unaffected.

    Returns "" when nothing resolves."""
    ident = resolve(root, kind) if root else None
    pr = ident.picture_root if ident else ""
    if pr:
        if os.path.isabs(pr):
            return os.path.normpath(pr)
        from utils.config import get_currentpictures_root
        base = get_currentpictures_root()
        if base:
            return os.path.normpath(os.path.join(base, pr.replace("/", os.sep)))
        # relative but no base configured → fall through to the day-parent fallback

    if active_picturedirectory is None:
        from utils.config import _config
        active_picturedirectory = _config.get("project", "picturedirectory", fallback="")
    active_picturedirectory = (active_picturedirectory or "").strip().strip('"').strip("'")
    if active_picturedirectory:
        cand = os.path.normpath(active_picturedirectory)
        for base_c in (cand, os.path.dirname(cand)):
            if _has_day_subdirs(base_c):
                return base_c
        return os.path.dirname(cand)
    return ""


def to_stored_picture_root(abs_root: str, currentpictures_root: str):
    """Turn an absolute trip picture root into the value to store in trip.ini.

    Returns (stored_str, is_relative). Prefers a forward-slashed path RELATIVE
    to the per-machine CurrentPictures root (portable across machines). Falls
    back to the absolute path when the root is outside the configured base (or
    no base is configured) — callers should warn in that case."""
    abs_root = os.path.normpath(abs_root)
    if currentpictures_root:
        base = os.path.normpath(currentpictures_root)
        try:
            rel = os.path.relpath(abs_root, base)
        except ValueError:
            rel = None  # different drive on Windows
        if rel is not None and not rel.startswith(".."):
            return rel.replace(os.sep, "/"), True
    return abs_root, False
