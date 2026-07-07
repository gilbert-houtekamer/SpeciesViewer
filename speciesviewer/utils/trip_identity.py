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


def _slug_of(trip_name: str) -> str:
    """A bare web slug from a trip_name that may be a bare slug ('ebro') or,
    occasionally, carry a '/trips/' prefix. Mirrors the derivation in
    retire_trip_to_virttrip."""
    return (trip_name or "").strip("/").rsplit("/", 1)[-1]


@dataclass
class TripIdentity:
    root: str                 # the (virt)trip folder on disk
    kind: str                 # "trip" | "virttrip"
    trip_id: str              # folder-basename identity (explicit key or derived)
    web_slug: str             # URL / master-registry key (explicit key or derived)
    title_nl: str = ""
    title_en: str = ""
    source_trip_id: str = ""  # virttrip → its origin trip's trip_id ("" if unknown)

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
