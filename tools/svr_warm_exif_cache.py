"""Pre-read the EXIF cache so the next Read Directory is a cache hit.

Adding or removing a field in engine/selected_fields.py changes
exifcache.field_fingerprint(), which makes EVERY cached entry a miss on
purpose — an entry captured under the old field list must never be served for
the new one. The next scan then re-reads the folder, which at library scale is
minutes of waiting inside the GUI.

This does that re-read up front, from the command line, so the wait happens
when you choose. It writes NOTHING except the cache file: no sidecars, no CSV,
no pictures touched.

    python tools/svr_warm_exif_cache.py "D:/CurrentPictures/2026/2026 Brazil"
    python tools/svr_warm_exif_cache.py "F:/iphone" "D:/CurrentPictures/2026"
    python tools/svr_warm_exif_cache.py --master        # the master-rescan cache

Several roots in one run share one cache save, so an overlapping folder read
twice still only costs one read.
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "speciesviewer"))

import exiftool                                    # noqa: E402
from engine import SELECTED_FIELDS                  # noqa: E402
from engine import exifcache                        # noqa: E402
from utils import config                            # noqa: E402


def warm(roots, use_master_cache=False, recurse=True):
    path = (exifcache.master_cache_path(config) if use_master_cache
            else exifcache.exif_cache_path(config))
    fingerprint = exifcache.field_fingerprint(SELECTED_FIELDS)
    print(f"cache       : {path}")
    print(f"fingerprint : {fingerprint}  ({len(SELECTED_FIELDS)} fields)")
    cache = exifcache.load_cache(path)

    started = time.time()
    total_read = total_hit = 0
    for root in roots:
        if not os.path.isdir(root):
            print(f"\n!! not a directory, skipped: {root}")
            continue
        print(f"\n{root}")
        hits, misses, stats = exifcache.partition(root, cache, fingerprint,
                                                  recurse=recurse)
        print(f"  {len(hits):,} already cached, {len(misses):,} to read")
        total_hit += len(hits)
        if not misses:
            continue
        workers = exifcache.auto_worker_count(
            len(misses), config.get_setting("performance", "exif_workers", "auto"))
        print(f"  reading with {workers} parallel exiftool worker(s)...")
        done, t0 = 0, time.time()
        with exiftool.ExifToolHelper(common_args=exifcache.COMMON_ARGS,
                                     encoding="utf-8", check_execute=False) as et:
            for batch in exifcache.read_metadata_parallel(
                    misses, SELECTED_FIELDS, workers, et):
                exifcache.update(cache, batch, fingerprint)
                done += len(batch)
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed else 0
                left = (len(misses) - done) / rate if rate else 0
                print(f"    {done:,}/{len(misses):,}  "
                      f"({rate:,.0f} files/s, ~{exifcache._fmt_duration(left)} left)",
                      flush=True)
        total_read += done

    # Saved once, at the end: a crash mid-run costs the re-read, never a cache
    # that claims entries it does not have.
    exifcache.save_cache(path, cache)
    print(f"\ncache saved : {total_read:,} newly read, {total_hit:,} were already there")
    print(f"elapsed     : {exifcache._fmt_duration(time.time() - started)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", help="folder(s) to pre-read")
    ap.add_argument("--master", action="store_true",
                    help="warm the master-rescan cache instead of the day/trip one")
    ap.add_argument("--no-recurse", action="store_true",
                    help="read each root's own files only, not its sub-folders")
    args = ap.parse_args(argv)
    if not args.roots:
        ap.error("name at least one folder to pre-read")
    return warm(args.roots, use_master_cache=args.master,
                recurse=not args.no_recurse)


if __name__ == "__main__":
    sys.exit(main())
