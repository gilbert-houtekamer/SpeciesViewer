# master_no_display.py: find master species that have NO picture to show on the
# website — the "Level.SPECIES X has no identified JPGs to show" log lines — and
# classify each by what is actually still on disk, so they can be SEEN and fixed.
#
# The website build flags a species when species.csv has pictures (count>0) but
# an empty BestJPG. That set is a mix, and only disk truth tells them apart:
#   - stale_counter : a JPG IS on disk; only the counter is wrong  -> recount / re-add
#   - raw_only      : only a RAW is on disk, no JPG                 -> render a JPG (DxO)
#   - orphan        : no files on disk at all                      -> true ghost, nothing to show
#
# Reporting only (review over auto-fix). The representative on-disk file lets a
# viewer render a preview straight from the RAW for the first two classes.

import os
import csv
import ast
import logging
from utils.ini_io import read_ini
from utils.raw_extensions import RAW_OR_HEIC

logger = logging.getLogger("speciesviewer")

_JPG_EXTS = (".jpg", ".jpeg")
_RAW_EXTS = tuple(RAW_OR_HEIC)  # tuple: .endswith() below requires one  # canonical table — see utils/raw_extensions.py


def _parse_files(raw):
    """data_pictures.csv stores Files as a python-repr list."""
    if not raw or raw == "[]":
        return []
    try:
        v = ast.literal_eval(raw)
        return [str(x) for x in v] if isinstance(v, (list, tuple)) else []
    except Exception:
        return [s.strip().strip("'").strip('"') for s in raw[1:-1].split(",") if s.strip()]


def _flagged_species(active_dir):
    """Species.csv Level.SPECIES rows the website would log: pictures claimed but
    BestJPG empty. Returns {key: {'name', 'english', 'claim'}}."""
    out = {}
    path = os.path.join(active_dir, "species.csv")
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            if r.get("Level") != "Level.SPECIES":
                continue
            try:
                claim = int(r.get("iPictureCollectionCount") or 0)
            except ValueError:
                claim = 0
            if claim > 0 and not (r.get("BestJPG") or "").strip():
                name = (r.get("ScientificName") or "").strip()
                out[name.lower()] = {"name": name,
                                     "english": (r.get("EnglishName") or "").strip(),
                                     "claim": claim}
    return out


def scan(master_root):
    """Find the flagged species and classify each by on-disk truth.
    Returns {ok, master_root, entries, totals} or {ok: False, error}.
    Each entry: {species, english, status, rep_path, n_pics, n_present, n_missing}."""
    active = os.path.join(master_root, "metadata", "active")
    sp_csv = os.path.join(active, "species.csv")
    pic_csv = os.path.join(active, "data_pictures.csv")
    if not os.path.isfile(sp_csv) or not os.path.isfile(pic_csv):
        return {"ok": False, "error": f"master CSVs not found under {active}"}

    try:
        flagged = _flagged_species(active)
    except Exception as e:
        return {"ok": False, "error": f"failed reading species.csv: {e}"}
    if not flagged:
        return {"ok": True, "master_root": master_root, "entries": [],
                "totals": {"flagged": 0, "stale_counter": 0, "raw_only": 0, "orphan": 0}}

    # Gather every on-disk file for each flagged species from data_pictures.csv.
    present = {k: [] for k in flagged}        # absolute paths that exist
    n_pics = {k: 0 for k in flagged}
    n_missing = {k: 0 for k in flagged}
    try:
        with open(pic_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                key = (r.get("WetSoortNaam") or "").strip().lower()
                if key not in flagged:
                    continue
                n_pics[key] += 1
                tp = (r.get("TargetPath") or "").strip().lstrip("/\\")
                for f in _parse_files(r.get("Files", "")):
                    cand = os.path.join(master_root, tp, f)
                    if os.path.isfile(cand):
                        present[key].append(cand)
                    else:
                        n_missing[key] += 1
    except Exception as e:
        return {"ok": False, "error": f"failed reading data_pictures.csv: {e}"}

    entries = []
    for key, info in flagged.items():
        files = present[key]
        jpgs = [p for p in files if p.lower().endswith(_JPG_EXTS)]
        raws = [p for p in files if p.lower().endswith(_RAW_EXTS)]
        if jpgs:
            status, rep = "stale_counter", jpgs[0]
        elif raws:
            status, rep = "raw_only", raws[0]
        else:
            status, rep = "orphan", None
        entries.append({"species": info["name"], "english": info["english"],
                        "status": status, "rep_path": rep,
                        "n_pics": n_pics[key], "n_present": len(files),
                        "n_missing": n_missing[key]})

    entries.sort(key=lambda e: (e["status"], e["species"]))
    totals = {"flagged": len(entries),
              "stale_counter": sum(1 for e in entries if e["status"] == "stale_counter"),
              "raw_only": sum(1 for e in entries if e["status"] == "raw_only"),
              "orphan": sum(1 for e in entries if e["status"] == "orphan")}
    return {"ok": True, "master_root": master_root, "entries": entries, "totals": totals}


_STATUS_LABEL = {
    "stale_counter": "has a JPG (counter stale — re-add to fix)",
    "raw_only": "RAW only (render a JPG)",
    "orphan": "no files on disk (orphan)",
}


def format_report(rep):
    if not rep.get("ok"):
        return f"Scan failed: {rep.get('error', 'unknown error')}"
    t = rep["totals"]
    lines = [
        "Master species without a website picture (no writes)",
        f"  master                 : {rep['master_root']}",
        "",
        f"  flagged species        : {t['flagged']}",
        f"    has a JPG (stale)    : {t['stale_counter']}",
        f"    RAW only (renderable): {t['raw_only']}",
        f"    orphan (no files)    : {t['orphan']}",
    ]
    for status in ("raw_only", "stale_counter", "orphan"):
        group = [e for e in rep["entries"] if e["status"] == status]
        if not group:
            continue
        lines.append("")
        lines.append(f"  {_STATUS_LABEL[status]}:")
        for e in group:
            eng = f" ({e['english']})" if e["english"] else ""
            lines.append(f"    {e['species']}{eng}  —  {e['n_present']}/{e['n_pics']} files on disk")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import argparse
    import configparser

    scriptpath = os.path.dirname(__file__)
    reporoot = os.path.abspath(os.path.join(scriptpath, os.pardir, os.pardir))
    sys.path.append(os.path.join(reporoot, "speciesviewer"))

    parser = argparse.ArgumentParser(description="master species without a website picture (no writes)")
    parser.add_argument("--config", default=os.environ.get("SVCONFIG", "svconfig.ini"))
    parser.add_argument("--master", default=None)
    args = parser.parse_args()

    config = configparser.ConfigParser()
    read_ini(config, args.config)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    def _clean(s):
        return (s or "").replace('"', "").replace("'", "").strip()

    master = args.master or _clean(config.get("master", "masterbiotadatabase", fallback=""))
    print(format_report(scan(master)))
