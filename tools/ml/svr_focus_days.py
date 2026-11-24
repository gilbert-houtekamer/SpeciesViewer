"""Experiment: does a "focus went elsewhere" ratio cut hold across culled days?

ONE JOB: for three culled days (Eempolder 2026-11-23: colours; Madagascar
2026-10-31 and 2026-11-04: what is left = kept, .trash/<day> = discarded) count
how many pictures fall below ratio cuts (sharp in the animal box / the frame's
sharpest part) and how many of those were kept. Mode "conf" = the AI's most
confident animal (as SV does now); "best" = the sharpest of all animals found.
Needs the caches filled (svr_bird_mask_focus.py --prepare).

INVARIANT: read-only.

  PYTHONPATH=speciesviewer python tools/ml/svr_focus_days.py [conf|best]
"""
import sys
from pathlib import Path

sys.path.insert(0, "tools/ml")
sys.path.insert(0, "speciesviewer")
import svr_focus_check as fc  # noqa: E402
from engine.ai_measure import AiCache, animal_box  # noqa: E402
from engine.picture_score import Cache, fine_in, frame_top  # noqa: E402

RAW = (".orf", ".heic")
MODE = sys.argv[1] if len(sys.argv) > 1 else "conf"
M = Path("D:/CurrentPictures/2026/2026 Madagascar")
days = [(Path("D:/CurrentPictures/2026/2026 Winter/2026-11-23"), None),
        (M / "2026-10-31", M / ".trash/2026-10-31"), (M / "2026-11-04", M / ".trash/2026-11-04")]
sc, bx = Cache(), AiCache()
for folder, trash in days:
    side = fc.sidecars(folder)
    srcs = [p for p in folder.iterdir() if p.suffix.lower() in RAW]
    gone = set()
    if trash:
        tr = [p for p in trash.rglob("*") if p.suffix.lower() in RAW]
        gone = {p.stem.lower() for p in tr}
        srcs += tr
    rows = []
    for src in srcs:
        s, b = sc.get(src), bx.boxes(src)
        box = animal_box(b) if b else None
        if s is None or box is None:
            continue
        animals = [x["box"] for x in b if x.get("cat") == "animal" and x.get("conf", 0) >= 0.2]
        if MODE == "best":
            box = max(animals, key=lambda bb: fine_in(s["fine"], bb))
        k = src.stem.lower()
        lab = "Purple" if k in gone else str(side.get(k, {}).get("Label", "") or "").capitalize()
        conf = max((x["conf"] for x in b if x.get("cat") == "animal"), default=0)
        rows.append((k, fine_in(s["fine"], box) / (frame_top(s["fine"]) or 1),
                     lab not in fc.GOING, src.suffix.lower(), conf))
    print(f"== {folder.parent.name}/{folder.name}: {len(rows)} with an animal")
    for cut in (0.05, 0.1, 0.15, 0.2, 0.3):
        lo = [r for r in rows if r[1] < cut]
        print(f"  ratio < {cut}: {len(lo):3d} · kept {sum(r[2] for r in lo)}")
    low_kept = sorted((r for r in rows if r[2] and r[1] < 0.3), key=lambda r: r[1])
    print("  kept, lowest:", [(r[0][-8:], round(r[1], 2), r[3], round(r[4], 2)) for r in low_kept[:14]])
