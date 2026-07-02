# micro_trip.py: ephemeral "micro-trip" bootstrap for Add-Pictures-to-Master.
#
# A micro-trip is a throwaway trip context built around ONE input directory of
# pictures. It exists only so the normal trip pipeline (read EXIF, recognise
# species, distil pictures) and the normal trip editing dialog can run against
# an arbitrary folder — no persistent trip, no entry in trips/. The scratch
# repository (where the per-day CSVs land) lives in a temp dir and is deleted on
# teardown. The picture files themselves are never moved or modified here.
#
# Ingest is master-READ-ONLY: it reads the master's species.csv for taxonomy and
# writes the produced CSVs into the scratch repo. Committing to the master is a
# later, separate step (the existing Add-to-Master pipeline).

import os
import csv
import shutil
import tempfile
import logging
from pathlib import Path
from configparser import ConfigParser

logger = logging.getLogger("speciesviewer")


def _row_has_jpg(row):
    """True if a data_pictures row has a JPG in its group (so it can show on the
    website). A picture with only RAW/HEIC and no JPG is what becomes a 'ghost
    species' in the master — worth flagging before commit."""
    if (row.get("BestJPG") or "").strip() or (row.get("JPGSize") or "").strip():
        return True
    files = (row.get("Files") or "")
    return ".jpg" in files.lower() or ".jpeg" in files.lower()


