#!/usr/bin/env python3
"""Copy JPG-bearing species folders from the master biota tree to a SmallMaster ZIP.

Writes smallmaster.zip into the target directory containing:
  biota/**/*.jpg         the pictures the laptop can actually show
  biota/**/*.xmp         but only beside a JPG that is in the ZIP (see
                         _has_picture) — a sidecar for a picture that is not
                         here is unreadable weight: the laptop takes rating,
                         EXIF and dimensions from metadata/active, and it is
                         read-only, so nothing ever writes the sidecar back
  metadata/active/       all CSVs; data_files.csv paths are made root-relative
  smallmaster.json       when this snapshot was taken (engine/smallmaster_stamp)
RAWs, .dop, HTML and web/ output are excluded.

Running twice is safe: the existing ZIP is overwritten.
"""

import argparse
import configparser
import csv as csv_mod
import io
import os
import sys
import time
import logging
import zipfile
from pathlib import Path

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from engine.exceptions import SVException
from engine.handling import relativize_path
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")

_COPY_EXTS = {".jpg", ".jpeg", ".xmp"}
_JPG_EXTS = (".jpg", ".jpeg")


def _has_picture(xmp_name: str, jpg_names) -> bool:
    """Is there a JPG in this folder belonging to the same picture as this .xmp?

    The master's own rule (db.master_mutate._belongs_to_upn): the stem followed
    by a separator or nothing, so dsc_0620.jpg, dsc_0620-small.jpg and
    dsc_0620_DxO.jpg all count for dsc_0620.xmp, while dsc_06201.jpg does not.
    A plain stem comparison would have quietly dropped 2,521 sidecars on this
    master that DO belong to a picture you can see.
    """
    stem = Path(xmp_name).stem.lower()
    return any(j.startswith(stem) and (len(j) == len(stem) or j[len(stem)] in "._- ")
               for j in jpg_names)


def _add_data_files_to_zip(zf: zipfile.ZipFile, src: str, master_root: str, arc_name: str) -> None:
    """Write data_files.csv into the ZIP with WindowsFileName/File:Directory made root-relative."""
    buf = io.StringIO()
    with open(src, "r", newline="", encoding="utf-8") as fh_in:
        reader = csv_mod.DictReader(fh_in, delimiter=";")
        fieldnames = reader.fieldnames
        writer = csv_mod.DictWriter(
            buf, fieldnames=fieldnames,
            delimiter=";", quoting=csv_mod.QUOTE_MINIMAL,
        )
        writer.writeheader()
        for row in reader:
            row["File:Directory"] = relativize_path(row.get("File:Directory", ""), master_root)
            row["WindowsFileName"] = relativize_path(row.get("WindowsFileName", ""), master_root)
            writer.writerow(row)
    zf.writestr(arc_name, buf.getvalue().encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)


def _human_time(seconds) -> str:
    """"3m10s" / "45s" — short enough to sit inside a progress line."""
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds}s"


