"""ONE job: move one picture's files from one day folder of a trip to another.

Used when a picture sits in the wrong day — typically a received picture whose
capture date (set by hand) turns out to be another day of the trip.

INVARIANTS
- Nothing is deleted and nothing is overwritten: if ANY file of the picture
  already exists in the target folder, nothing of that picture moves.
- All or nothing per picture: a move that fails halfway puts back what it had
  already moved, and says so if even that fails.
- The DxO recipes travel along (<file>.dop beside every moved file), so no
  orphan .dop is left to become a phantom picture at the next read.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger("speciesviewer")


def files_with_recipes(files) -> list:
    """The existing files plus every <file>.dop beside them, without duplicates."""
    out, seen = [], set()
    for f in files:
        for p in (Path(f), Path(str(f) + ".dop")):
            key = str(p).lower()
            if key not in seen and p.is_file():
                seen.add(key)
                out.append(p)
    return out


def move_picture(files, target_dir) -> tuple:
    """Move one picture's `files` (and their .dop) into `target_dir`.
    Returns (moved_paths_in_target, problem); problem is "" on success."""
    target = Path(target_dir)
    if not target.is_dir():
        return [], f"the folder {target.name} does not exist"
    srcs = files_with_recipes(files)
    if not srcs:
        return [], "its files were not found"
    clash = [s.name for s in srcs if (target / s.name).exists()]
    if clash:
        return [], f"{', '.join(clash[:3])} already exists in {target.name}"
    done = []
    for s in srcs:
        try:
            shutil.move(str(s), str(target / s.name))
            done.append((s, target / s.name))
        except Exception as e:
            back_failed = []
            for orig, moved in reversed(done):
                try:
                    shutil.move(str(moved), str(orig))
                except Exception:
                    back_failed.append(moved.name)
            if back_failed:
                logger.error(f"Move to {target.name}: {s.name} failed ({e}); could NOT "
                             f"put back {', '.join(back_failed)} — they are in {target}.")
                return [], (f"moving {s.name} failed and {len(back_failed)} file(s) "
                            f"stayed in {target.name}")
            return [], f"moving {s.name} failed ({e}); nothing was moved"
    return [m for _, m in done], ""