class MicroTrip:
    """Context manager that stands up a scratch trip repo around `input_dir` and
    tears it down on exit. The picture directory (input_dir) is treated as one
    "day"; its folder name becomes the project name.

    Usage:
        with MicroTrip(input_dir, master_root) as mt:
            mt.ingest()                 # read EXIF + recognise species
            report = mt.summary()       # what we found (no writes to master)
    """

    def __init__(self, input_dir, master_root, *, web_root="/nature/", keep=False,
                 recurse=False):
        self.input_dir = str(input_dir).replace('"', "").replace("'", "").rstrip("/\\")
        self.master_root = str(master_root).replace('"', "").replace("'", "")
        self.web_root = web_root
        self.keep = keep                 # leave the scratch repo on disk (debug)
        self.recurse = recurse           # descend into sub-folders (default: just this dir)
        self.scratch_repo = None
        self.project_name = Path(self.input_dir).stem

    # ── lifecycle ──────────────────────────────────────────────────────────
    # Use either the context-manager form (`with MicroTrip(...) as mt:`) for a
    # scan-and-forget pass, or the explicit setup()/teardown() pair when the
    # scratch must outlive the call (the GUI keeps it alive for editing).
    def setup(self):
        if not os.path.isdir(self.input_dir):
            raise FileNotFoundError(f"Input directory not found: {self.input_dir}")
        species_csv = os.path.join(self.master_root, "metadata", "active", "species.csv")
        if not os.path.isfile(species_csv):
            raise FileNotFoundError(f"Master species reference not found: {species_csv}")
        self.scratch_repo = tempfile.mkdtemp(prefix="sv_microtrip_")
        logger.info(f"micro_trip: scratch repo {self.scratch_repo}")
        return self

    def teardown(self):
        if self.scratch_repo and os.path.isdir(self.scratch_repo) and not self.keep:
            try:
                shutil.rmtree(self.scratch_repo, ignore_errors=True)
                logger.info("micro_trip: scratch repo removed")
            except Exception as e:
                logger.warning(f"micro_trip: could not remove scratch repo: {e}")
            finally:
                self.scratch_repo = None

    def __enter__(self):
        return self.setup()

    def __exit__(self, exc_type, exc, tb):
        self.teardown()
        return False

    # ── config (mirrors utils.batch_config.generate_svconfig_ini, trip mode) ──
    def build_config(self):
        """A self-contained module config for the batch steps, pointed at the
        scratch repo + input dir, reading taxonomy from the master."""
        species_csv = os.path.join(self.master_root, "metadata", "active", "species.csv")
        cfg = ConfigParser()
        cfg["repository"] = {
            "BiotaDatabase":    self.scratch_repo,
            "BiotaWebRoot":     self.web_root,
            "ReferenceSpecies": species_csv,
        }
        cfg["master"] = {"MasterBiotaDatabase": self.master_root,
                         "MasterBiotaWebRoot": self.web_root}
        cfg["project"] = {"PictureDirectory": self.input_dir,
                          "Recurse": "1" if self.recurse else "0",
                          # absolute paths in the scratch so the review window
                          # resolves files no matter which trip is active.
                          "Relativize": "0"}
        cfg["debug"] = {"Classification": "0", "SpeciesFound": "0"}
        cfg["websettings"] = {"Sftppwd": "", "base_url": ""}
        cfg["avibase"] = {"source_format": "avibase", "phone_layout": "false",
                          "first_column_lang": "nl"}
        cfg["utilities"] = {"smallmaster_target": ""}
        return cfg

    @property
    def review_dir(self):
        """Where the per-day CSVs land: scratch/metadata/review/<project_name>."""
        return os.path.join(self.scratch_repo, "metadata", "review", self.project_name)

    def _seed_active(self):
        """Seed scratch/metadata/active so sv3's species recognition has its
        references. The recognition tables (speciescollection, species) are
        copied from the master; the "already present" base (data_files,
        data_pictures) is seeded EMPTY (header only) so every picture in the
        input directory counts as new and gets classified + written out."""
        from engine.csv_columns import DATA_FILE_FIELDS, PICTURE_FIELDS
        active = os.path.join(self.scratch_repo, "metadata", "active")
        os.makedirs(active, exist_ok=True)
        master_active = os.path.join(self.master_root, "metadata", "active")
        for name in ("speciescollection.csv", "species.csv"):
            src = os.path.join(master_active, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(active, name))
            else:
                raise FileNotFoundError(f"Master recognition table missing: {src}")
        for name, fields in (("data_files.csv", DATA_FILE_FIELDS),
                             ("data_pictures.csv", PICTURE_FIELDS)):
            with open(os.path.join(active, name), "w", encoding="utf-8", newline="") as fh:
                csv.writer(fh, delimiter=";").writerow(fields)

    # ── ingest: read EXIF + recognise species (master read-only) ──────────────
    def ingest(self):
        """Run the existing read+summarise steps against the input directory.
        Produces data_files.csv / data_pictures.csv / species.csv in review_dir.
        Returns the path to data_pictures.csv."""
        import batch.sv2ReadDirExif as sv2
        import batch.sv3WritePictures as sv3
        cfg = self.build_config()
        self._seed_active()
        logger.info(f"micro_trip: ingesting {self.input_dir}")
        sv2.main(None, cfg)
        sv3.main(None, cfg)
        return os.path.join(self.review_dir, "data_pictures.csv")

    # ── summary: what did we find (no writes) ────────────────────────────────
    def summary(self):
        """Read the produced data_pictures.csv and report what the micro-trip
        found. Returns {ok, pictures, identified, no_species, species:{name:n}}
        or {ok: False, error}."""
        pic_csv = os.path.join(self.review_dir, "data_pictures.csv")
        if not os.path.isfile(pic_csv):
            return {"ok": False, "error": f"No data_pictures.csv produced at {pic_csv}"}
        import re
        pictures = identified = no_species = 0
        species = {}
        unrecognized = {}            # tagged name -> count (name didn't resolve)
        date_lo = date_hi = ""       # YYYY:MM:DD span of the scanned pictures
        try:
            with open(pic_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh, delimiter=";"):
                    if not (row.get("UniquePictureName") or "").strip():
                        continue
                    pictures += 1
                    day = (row.get("DateTimeOriginal") or "")[:10].strip()
                    if day:
                        date_lo = day if not date_lo else min(date_lo, day)
                        date_hi = day if not date_hi else max(date_hi, day)
                    sp = (row.get("WetSoortNaam") or "").strip()
                    err = (row.get("errormessage") or "").strip()
                    if not sp and err:
                        # The user tagged a name that didn't resolve to a species
                        # ("may have a typo" / not in GBIF). Surface it so it's
                        # obvious — otherwise it's silently dropped from the set.
                        name = (row.get("subject") or "").strip()
                        if not name:
                            m = re.search(r'"([^"]+)"', err)
                            name = m.group(1) if m else "(unresolved)"
                        unrecognized[name] = unrecognized.get(name, 0) + 1
                        continue
                    if sp:
                        identified += 1
                        rec = species.setdefault(sp, {"n": 0, "nl": "", "eng": "", "jpgs": 0})
                        rec["n"] += 1
                        if not rec["nl"]:
                            rec["nl"] = (row.get("NLSoortNaam") or "").strip()
                        if not rec["eng"]:
                            rec["eng"] = (row.get("EngSoortNaam") or "").strip()
                        if _row_has_jpg(row):
                            rec["jpgs"] += 1
                    else:
                        no_species += 1
        except Exception as e:
            return {"ok": False, "error": f"failed reading {pic_csv}: {e}"}
        # How many of each species the master ALREADY holds — a rarity cue: a
        # species the master is rich in makes the trip's extra copies expendable
        # (cull blue); a near-absent one is worth keeping. Counted from the
        # master's real pictures (NOT the drift-prone species.csv counters).
        master_counts = self._master_species_counts(date_lo, date_hi)
        for sp, rec in species.items():
            mp, mj, rp, rj = master_counts.get(sp.lower(), (0, 0, 0, 0))
            rec["master_pics"], rec["master_jpgs"] = mp, mj
            rec["range_pics"], rec["range_jpgs"] = rp, rj
        with_jpg = sum(r["jpgs"] for r in species.values())
        return {"ok": True, "pictures": pictures, "identified": identified,
                "no_species": no_species, "species": species,
                "unrecognized": unrecognized,
                "identified_with_jpg": with_jpg, "identified_no_jpg": identified - with_jpg,
                "date_lo": date_lo, "date_hi": date_hi,
                "review_dir": self.review_dir}

    def _master_species_counts(self, date_lo="", date_hi=""):
        """{species_lower: (pictures, jpgs, range_pictures, range_jpgs)} from the
        master's real pictures. The range_* pair counts only master rows whose
        DateTimeOriginal day falls within [date_lo, date_hi] (the scanned trip's
        own date span) — a "are these already in the master?" cue. With no span
        given the range_* pair is left at 0."""
        counts = {}
        pic_csv = os.path.join(self.master_root, "metadata", "active", "data_pictures.csv")
        if not os.path.isfile(pic_csv):
            return counts
        try:
            with open(pic_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh, delimiter=";"):
                    sp = (row.get("WetSoortNaam") or "").strip().lower()
                    if not sp:
                        continue
                    has_jpg = 1 if _row_has_jpg(row) else 0
                    p, j, rp, rj = counts.get(sp, (0, 0, 0, 0))
                    p += 1
                    j += has_jpg
                    if date_lo and date_hi:
                        day = (row.get("DateTimeOriginal") or "")[:10].strip()
                        if day and date_lo <= day <= date_hi:
                            rp += 1
                            rj += has_jpg
                    counts[sp] = (p, j, rp, rj)
        except Exception as e:
            logger.warning(f"micro_trip: could not read master species counts: {e}")
        return counts


