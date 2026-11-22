# Picture score — suggest the redundant pictures of a species

Status: AGREED (2026-11-22). Steps 1 + 2 built 2026-11-22 (`engine/picture_score.py`,
`engine/redundancy.py`, `gui/suggest_dialog.py`, "AI…" in the master Species Viewer),
committed 3884210. Review in the grid built 2026-11-22: bins / green frames on the
cards while the dialog is open, a day picked in the table moves the grid there — awaiting
GH's test. Next: refuse/add a bin per card, then "Mark the bins Purple" (+ undo). Nothing is marked or deleted
before step 3, and step 3 only ever sets Purple on candidates, with an exact undo.

## Problem

The master's `biota` holds 1.76 TB, 98 % of it RAW. About 1000 species have more than 50
pictures, about 700 more than 10 per day. Deleting similar pictures by hand freed ~3 % — too
much work. The goal is ≥ 500 GB, without losing a picture you would have kept.

"The best" is subjective, so the program only **suggests**; you judge, and the existing
Purple-delete route does the deleting.

## What the experiment showed (`tools/svr_picture_score.py`, c0b1023 / ee013ca)

- **Subject sharpness works**: the frame is cut into 16×12 tiles, each tile's detail is
  measured (Laplacian energy after a light blur, at 1600 px), and the mean of the sharpest
  3 % of tiles is "how sharp the subject is". On a soft background the bird stands out.
- **Poses**: frames ≤ 30 s apart that look alike (8×8 hash of a blurred frame, ≤ 25 %
  different) are one pose.
- **The OM-1 AF point is less consistent than the sharpest tiles** (it sits in the sky above
  the bird, on a branch in front of it). It stays **drawn** in the large view, as
  information, but no longer counts in the score.
- **Known limits** (GH, 2026-11-22):
  - a large bird with a sharp tail and a soft head scores as sharp (same for dragonflies:
    head or body); telling head from tail needs a model that recognises a head/eye — later,
    if ever;
  - two frames that look equally sharp at 1600 px can differ at the pixel level → step 5.
- The contact sheet with the large view (AF point, searched region, sharpest tiles) is
  "very instructive" — the way to build confidence.

## Rules

- **Keepers never change**: a picture with stars, Red, Yellow or a JPG.
- **Candidates**: no stars AND (Green OR no colour). Blue / Purple are already your verdict
  and are left alone (not counted as candidates).
- **How many to keep depends on how often you saw the species** — a desert sparrow seen on
  one day deserves more per day than a black-headed gull seen on 67 days. Default, besides
  your own keepers:

  `per day = 24 / √(days seen)`, kept between 3 and 20

  | days seen | 1 | 4 | 9 | 25 | 67 |
  |---|---|---|---|---|---|
  | kept per day | 20 | 12 | 8 | 5 | 3 |

  Every number is shown in the dialog and can be changed there, so you judge the cut-offs.
- Per day: the sharpest frame of every pose first (best poses first), then second-best
  frames, up to `per pose`; a frame below `soft` × its pose's best is never kept for its own
  sake.

## Steps

1. **Engine** — two small modules, each with ONE job, plus tests; the tool runs on them.
   - `engine/picture_score.py`: measure ONE picture (tiles, sharpness, isolation, pose
     hash) and cache it (app-data SQLite, keyed on path + size + mtime). Reads any format
     (RAW via the preview path, JPG, HEIC/PNG/TIFF).
   - `engine/redundancy.py`: pure functions over scored pictures — keeper / candidate,
     poses, the rarity rule, the per-day selection, and the statistics. No I/O, no Qt.
2. **"AI" button in the master Species Viewer** → a read-only dialog for the species in view:
   - statistics: pictures, days, places, date range; keepers (stars / Red / Yellow / JPG);
     candidates and their size; already Blue/Purple;
   - the settings, pre-filled by the rarity rule;
   - the result: poses, kept, suggested Purple, GB freed — overall and per day (day, place,
     pictures, keepers, kept, suggested) — recomputed instantly when a setting changes;
   - scoring runs in the background with progress; a second look is instant (cache).
3. **"Mark suggestions Purple"** in that dialog: only on candidates; remembers each
   picture's previous colour so **Undo** puts back Green or nothing exactly. Deletes nothing.
4. **4th Editor tab, "Sharpness"**: the large view with tiles, AF point and verdict.
5. **Pixel-level tie-break**: within a pose, measure only the subject tiles again at full
   preview resolution, so near-ties are decided the way the 100 % tab shows them. Compare
   frames of one pose on the *same* region rather than each frame's own best part.
6. **Later, when trusted**: Purple suggestions in the trip pipeline as soon as a day's
   species are known; a batch over the ~700 species with many pictures. No automatic run
   before GH says so.

## Notes

- Purple is SpeciesViewer's existing "delete later" colour; the Purple-delete moves files to
  the master trash and Empty Master Trash makes it final — so nothing in this plan deletes.
- Scores live in `%LOCALAPPDATA%/SpeciesViewer/scores/picture_scores.sqlite`: rebuildable,
  never in Dropbox.
