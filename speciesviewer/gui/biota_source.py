"""Biota tree sources — one uniform walk over the taxonomic tree per scope.

The Biota Browser (gui/biota_browser.py) is a VIEW; a BiotaSource is its MODEL.
Trip / Album and Master all store the identical tree — Level / Up / Leaves rooted
at 'biota' — so the browser code never branches on scope: it asks a source for a
node's children, breadcrumb chain, counts, cover thumbnail, and (at a leaf) the
species' pictures, and the source knows where those come from.

Only MasterBiotaSource is implemented today (the Master is the cleanest tree —
one SQLite table with an Up index and a per-node representative cover). A
TripBiotaSource wrapping Project.sp.species_dict is the next scope; it will expose
the same surface, so the browser is untouched when it lands.

Node dict (what the browser consumes):
    { "sci", "rank", "level", "nl", "en", "up", "target_path" }
`rank` is the short label ("SPECIES", "FAMILY", …); `level` is the raw
"Level.SPECIES" string as stored.
"""

import logging
from pathlib import Path

logger = logging.getLogger("speciesviewer")

ROOT = "biota"

# rank order, coarsest→finest, for the level filter and search sort
RANK_ORDER = ["KINGDOM", "PHYLUM", "CLASS", "ORDER", "FAMILY", "SPECIES"]
RANK_LABEL = {"BIOTA": "Biota", "KINGDOM": "Kingdom", "PHYLUM": "Phylum",
              "CLASS": "Class", "ORDER": "Order", "FAMILY": "Family",
              "SPECIES": "Species"}

# RAW extensions whose embedded preview can stand in for a cover (mirrors
# gui.rating_provider._RAW_EXTS, kept local so the model needs no GUI import).
_RAW_EXTS = {".nef", ".orf", ".cr2", ".cr3", ".arw", ".rw2", ".raf", ".dng", ".pef"}


def _rank_of(level: str) -> str:
    """'Level.SPECIES' -> 'SPECIES'."""
    return (level or "").split(".")[-1].upper()


