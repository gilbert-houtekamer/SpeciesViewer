#!/usr/bin/env python3

# Read pictures from an SD-card (or any folder) into the project pictures
# directory — the front-stage "ingest" step that precedes the per-day sv1-sv7
# pipeline.
#
# M0 scope (deliberately minimal):
#   - copy flat into the destination: NO rename, NO date-bucketing (those come later)
#   - verified copy (sha256 of source vs destination); a failed verify removes the
#     partial copy and is reported as a failure
#   - skip files already copied (same name + identical content) so re-runs are safe
#   - never modify the card (no delete / format)
#
# Only picture / raw / video files are copied; everything else on the card is
# ignored. Paths use pathlib so the same code works on Windows and macOS.

import hashlib
import logging
import shutil
from pathlib import Path

# Lower-case extensions (incl. dot) we treat as "pictures" worth ingesting.
PICTURE_EXTENSIONS = {
    ".cr3", ".cr2", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2",  # raw
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff",       # stills
    ".mp4", ".mov",                                                    # video
}


def _sha256(path, chunk_size=1 << 20):
    """Streaming sha256 of a file, so we never load a multi-GB raw into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _already_copied(src, target):
    """True if target is byte-identical to src (same size, same hash)."""
    try:
        if src.stat().st_size != target.stat().st_size:
            return False
    except OSError:
        return False
    return _sha256(src) == _sha256(target)


def target_name(src_name, prefix=""):
    """Destination filename for a source picture. With a prefix, the original
    name is preserved and prefixed: 'IMG_1234.CR3' -> '2026 Zeeland - IMG_1234.CR3'.
    The prefix is what makes names unique across cards/runs, so re-running is
    still idempotent (same input -> same target name)."""
    prefix = (prefix or "").strip()
    return f"{prefix} - {src_name}" if prefix else src_name


def copy_card(source, dest, prefix="", logger=None, progress=None, should_cancel=None):
    """Copy picture files from `source` (recursively) into `dest` (flat).

    prefix                       - optional filename prefix (see target_name).
    progress(done, total, name)  - optional callback, called per file before copy.
    should_cancel()              - optional callable; return True to stop early.

    Returns a summary dict: total / copied / skipped / conflict / failed / bytes.
    """
    log = logger or logging.getLogger("speciesviewer")
    source = Path(source)
    dest = Path(dest)

    files = sorted(
        p for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() in PICTURE_EXTENSIONS
    )
    total = len(files)
    summary = {"total": total, "copied": 0, "skipped": 0,
               "conflict": 0, "failed": 0, "bytes": 0}

    log.info(f"Found {total} picture(s) under {source}")

    for i, src in enumerate(files, 1):
        if should_cancel and should_cancel():
            log.warning("⏹ Card read cancelled by user.")
            break
        if progress:
            progress(i, total, src.name)

        target = dest / target_name(src.name, prefix)
        try:
            if target.exists():
                if _already_copied(src, target):
                    summary["skipped"] += 1
                    continue
                # Same name, different content — flat copy has no place to put
                # this without overwriting. Leave the existing file alone and
                # flag it; renaming (M1) is what ultimately resolves this.
                log.warning(f"⚠️ Name collision, not overwriting: {target.name} "
                            f"(already exists with different content)")
                summary["conflict"] += 1
                continue

            src_hash = _sha256(src)
            shutil.copy2(src, target)  # copy2 keeps mtime — needed for date-bucketing later
            if _sha256(target) != src_hash:
                target.unlink(missing_ok=True)
                log.warning(f"❌ Verification failed, removed partial copy: {src.name}")
                summary["failed"] += 1
                continue

            summary["copied"] += 1
            summary["bytes"] += target.stat().st_size
        except OSError as e:
            log.warning(f"❌ Failed to copy {src.name}: {e}")
            summary["failed"] += 1

    log.info(
        f"Card read summary — copied {summary['copied']}, skipped {summary['skipped']}, "
        f"conflicts {summary['conflict']}, failed {summary['failed']} of {total}."
    )
    return summary


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Copy pictures from an SD-card folder into a project directory.")
    parser.add_argument("source", help="Source folder (e.g. a mounted SD-card)")
    parser.add_argument("dest", help="Destination project pictures folder")
    parser.add_argument("--prefix", default="", help="Optional filename prefix, e.g. '2026 Zeeland'")
    args = parser.parse_args()
    copy_card(args.source, args.dest, prefix=args.prefix)