def _progress_line(files_done, files_total, bytes_done, bytes_total, elapsed) -> str:
    """"12,000 of 48,912 files (25%), 3.1 of 11.7 GB, 2m10s gone, ~6m20s left".

    The estimate is made from BYTES, not from the file count: the tree mixes
    4 MB JPGs with 4 KB sidecars, so counting files would promise a finish time
    that moves around as the mix changes. It also measures the whole run rather
    than the last few files, which is what makes it steady enough to plan by.
    """
    from utils.disk_space import human_bytes
    pct = (100 * bytes_done // bytes_total) if bytes_total else 0
    line = (f"{files_done:,} of {files_total:,} files ({pct}%), "
            f"{human_bytes(bytes_done)} of {human_bytes(bytes_total)}, "
            f"{_human_time(elapsed)} gone")
    if bytes_done and elapsed > 2:
        rate = bytes_done / elapsed
        line += (f", ~{_human_time((bytes_total - bytes_done) / rate)} left "
                 f"at {rate / 1024 ** 2:.0f} MB/s")
    return line


def main(args, config):
    starttime = time.time()

    source_root = (
        config.get("master", "MasterBiotaDatabase", fallback="")
        or config.get("repository", "BiotaDatabase", fallback="")
    )
    target_dir = config.get("utilities", "smallmaster_target", fallback="")

    if not source_root or not os.path.isdir(source_root):
        raise SVException(f"Master source not found: {source_root!r}")
    if not target_dir:
        raise SVException("No target directory specified.")
    if os.path.normcase(os.path.normpath(source_root)) == os.path.normcase(os.path.normpath(target_dir)):
        raise SVException("Source and target are the same directory.")

    biota_src = os.path.join(source_root, "biota")
    meta_src  = os.path.join(source_root, "metadata", "active")

    if not os.path.isdir(biota_src):
        raise SVException(f"biota/ not found under {source_root!r}")

    os.makedirs(target_dir, exist_ok=True)
    zip_path = os.path.join(target_dir, "smallmaster.zip")

    copied_pics = 0
    copied_meta = 0
    errors = 0

    # ── know the size of the job before starting it ──────────────────────────
    # The walk is seconds; the writing is minutes, and over a network share it
    # is much worse than that. Counting first turns "12,000 files added…" —
    # which says nothing about whether that is nearly done or barely started —
    # into a fraction with a finish time. Collected as a LIST rather than
    # counted in a separate pass, so the tree is walked once and the write loop
    # spends its time writing.
    # A sidecar whose picture is not in the ZIP is 12 KB of nothing on the
    # laptop: ratings, EXIF and dimensions all come from metadata/active, the
    # card is drawn from those, and the machine is read-only so nothing writes
    # the sidecar back. On this master they are 70% of the FILE COUNT and 3%
    # of the bytes — and the file count is what makes the copy and the install
    # slow. Set [utilities] smallmaster_all_sidecars = yes for the old
    # everything-in behaviour.
    all_sidecars = config.get("utilities", "smallmaster_all_sidecars",
                              fallback="no").strip().lower() in ("yes", "true", "1")
    skipped_sidecars = 0

    logger.info("SmallMaster: scanning the master…")
    plan = []                      # [(abs_path, arc_name, size, compress), …]
    total_bytes = 0
    for dirpath, _dirs, filenames in os.walk(biota_src):
        jpg_names = [n.lower() for n in filenames if n.lower().endswith(_JPG_EXTS)]
        for fname in filenames:
            suffix = Path(fname).suffix.lower()
            if suffix not in _COPY_EXTS:
                continue
            if suffix == ".xmp" and not all_sidecars and not _has_picture(fname, jpg_names):
                skipped_sidecars += 1
                continue
            abs_path = os.path.join(dirpath, fname)
            arc_name = "biota/" + os.path.relpath(abs_path, biota_src).replace("\\", "/")
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                size = 0
            # JPGs are already compressed; XMPs are small text → deflate
            compress = (zipfile.ZIP_STORED if suffix in {".jpg", ".jpeg"}
                        else zipfile.ZIP_DEFLATED)
            plan.append((abs_path, arc_name, size, compress))
            total_bytes += size
    scan_elapsed = time.time() - starttime
    from utils.disk_space import human_bytes
    logger.info(f"SmallMaster: {len(plan):,} biota file(s), "
                f"{human_bytes(total_bytes)} to write → {zip_path} "
                f"(scanned in {scan_elapsed:.1f}s)")
    if skipped_sidecars:
        logger.info(f"  Leaving out {skipped_sidecars:,} sidecar(s) whose picture has "
                    f"no JPG — the laptop reads their rating and EXIF from "
                    f"metadata/active, not from the sidecar.")
    elif all_sidecars:
        logger.info("  Including every sidecar ([utilities] smallmaster_all_sidecars).")
    # Say it before the hour is spent, not after. Writing the ZIP straight onto
    # a share means every one of those gigabytes crosses the network as it is
    # produced, and a connection that drops halfway leaves a part-written ZIP
    # that looks like a file. Building it locally and copying the finished
    # article costs one extra copy and cannot half-finish.
    try:
        from utils.disk_space import is_network_path
        if is_network_path(target_dir):
            logger.warning(
                "  The output directory is on a network drive. Writing a "
                "multi-gigabyte ZIP over the network is slow, and a dropped "
                "connection leaves a half-written ZIP — building it on a local "
                "disk and copying the finished file is usually faster and safer.")
    except Exception:
        pass

    with zipfile.ZipFile(zip_path, "w") as zf:

        # ── biota tree: JPG/XMP only ─────────────────────────────────────────
        done_bytes = 0
        last_report = time.time()
        for abs_path, arc_name, size, compress in plan:
            try:
                zf.write(abs_path, arc_name, compress_type=compress)
                copied_pics += 1
                done_bytes += size
            except Exception as e:
                logger.warning(f"  ZIP biota failed {Path(abs_path).name}: {e}")
                errors += 1
                continue
            # Report on files OR on time, whichever comes first: 500 files is a
            # sensible tick on a local disk and several minutes of silence on a
            # slow target, which is exactly where the reassurance is needed.
            now = time.time()
            if copied_pics % 500 == 0 or now - last_report >= 5.0:
                last_report = now
                logger.info("  " + _progress_line(copied_pics, len(plan),
                                                  done_bytes, total_bytes,
                                                  now - starttime))

        # ── metadata/active ──────────────────────────────────────────────────
        if os.path.isdir(meta_src):
            for fname in os.listdir(meta_src):
                src_file = os.path.join(meta_src, fname)
                if not os.path.isfile(src_file):
                    continue
                arc_name = "metadata/active/" + fname
                try:
                    if fname.lower() == "data_files.csv":
                        _add_data_files_to_zip(zf, src_file, source_root, arc_name)
                    else:
                        zf.write(src_file, arc_name, compress_type=zipfile.ZIP_DEFLATED)
                    copied_meta += 1
                except Exception as e:
                    logger.warning(f"  ZIP metadata failed {fname}: {e}")
                    errors += 1

        # ── the snapshot's own date, written LAST ────────────────────────────
        # On the laptop this copy answers "is this a lifer?" and feeds the
        # species suggestions, and a stale copy answers both confidently and
        # wrongly with nothing to notice. So the ZIP carries its date, at the
        # root, where extracting it to the master directory files it correctly
        # with no install-side step to forget. Last, and from the counts that
        # actually went in — a stamp written up front can describe a ZIP that
        # never finished.
        from engine.smallmaster_stamp import STAMP_NAME, stamp_json
        zf.writestr(STAMP_NAME,
                    stamp_json(source_root=source_root,
                               biota_files=copied_pics,
                               metadata_files=copied_meta),
                    compress_type=zipfile.ZIP_DEFLATED)

    elapsed = time.time() - starttime
    warn = f", {errors} errors" if errors else ""
    zip_mb = os.path.getsize(zip_path) / 1024 / 1024
    rate = f", {zip_mb / max(elapsed - scan_elapsed, 0.1):.0f} MB/s"
    missing = len(plan) - copied_pics
    short = f", {missing} file(s) NOT written" if missing else ""
    msg = (
        f"SmallMaster ZIP done in {_human_time(elapsed)}: "
        f"{copied_pics:,} of {len(plan):,} biota files + {copied_meta} metadata files, "
        f"{zip_mb:.0f} MB{rate}, snapshot dated {time.strftime('%d %b %Y %H:%M')} "
        f"→ {zip_path}{warn}{short}"
    )
    logger.info(msg)
    return msg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svu9SmallMaster — create SmallMaster database ZIP")
    parser.add_argument(
        "--config",
        default=os.environ.get("SVCONFIG") or str(svconfig_path()),
        help="Configuration file",
    )
    parser.add_argument("--target", required=True, help="Target directory to write smallmaster.zip into")
    args = parser.parse_args()
    config = configparser.ConfigParser()
    read_ini(config, args.config)
    if not config.has_section("utilities"):
        config.add_section("utilities")
    config["utilities"]["smallmaster_target"] = args.target
    logging.basicConfig(level=logging.INFO)
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"svu9SmallMaster failed: {e}")
    else:
        logger.info("svu9SmallMaster succeeded")
