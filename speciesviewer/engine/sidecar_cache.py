"""Per-folder XMP sidecar metadata cache (.sv_sidecar_cache.json).

Written at ingest time (sv0ReadSDCard) after XMP sidecars are created.
Read at culling time (sidecar_provider) with mtime_ns-based staleness
detection: a changed mtime (e.g. after Read & Summarize writes species
into the XMP) triggers a single-file exiftool re-read and cache update.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("speciesviewer")

_FILENAME = ".sv_sidecar_cache.json"
_VERSION = 5   # v2 added location text; v3 adds CountryCode so the cached row is a
               # complete substitute for a read_location() exiftool spawn; v4 adds
               # GPSProcessingMethod — WITHOUT the bump, an existing cache is only
               # re-read when a file's mtime changes, so a picture seeded from a place
               # name would come back after a restart with no provenance, and the map
               # would refuse to move the very point you seeded in order to refine it.
               # v5 EVICTS caches poisoned by the put_field bug below: a geotag wrote
               # GPS + place text but only the place text was patched in, and the
               # re-stamped mtime made the row claim to be current — so the cache went
               # on reporting "no GPS" for pictures that had been placed. Measured on
               # Helgoland: 92 of 93 sidecars had GPS on disk, the cache admitted 25.

# Tags exiftool reads from each XMP — must stay in sync with usage in sidecar_provider.
_CACHE_TAGS = [
    "-Subject", "-Rating", "-Label", "-Description", "-Headline",
    "-DateTimeOriginal", "-ISO", "-FNumber", "-ExposureTime", "-FocalLength",
    "-Make", "-Model", "-LensModel", "-SubjectDistance", "-ImageWidth", "-ImageHeight",
    "-GPSPosition", "-GPSLatitude", "-GPSLongitude",
    # HOW the fix was obtained. Blank = the camera measured it, and the map refuses
    # to move it. "MANUAL" = a human placed it (map drag / place-name lookup), so it
    # may still be dragged onto the exact spot. Cached because the map asks this of
    # every bubble it is about to move.
    "-GPSProcessingMethod",
    # Reverse-geocoded place text, so the grid card's location line AND the large
    # viewer's per-step read_location() cost no exiftool spawn (they ride this one
    # batched read). Municipality reads fine without -config.
    "-Location", "-City", "-Municipality", "-State", "-Country", "-CountryCode",
]


class SidecarCache:
    """Load / query / update the per-folder XMP metadata cache."""

    def __init__(self, folder: Path):
        self._folder = Path(folder)
        self._path = self._folder / _FILENAME
        self._dirty = False
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    raw = json.load(f)
                if raw.get("version") == _VERSION:
                    self._data = raw.get("files", {})
        except Exception:
            self._data = {}

    def _key(self, xmp_path: Path) -> str:
        return xmp_path.name.lower()

    def get(self, xmp_path: Path) -> dict | None:
        """Return cached row if XMP mtime_ns still matches; None if stale or missing."""
        entry = self._data.get(self._key(xmp_path))
        if not entry:
            return None
        try:
            if xmp_path.stat().st_mtime_ns == entry.get("_mtime_ns"):
                return entry
        except OSError:
            pass
        return None

    def put(self, xmp_path: Path, row: dict) -> None:
        """Store a full metadata row, recording the current XMP mtime_ns."""
        try:
            mtime_ns = xmp_path.stat().st_mtime_ns
        except OSError:
            return
        entry = {k: v for k, v in row.items() if k != "_mtime_ns"}
        entry["_mtime_ns"] = mtime_ns
        self._data[self._key(xmp_path)] = entry
        self._dirty = True

    def put_field(self, xmp_path: Path, field: str, value) -> None:
        """Patch one field after a write-back from the culling viewer.

        Updates the stored mtime_ns to the file's new mtime so the next
        open does not re-read this entry via exiftool unnecessarily.
        No-op if no entry exists yet (will be read fresh on next open).

        ⚠ Re-stamping the mtime asserts THE WHOLE ROW IS NOW CURRENT. That is only
        true when the write changed exactly the field being patched. If a single
        write touched other tags too, patch them all — or call invalidate(), which
        is always safe. Getting this wrong does not corrupt the file; it makes the
        cache confidently return stale values, which is harder to notice. (It bit us
        once: geotagging writes GPS *and* place text, only the place text was patched
        here, and the re-stamped row then hid the new coordinate from everything that
        trusted the cache.)
        """
        key = self._key(xmp_path)
        if key not in self._data:
            return
        self._data[key][field] = value
        try:
            self._data[key]["_mtime_ns"] = xmp_path.stat().st_mtime_ns
        except OSError:
            pass
        self._dirty = True

    def invalidate(self, xmp_path: Path) -> None:
        """Forget this file — the next open re-reads it from disk.

        For a write that changed more of the row than the caller can restate (a
        geotag writes coordinates, a processing method AND the reverse-geocoded
        place). Dropping the entry costs one batched exiftool read later and is
        always correct; guessing at the row's new contents is not."""
        key = self._key(xmp_path)
        if self._data.pop(key, None) is not None:
            self._dirty = True

    def save(self) -> None:
        """Flush to disk atomically; silently skips if nothing changed."""
        if not self._dirty:
            return
        try:
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": _VERSION, "files": self._data},
                          f, separators=(",", ":"))
            tmp.replace(self._path)
        except OSError as e:
            logger.warning(f"Could not save sidecar cache {self._path}: {e}")


def read_xmp_rows(xmp_paths: list[Path]) -> list[dict]:
    """Run exiftool on xmp_paths and return parsed JSON rows.

    Uses run_exiftool_files (argfile) to avoid the Windows command-line
    length limit regardless of how many files are passed.
    """
    from engine.exif_writer import run_exiftool_files
    import json as _json
    if not xmp_paths:
        return []
    try:
        r = run_exiftool_files(_CACHE_TAGS + ["-j"], xmp_paths)
        return _json.loads(r.stdout) if r.stdout.strip() else []
    except Exception as e:
        logger.warning(f"Could not read XMP metadata via exiftool: {e}")
        return []


def build_cache_for_folder(folder: Path, xmp_paths: list[Path]) -> None:
    """Build or update the sidecar cache for one folder.

    Only re-reads XMPs whose mtime_ns has changed since the cache was last
    written — so repeat ingest runs (partial card top-up) are incremental.
    Called at ingest time after XMP sidecars are created.
    """
    cache = SidecarCache(folder)
    stale = [p for p in xmp_paths if cache.get(p) is None]
    if not stale:
        return
    rows = read_xmp_rows(stale)
    for row in rows:
        src = row.get("SourceFile", "")
        if src:
            cache.put(Path(src), row)
    cache.save()
    logger.info(f"Sidecar cache: {len(rows)} entries written for {folder.name}")