class MasterBiotaSource:
    """Walks the master's biota tree, counting only what actually has pictures.

    Loaded once from two cheap queries: the whole tree (taxa_rows) and the
    per-species picture tally (species_picker_rows). Picture and species counts
    are aggregated UP the tree from the leaves that truly have master pictures —
    so an empty taxon never shows, exactly like the website's render gate — rather
    than trusting the species table's cached counters, which can lag the pictures.
    """

    def __init__(self, db, master_root):
        self.db = db
        self.master_root = Path(master_root) if master_root else None
        self.scope_label = "Master collection"
        self._by_sci = {}          # sci_lower -> node dict
        self._kids = {}            # sci_lower -> [child sci_lower, …]
        self._pics = {}            # sci_lower -> aggregated picture count
        self._species_n = {}       # sci_lower -> aggregated species count
        self._own_pics = {}        # sci_lower -> pictures on THIS species only
        self._cover = {}           # sci_lower -> resolved cover Path | None (memo)
        self._cover_spec = {}      # sci_lower -> ("jpg"|"raw", Path) | None (memo)
        self._load()

    # ── build ───────────────────────────────────────────────────────────────
    def _load(self):
        rows = self.db.taxa_rows()
        for r in rows:
            key = (r["sci"] or "").strip().lower()
            if not key:
                continue
            r["rank"] = _rank_of(r["level"])
            self._by_sci[key] = r
        # children from Up (more reliable than the Leaves list), taxa-order sorted
        for key, r in self._by_sci.items():
            up = (r["up"] or "").strip().lower()
            if up and up in self._by_sci:
                self._kids.setdefault(up, []).append(key)
        for up, kids in self._kids.items():
            kids.sort(key=lambda k: (self._by_sci[k]["target_path"].lower(),
                                     self._by_sci[k]["sci"].lower()))

        # ground-truth picture counts, per species that has master pictures
        for row in self.db.species_picker_rows():
            key = (row["sci"] or "").strip().lower()
            if key in self._by_sci:
                self._own_pics[key] = row["n"]

        # aggregate counts up every ancestor chain (one walk per photographed leaf)
        for key, n in self._own_pics.items():
            self._pics[key] = self._pics.get(key, 0) + n
            self._species_n[key] = self._species_n.get(key, 0) + 1
            up = (self._by_sci[key]["up"] or "").strip().lower()
            seen = {key}
            while up and up in self._by_sci and up not in seen:
                seen.add(up)
                self._pics[up] = self._pics.get(up, 0) + n
                self._species_n[up] = self._species_n.get(up, 0) + 1
                up = (self._by_sci[up]["up"] or "").strip().lower()

    # ── tree walk ───────────────────────────────────────────────────────────
    def root(self) -> str:
        return ROOT if ROOT in self._by_sci else next(iter(self._by_sci), ROOT)

    def node(self, sci: str) -> dict | None:
        return self._by_sci.get((sci or "").strip().lower())

    def is_species(self, sci: str) -> bool:
        n = self.node(sci)
        return bool(n and n["rank"] == "SPECIES")

    def children(self, sci: str) -> list[dict]:
        """Child nodes that have at least one master picture, in taxonomic order."""
        key = (sci or "").strip().lower()
        return [self._by_sci[c] for c in self._kids.get(key, [])
                if self._pics.get(c, 0) > 0]

    def chain(self, sci: str) -> list[dict]:
        """[root … sci] — the breadcrumb, walked via Up."""
        out, key, seen = [], (sci or "").strip().lower(), set()
        while key and key in self._by_sci and key not in seen:
            seen.add(key)
            out.append(self._by_sci[key])
            key = (self._by_sci[key]["up"] or "").strip().lower()
        out.reverse()
        return out

    def counts(self, sci: str) -> tuple[int, int]:
        """(species with pictures, total pictures) under this node."""
        key = (sci or "").strip().lower()
        return self._species_n.get(key, 0), self._pics.get(key, 0)

    # ── representative cover ────────────────────────────────────────────────
    def cover_path(self, sci: str) -> Path | None:
        """The node's own BestJPG if it resolves on disk; otherwise the cover of
        its best-rated photographed descendant. Memoized — a full-tree browse only
        stats each cover once."""
        key = (sci or "").strip().lower()
        if key in self._cover:
            return self._cover[key]
        self._cover[key] = self._resolve_cover(key, set())
        return self._cover[key]

    def _resolve_cover(self, key: str, seen: set) -> Path | None:
        if not key or key in seen or key not in self._by_sci or self.master_root is None:
            return None
        seen.add(key)
        n = self._by_sci[key]
        best_jpg, best_dir = n["best_jpg"], n["best_dir"]
        if best_jpg and best_dir:
            p = self.master_root / best_dir / best_jpg
            if p.suffix.lower() in (".jpg", ".jpeg") and p.exists():
                return p
        # fall back to the best photographed child (highest currentHighestRating,
        # then most pictures) — recurse until a real JPG turns up
        kids = [c for c in self._kids.get(key, []) if self._pics.get(c, 0) > 0]
        kids.sort(key=lambda c: (-int(self._by_sci[c]["rate"] or 0),
                                 -self._pics.get(c, 0)))
        for c in kids:
            p = self._resolve_cover(c, seen)
            if p is not None:
                return p
        return None

    # cover as a (kind, Path) spec — kind "jpg" or "raw". Falls back to the best
    # picture's RAW when a node (common on an unprocessed trip) has no JPG anywhere,
    # so the caller can extract its embedded preview instead of showing a blank.
    def cover_spec(self, sci: str):
        key = (sci or "").strip().lower()
        if key in self._cover_spec:
            return self._cover_spec[key]
        self._cover_spec[key] = self._resolve_cover_spec(key, set())
        return self._cover_spec[key]

    def _resolve_cover_spec(self, key: str, seen: set):
        if not key or key in seen or key not in self._by_sci or self.master_root is None:
            return None
        seen.add(key)
        n = self._by_sci[key]
        # 1. a real JPG on this node
        if n["best_jpg"] and n["best_dir"]:
            p = self.master_root / n["best_dir"] / n["best_jpg"]
            if p.suffix.lower() in (".jpg", ".jpeg") and p.exists():
                return ("jpg", p)
        # 2. a species with no JPG → its best picture's RAW (for a preview)
        if n["rank"] == "SPECIES":
            raw = self._species_raw_cover(key, n["target_path"] or n["best_dir"])
            if raw is not None:
                return ("raw", raw)
        # 3. best photographed child, recurse
        kids = [c for c in self._kids.get(key, []) if self._pics.get(c, 0) > 0]
        kids.sort(key=lambda c: (-int(self._by_sci[c]["rate"] or 0),
                                 -self._pics.get(c, 0)))
        for c in kids:
            r = self._resolve_cover_spec(c, seen)
            if r is not None:
                return r
        return None

    def _species_raw_cover(self, key: str, target_path: str):
        """A RAW file of this species that actually exists on disk, preferring the
        best-rated picture. A processed picture is filed under the repo's biota/
        tree (root/TargetPath/<raw>); an UNPROCESSED one (no JPG, no folder yet —
        common on a working trip) lives at its ingest location, an absolute
        WindowsFileName. Some rows carry a STALE repo path that was never copied,
        so we must scan all the species' RAWs for one that resolves, not just the
        single best pic's."""
        pics = self.db.pictures_for_species(key)
        if not pics:
            return None
        upns = [p.get("UniquePictureName") for p in pics if p.get("UniquePictureName")]
        if not upns:
            return None
        try:
            frows = self.db.files_for_upns(upns)   # {File:FileName: row}, chunked
        except Exception:
            return None
        # upn -> first RAW of that picture that exists on disk
        raw_by_upn = {}
        for fname, frow in frows.items():
            ext = "." + ((frow.get("WindowsExtension") or "").lower())
            if ext not in _RAW_EXTS:
                continue
            upn = frow.get("UniquePictureName")
            if not upn or upn in raw_by_upn:
                continue
            found = None
            if target_path:
                cand = self.master_root / target_path / fname
                if cand.exists():
                    found = cand
            if found is None:
                wf = frow.get("WindowsFileName") or ""
                if wf and Path(wf).exists():
                    found = Path(wf)
            if found is not None:
                raw_by_upn[upn] = found
        if not raw_by_upn:
            return None

        def _si(v):
            try:
                return int(str(v).strip() or 0)
            except (TypeError, ValueError):
                return 0
        for pic in sorted(pics, key=lambda p: (_si(p.get("Rating")), _si(p.get("Rate"))),
                          reverse=True):
            u = pic.get("UniquePictureName")
            if u in raw_by_upn:
                return raw_by_upn[u]
        return next(iter(raw_by_upn.values()))

    # ── leaf pictures (handed to the existing card grid) ────────────────────
    def pictures_for_species(self, sci: str) -> list[dict]:
        return self.db.pictures_for_species(sci)

    def all_species(self) -> list[dict]:
        """Every species with at least one master picture, scientific-name order —
        the flat 'all species' index (backlog ch.14, item 2)."""
        out = [n for k, n in self._by_sci.items()
               if n["rank"] == "SPECIES" and self._pics.get(k, 0) > 0]
        out.sort(key=lambda n: n["sci"].lower())
        return out

    # ── rank search ─────────────────────────────────────────────────────────
    def search(self, query: str, rank: str | None = None) -> list[dict]:
        """Every photographed node whose NL/EN/scientific name contains `query`,
        optionally restricted to one rank. Coarsest rank first, then by name."""
        q = (query or "").strip().lower()
        if not q:
            return []
        want = (rank or "").upper() if rank and rank != "ANY" else None
        hits = []
        for key, n in self._by_sci.items():
            if key == ROOT or self._pics.get(key, 0) <= 0:
                continue
            if want and n["rank"] != want:
                continue
            if any(q in (n[f] or "").lower() for f in ("sci", "nl", "en")):
                hits.append(n)
        hits.sort(key=lambda n: (RANK_ORDER.index(n["rank"]) if n["rank"] in RANK_ORDER else 99,
                                 n["sci"].lower()))
        return hits
