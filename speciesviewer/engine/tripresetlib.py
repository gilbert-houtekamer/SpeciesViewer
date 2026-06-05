"""Shared logic for clearing trip repository data."""

import os
import shutil
import logging
import time
from pathlib import Path

logger = logging.getLogger("speciesviewer")


def _find_locked_files(directory):
    """Try to identify files that are locked by another process."""
    locked = []
    for root, dirs, files in os.walk(directory):
        for f in files:
            fpath = os.path.join(root, f)
            try:
                with open(fpath, 'a'):
                    pass
            except PermissionError:
                locked.append(fpath)
    return locked


def clear_review_directories(repo_path):
    """Delete all day directories under metadata/review/.

    Returns the number of directories removed.
    Uses retry loop because Windows sometimes holds file locks briefly.
    """
    review_dir = os.path.join(repo_path, 'metadata', 'review')
    if not os.path.isdir(review_dir):
        logger.warning(f"Review directory does not exist: {review_dir}")
        return 0

    removed = 0
    for item in os.listdir(review_dir):
        item_path = os.path.join(review_dir, item)
        if os.path.isdir(item_path):
            for attempt in range(10):
                try:
                    shutil.rmtree(item_path)
                    removed += 1
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                locked = _find_locked_files(item_path)
                if locked:
                    for lf in locked:
                        logger.warning(f"File is locked (open in another program?): {lf}")
                else:
                    logger.warning(f"Could not remove {item_path} after 10 attempts.")
    return removed


def reset_active_data(repo_path):
    """Reset the active data CSV files to empty templates."""
    dist_dir = str(Path(__file__).resolve().parent.parent / "dist" / "installation files")
    active_dir = os.path.join(repo_path, 'metadata', 'active')

    templates = [
        ('empty_data_files.csv', 'data_files.csv'),
        ('empty_data_pictures.csv', 'data_pictures.csv'),
        ('empty_species.csv', 'species.csv'),
    ]
    for template, target in templates:
        src = os.path.join(dist_dir, template)
        dst = os.path.join(active_dir, target)
        shutil.copy(src, dst)
