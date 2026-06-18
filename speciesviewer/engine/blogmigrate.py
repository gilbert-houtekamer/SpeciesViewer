# blogmigrate.py: bridge a real trip's hand-written day blogs into its successor
# virtual trip, as part of trip retirement.
#
# A real trip keeps each day's blog text in CurrentPictures/<year>/<trip>/<date>/
# blog.ini — the volatile folder that gets cleaned out. A virtual trip keeps the
# same text in the durable <virttrip>/metadata/blog/blog_<date>.ini. The INI
# format is identical (sections blog_text / blog_config / blog_selection), so
# moving the blog forward is a faithful copy + rename — NO text is generated.
# Days with no blog.ini are left untouched (nothing fabricated), so the virtual
# trip's blog comes out indistinguishable from the original.

import os
import re
import shutil
import logging

logger = logging.getLogger("speciesviewer")

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def migrate_trip_blogs(trip_picture_root, virttrip_root, overwrite=False):
    """Copy each dated day's blog.ini from the trip's picture folder into the
    virttrip's durable blog dir as blog_<date>.ini. Returns
    (copied, kept_existing, days_without_blog). Existing virttrip blog files are
    kept (not clobbered) unless overwrite=True, so re-running never loses edits."""
    blog_dir = os.path.join(virttrip_root, "metadata", "blog")
    copied = kept = no_blog = 0
    if not os.path.isdir(trip_picture_root):
        logger.warning(f"blogmigrate: trip picture root not found: {trip_picture_root}")
        return copied, kept, no_blog
    os.makedirs(blog_dir, exist_ok=True)
    for day in sorted(os.listdir(trip_picture_root)):
        if not _DAY_RE.match(day):
            continue
        src = os.path.join(trip_picture_root, day, "blog.ini")
        if not os.path.isfile(src):
            no_blog += 1
            continue
        dest = os.path.join(blog_dir, f"blog_{day}.ini")
        if os.path.exists(dest) and not overwrite:
            logger.info(f"blogmigrate: kept existing {os.path.basename(dest)}")
            kept += 1
            continue
        shutil.copy2(src, dest)
        logger.info(f"blogmigrate: {day}/blog.ini -> {os.path.basename(dest)}")
        copied += 1
    logger.info(f"blogmigrate: {copied} copied, {kept} kept, "
                f"{no_blog} day(s) without a blog")
    return copied, kept, no_blog
