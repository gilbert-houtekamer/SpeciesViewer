# master_recount.py: recount the master's species.csv counters from its pictures.
#
# ONE JOB: make species.csv say what data_pictures.csv holds. Picture counters
# are ADDED UP at each commit and never recounted, so deletes, trims and moves
# left them too high (2026-10-17: 116 species, ~1,800 pictures — Maghreb Magpie
# 569 stored, 94 real). The website uses them for its counts and to decide
# whether a species gets a page. This is the apply step that
# species_counter_audit (report only) was written to be followed by.
#
#   species   pictures, pictures with a JPG (counted as the audit counts them),
#             species count 1 when it has pictures
#   best pick redone only when it points at a picture that no longer exists
#             (a pick that still exists, chosen by hand or not, stays)
#   groups    counts summed from their children (by Up); a group's best pick
#             redone only when its picture no longer exists
#   untouched the "this run" counters (they belong to the pipeline), names, paths
#
# plan() changes nothing; apply() backs species.csv up first and writes it with
# the Dropbox-safe replace.

import csv
import os
import time
import logging

from engine.species_counter_audit import _actual_from_pictures
from utils.atomic_replace import replace_with_retry

logger = logging.getLogger("speciesviewer")

COUNTS = ("iSpeciesCollectionCount", "iPictureCollectionCount", "iPicturesWithJPGsCollection")
PICK = ("currentHighestRating", "BestJPG", "BestUPN", "BestJPGPathName")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _paths(master_root):
    active = os.path.join(master_root, "metadata", "active")
    return active, os.path.join(active, "species.csv"), os.path.join(active, "data_pictures.csv")


def _best_pictures(pictures_csv):
    """{species: (rate, BestJPG, UPN, BestJPGPathName)} — its best picture WITH a JPG."""
    best = {}
    with open(pictures_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter=";"):
            sp = (r.get("WetSoortNaam") or "").strip().lower()
            if not sp or not (r.get("BestJPG") or "").strip():
                continue
            cand = (_int(r.get("Rate")), r["BestJPG"], (r.get("UniquePictureName") or "").strip(),
                    r.get("BestJPGPathName") or "")
            if sp not in best or cand[0] > best[sp][0]:
                best[sp] = cand
    return best


def _recount(master_root):
    """(header, rows, changes) — rows recounted in memory, changes listed."""
    active, species_csv, pictures_csv = _paths(master_root)
    actual, upns = _actual_from_pictures(active)
    best = _best_pictures(pictures_csv)
    with open(species_csv, "r", encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh, delimiter=";")
        header, rows = rd.fieldnames, list(rd)
    before = {r["ScientificName"]: {c: r.get(c, "") for c in COUNTS + PICK} for r in rows}
    by_name = {r["ScientificName"]: r for r in rows}
    kids = {}
    for r in rows:
        kids.setdefault(r["Up"], []).append(r["ScientificName"])

    def gone(r):
        return bool((r.get("BestUPN") or "").strip()) and r["BestUPN"].strip() not in upns

    def set_pick(r, rate, jpg, upn, path):
        r["currentHighestRating"], r["BestJPG"], r["BestUPN"], r["BestJPGPathName"] = \
            str(rate), jpg, upn, path

    done = set()

    def walk(name):
        if name in done or name not in by_name:
            return
        done.add(name)
        r = by_name[name]
        children = [k for k in kids.get(name, []) if k in by_name and k != name]
        if r["Level"] == "Level.SPECIES" and not children:
            a = actual.get(name.lower(), {"pics": 0, "jpgs": 0})
            r["iPictureCollectionCount"] = str(a["pics"])
            r["iPicturesWithJPGsCollection"] = str(a["jpgs"])
            r["iSpeciesCollectionCount"] = "1" if a["pics"] else "0"
            if gone(r):
                b = best.get(name.lower())
                set_pick(r, *(b if b else (0, "", "", "")))
            return
        for k in children:
            walk(k)
        for c in COUNTS:
            r[c] = str(sum(_int(by_name[k][c]) for k in children))
        if gone(r):
            top = max((by_name[k] for k in children if (by_name[k].get("BestUPN") or "").strip()
                       and not gone(by_name[k])),
                      key=lambda k: _int(k["currentHighestRating"]), default=None)
            if top is not None:
                set_pick(r, top["currentHighestRating"], top["BestJPG"], top["BestUPN"],
                         top["BestJPGPathName"])
            else:
                set_pick(r, 0, "", "", "")

    for r in rows:
        if not r["Up"] or r["Up"] not in by_name:      # roots: biota, topics
            walk(r["ScientificName"])
    for r in rows:                                     # anything not under a root
        walk(r["ScientificName"])

    changes = []
    for r in rows:
        old = before[r["ScientificName"]]
        for c in COUNTS + PICK:
            if (old.get(c) or "") != (r.get(c) or ""):
                changes.append((r["Level"], r["ScientificName"], c, old.get(c) or "", r.get(c) or ""))
    return header, rows, changes


def plan(master_root):
    """What a recount would change — nothing is written."""
    _h, rows, changes = _recount(master_root)
    species = {n for lvl, n, c, o, v in changes if lvl == "Level.SPECIES"}
    picks = {n for lvl, n, c, o, v in changes if c == "BestUPN"}
    biota = next((r for r in rows if r["Level"] == "Level.BIOTA"), None)
    return {"changes": changes, "species": len(species), "picks": len(picks),
            "biota_pictures": biota["iPictureCollectionCount"] if biota else ""}


def apply(master_root):
    """Recount and write species.csv (backup first). Returns plan()'s summary."""
    header, rows, changes = _recount(master_root)
    active, species_csv, _p = _paths(master_root)
    if not changes:
        return {"changes": [], "species": 0, "picks": 0, "backup": ""}
    backup_dir = os.path.join(master_root, "admin", "status", "backup")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, time.strftime("%Y%m%d-%H%M%S") + "_species.csv")
    with open(species_csv, "rb") as src, open(backup, "wb") as dst:
        dst.write(src.read())
    tmp = species_csv + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    replace_with_retry(tmp, species_csv)
    species = {n for lvl, n, c, o, v in changes if lvl == "Level.SPECIES"}
    picks = {n for lvl, n, c, o, v in changes if c == "BestUPN"}
    logger.info(f"Recounted species.csv: {len(changes)} values in {len(species)} species changed, "
                f"{len(picks)} best picks redone; backup {backup}.")
    return {"changes": changes, "species": len(species), "picks": len(picks), "backup": backup}
