"""EXPERIMENT — do iNaturalist's country lists name the NON-birds better, without
spoiling the birds?

ONE JOB: for every pose (engine.redundancy.poses) of pictures that carry YOUR
name — the master's pictures of one country, or a trip folder whose sidecars
have names (dc:subject: English, Dutch or scientific, ", male" etc. ignored) —
take the sharpest frame, cut it to the animal (MegaDetector, cached), and let
BioCLIP 2 name it four ways:
  open      everything BioCLIP knows (~950,000 names)
  ebird     what SV did before: eBird's birds, weak → everything
  mammals   eBird's birds + iNaturalist's mammals of the country (the new default)
  all       eBird's birds + iNaturalist's animal groups (engine.inat_lists) of the country
Counts first name right / right in the top 3, for birds and for the rest apart.
READ-ONLY for pictures, sidecars and master; writes app-data
reports/inat_names_<region>_<time>.csv. Needs the AI models (File ▸ Settings ▸
AI Models…) and an eBird key.

  python tools/ml/svr_inat_names.py --region TZ                (master, Tanzania)
  python tools/ml/svr_inat_names.py --region MG --folder "D:/CurrentPictures/2026/2026 Madagascar"
  ... --min-records 3   --limit 200
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

SV = str(Path(__file__).resolve().parents[2] / "speciesviewer")
sys.path.insert(0, SV)
sys.path.insert(0, str(Path(__file__).resolve().parent))

ANIMAL_GROUPS = ["mammals", "reptiles", "butterflies", "moths", "dragonflies",
                 "grasshoppers", "bees", "beetles", "flies", "bugs", "insects",
                 "spiders", "molluscs", "fish"]
MODES = ("open", "ebird", "mammals", "all")


def collection(root: Path):
    """({name: sci} for scientific, English and Dutch names; {sci: class})."""
    from db.master_mutate import _read_collection
    rows = _read_collection(root)
    by_name: dict[str, str] = {}
    for s, r in rows.items():
        if (r.get("Level") or "") != "Level.SPECIES":
            continue
        for n in (s, r.get("EnglishName"), r.get("DutchName")):
            n = (n or "").strip().lower()
            if n:
                by_name.setdefault(n, s)

    def klass(s):
        seen = 0
        while s and seen < 15:
            r = rows.get(s) or {}
            if (r.get("Level") or "") == "Level.CLASS":
                return s
            s, seen = (r.get("Up") or "").strip().lower(), seen + 1
        return ""
    return by_name, klass


def folder_names(folder: Path, by_name: dict) -> dict[str, str]:
    """{picture stem: your scientific name} from the sidecars' dc:subject."""
    out = {}
    for f in glob.glob(str(folder / "**" / "*.xmp"), recursive=True):
        t = Path(f).read_text(encoding="utf-8", errors="replace")
        m = re.search(r"<dc:subject>(.*?)</dc:subject>", t, re.S)
        if not m:
            continue
        for n in re.findall(r"<rdf:li>(.*?)</rdf:li>", m.group(1)):
            n = n.replace("&#39;", "'").split(",")[0].strip().lower()
            if n in by_name:
                out[Path(f).stem] = by_name[n]
                break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--region", required=True, help="ISO country code, e.g. MG, TZ, NL")
    ap.add_argument("--folder", help="a trip folder instead of the master")
    ap.add_argument("--min-records", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="only the first N poses")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    from PIL import Image, ImageOps
    from engine import ai_measure as am, ai_models, ai_names, ebird, inat_lists
    from engine import redundancy as rd
    from engine.countries import iso_of
    from engine.picture_score import Cache, image_bytes, score_pictures
    from utils.cache_paths import app_data_dir
    from utils.config import _config

    t0 = time.time()
    region = a.region.upper()
    root = Path(_config.get("master", "masterbiotadatabase", fallback="").strip().strip('"'))
    by_name, klass = collection(root)
    if a.folder:
        import svr_bioclip_trip as bt
        pics = bt.folder_pictures(Path(a.folder))
        mine = folder_names(Path(a.folder), by_name)
    else:
        from db.master_db import MasterDB
        db = MasterDB(str(root))
        db.ensure_fresh()
        rows = [r for day in db.pictures_in_daterange("0001-01-01", "9999-12-31").values()
                for r in day if iso_of((r.get("Country") or "").strip()) == region]
        db.close()
        mine = {r["UniquePictureName"]: (r.get("WetSoortNaam") or "").strip().lower() for r in rows}
        pics = rd.from_master_rows(rows, str(root))
    pics = [p for p in pics if mine.get(p["upn"])]
    print(f"{len(pics)} named pictures; sharpness…", flush=True)
    cache = Cache()
    score_pictures(pics, cache)
    cache.close()
    groups = rd.poses(pics, rd.Settings().gap, rd.Settings().change)
    if a.limit:
        groups = groups[:a.limit]
    print(f"{len(groups)} poses ({time.time() - t0:.0f} s); the lists…", flush=True)

    if not ebird.species(region):
        print(f"No eBird list for {region} (key in the config?) — stopping.")
        return 1
    inat_lists.species.__defaults__ = (a.min_records,)     # experiment: try 1 / 3 / 10
    for g in ANIMAL_GROUPS:
        inat_lists.fetch(region, g, progress=lambda s: print("  " + s, flush=True))
    lists = {"open": None, "ebird": [], "mammals": ["mammals"], "all": ANIMAL_GROUPS}
    sizes = {m: (len(am._allowed(region, g)[0] or ()) if g is not None else 0)
             for m, g in lists.items()}
    print(f"names to choose among: {sizes} ({time.time() - t0:.0f} s)", flush=True)

    aic = am.AiCache()
    clip = ai_models.bioclip()
    out_rows = []
    tally = {m: Counter() for m in MODES}
    for i, g in enumerate(groups):
        rep = max(g, key=lambda p: p.get("sharp") or 0)
        sci = Counter(mine[p["upn"]] for p in g).most_common(1)[0][0]
        src = Path(rep["src"])
        boxes = aic.boxes(src)
        held = {}

        def picture():
            if "im" not in held:
                data = image_bytes(src)
                held["im"] = (ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
                              if data else None)
            return held["im"]
        if boxes is None:
            if picture() is None:
                continue
            boxes = ai_models.detector().boxes(picture())
            aic.put_boxes(src, boxes)
        box = am.animal_box(boxes)
        vec = aic.vector(src, box)
        if vec is None:
            if picture() is None:
                continue
            vec = clip.picture_vectors([am.crop_image(picture(), box)])[0]
            aic.put_vector(src, box, vec)
        cls = klass(sci) or clip.group.get(sci.capitalize(), ("",))[0]
        kind = "birds" if cls == "aves" else "other"
        row = {"pose": i, "picture": src.name, "yours": sci, "class": cls, "boxed": bool(box)}
        for m in MODES:
            if m == "open":
                top = clip.top(vec, k=3)
                names = [{"sci": s, "prob": p} for s, _c, p in top]
            else:
                names, _among = am.names_for(vec, region, root, k=3, groups=lists[m])

            def same(n):
                s = (n.get("sci") or "").lower()
                if s == sci:
                    return True
                fam = clip.group.get(n.get("sci", "").capitalize(), ("", ""))[1]
                _how, r = ai_names.match(s, root, fam)
                return bool(r) and (r.get("ScientificName") or "").lower() == sci
            ok1 = bool(names) and same(names[0])
            ok3 = any(same(n) for n in names[:3])
            tally[m][(kind, "n")] += 1
            tally[m][(kind, "1")] += ok1
            tally[m][(kind, "3")] += ok3
            row[m] = (f"{names[0].get('sci', '')} {names[0].get('prob', 0):.0%}"
                      if names else "")
            row[m + "_ok"] = "1" if ok1 else ("3" if ok3 else "")
        out_rows.append(row)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(groups)} poses ({time.time() - t0:.0f} s)", flush=True)
    aic.close()

    print(f"\n{region}: first name right / in the top 3")
    for kind in ("birds", "other"):
        n = tally["open"][(kind, "n")]
        print(f"  {kind} ({n} poses)")
        for m in MODES:
            t = tally[m]
            if n:
                print(f"    {m:8s} {t[(kind, '1')] / n:5.0%}  {t[(kind, '3')] / n:5.0%}")
    print("\nNot birds, pose by pose (✓ first right, 3 = in top 3):")
    for r in out_rows:
        if r["class"] != "aves":
            print(f"  {r['yours'][:30]:30s} " + "  ".join(
                f"{m}: {r[m][:30]:30s}{'✓' if r[m + '_ok'] == '1' else r[m + '_ok'] or '·'}"
                for m in ("ebird", "mammals", "all")))
    out = app_data_dir() / "reports" / f"inat_names_{region}_{datetime.now():%Y%m%d-%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0]) if out_rows else ["pose"])
        w.writeheader()
        w.writerows(out_rows)
    print(f"\n{out}  ({time.time() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