def format_summary(rep):
    if not rep.get("ok"):
        return f"Micro-trip ingest failed: {rep.get('error', 'unknown error')}"
    no_jpg = rep.get("identified_no_jpg", 0)
    lines = [
        "Micro-trip ingest (scratch repo, master not modified)",
        f"  review dir : {rep['review_dir']}",
        "",
        f"  pictures   : {rep['pictures']}",
        f"  identified : {rep['identified']}  ({rep.get('identified_with_jpg', 0)} with a JPG, "
        f"{no_jpg} without)",
        f"  no species : {rep['no_species']}",
    ]
    if no_jpg:
        lines.append(f"  (!) {no_jpg} identified picture(s) have NO JPG - these would become "
                     f"ghost species in the master. DxO-render them first.")
    unrec = rep.get("unrecognized") or {}
    if unrec:
        total = sum(unrec.values())
        lines.append(f"  (!) {total} picture(s) have a name that was NOT recognised - fix the "
                     f"name (or add the species), then re-scan. They show under the Species "
                     f"filter with a warning (!) marker:")
        for name in sorted(unrec):
            lines.append(f"        {unrec[name]:>4}  {name}")
    lines.append("")
    lines.append(f"    {'this folder':>14}   {'in master (all)':>14}")
    lines.append(f"    {'#':>6} {'JPG':>6}   {'#':>6} {'JPG':>6}   "
                 f"species  (in master = rarity cue: rich = expendable, near-absent = keep)")
    for sp in sorted(rep["species"]):
        rec = rep["species"][sp]
        names = " / ".join(x for x in (rec["nl"], rec["eng"]) if x)
        suffix = f"  ({names})" if names else ""
        flag = "" if rec["jpgs"] else "  <- no JPG"
        lines.append(f"    {rec['n']:>6} {rec['jpgs']:>6}   "
                     f"{rec.get('master_pics', 0):>6} {rec.get('master_jpgs', 0):>6}   "
                     f"{sp}{suffix}{flag}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import argparse
    import configparser

    scriptpath = os.path.dirname(__file__)
    reporoot = os.path.abspath(os.path.join(scriptpath, os.pardir, os.pardir))
    os.chdir(reporoot)
    sys.path.append(os.path.join(reporoot, "speciesviewer"))

    parser = argparse.ArgumentParser(description="micro-trip ingest of one directory (no master writes)")
    parser.add_argument("input_dir", help="directory of pictures to ingest")
    parser.add_argument("--config", default=os.environ.get("SVCONFIG", "svconfig.ini"))
    parser.add_argument("--keep", action="store_true", help="leave the scratch repo on disk")
    parser.add_argument("--recurse", action="store_true", help="descend into sub-folders")
    args = parser.parse_args()

    conf = configparser.ConfigParser()
    conf.read(args.config)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    def _clean(s):
        return (s or "").replace('"', "").replace("'", "").strip()

    master_root = _clean(conf.get("master", "masterbiotadatabase", fallback=""))
    web_root = _clean(conf.get("master", "masterbiotawebroot", fallback="/nature/")) or "/nature/"

    with MicroTrip(args.input_dir, master_root, web_root=web_root, keep=args.keep,
                   recurse=args.recurse) as mt:
        mt.ingest()
        print(format_summary(mt.summary()))
        if args.keep:
            print(f"\nscratch repo kept at: {mt.scratch_repo}")
