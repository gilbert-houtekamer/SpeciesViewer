# AI for a day — sharp, size, species (plan, 2026-11-24)

Status: AGREED 2026-11-24 ("mostly yes"); building step 1. Builds on [picture_score_plan.md](picture_score_plan.md)
(the score, the master AI…, the experiments and their results).

## Goal

On a day of new pictures (all of them, not the third that reaches the master), SpeciesViewer
suggests per picture **how good it is** and per pose **what it is**, so rating and naming help
each other: an out-of-focus picture needs no name, a confident name speeds up the rest of the
pose. GH: "rating + species proposals = game changer"; for a day like the Eempolder "almost all
automatic". Everything stays a suggestion until GH accepts it.

## What the experiments showed (2026-11-23)

- BioCLIP 2 names birds right first in 77 % within the country's eBird list (Gambia, 4,074
  poses); on a new day (Eempolder, 126 poses) "mostly correct", including a pallid harrier.
  Below 45–50 % confidence the names are nonsense; low confidence ≈ out of focus.
- The animal box (MegaDetector) does not improve ranking WITHIN a burst, but it is needed to
  judge ONE picture: sharp grass with a soft bird is out of focus (the lapwing), and it gives the
  bird's size.
- Both models run through ONNX Runtime without PyTorch, with the same answers (98.7–99.2 % the
  same first name; boxes overlap 0.99). 4.5 GB of model files, the same on Windows and Mac.

## The three measures (bars 0–100 %)

| Bar | Meaning | Scale |
|---|---|---|
| **Sharp** | sharpness INSIDE the animal box | percentile of your own master pictures from the same camera ("sharper on the bird than X % of what you kept") |
| **Size** | the animal's long side at the picture's own size | 100 px = 0 %, 2500 px = 100 % (log) |
| **Species** | BioCLIP's confidence in its first name | its own percentage; below the threshold (45–50 %, per day) = "not sure" |

No animal found = sharp 0, size 0, no name.

## Suggestions

- **Purple** (the same rules as the master AI…: only candidates — no stars above the day's
  usual, Green or no colour, no JPG): out of focus (sharp below a cut-off), too many of the same
  pose (keep the best by sharp, then size), and — new — small (size below a cut-off) when the
  pose has a bigger one.
- **A name per pose**: the best frame of the pose (sharp first, then size) is named, only when it
  is sharp enough; the other frames of the pose get the same name. "Same as the previous pose"
  when its first name matches.

## Where it shows

1. **Editor, Sharpness tab** — the box drawn in the picture; on the right the three bars and the
   top 3 species in words with percentages. Works for any picture (master, trip, culling).
2. **Culling grid of a day** — an **AI…** button, like the master's, but for all species of the
   day: statistics, the cut-offs and the species threshold top right; bins and a small name label
   ("kievit 92 %", or "?") on the cards; k/b as in the master. Then **Accept**: mark the bins
   Purple and/or give the confident poses their name, through the existing species route (so the
   recent-species list and species learning follow) — with undo.
3. Later: the master AI… can use sharp-in-box and size as a tie-breaker; the card ingest can
   name while reading.

## Building blocks (engine, no Qt)

| Module | One job |
|---|---|
| `engine/ai_models.py` | the two ONNX models (from tools/ml/onnx_models.py); DirectML / CoreML / CPU |
| `engine/ai_measure.py` | per picture: box, size, sharp-in-box, sharp percentile; per pose: top 3 names. Cached in app-data `scores/ai.sqlite` (path + size + mtime + model version) |
| `engine/ai_yardstick.py` | per camera: the sorted sharp-in-box of the master's pictures (rebuilt when the master changes) |
| `engine/ebird.py` | a region's species list + the eBird taxonomy, cached in app-data `ebird/` (offline later); key in `[ebird] api_key` |
| `engine/ai_names.py` | BioCLIP's scientific name → a species in your species list (exact, synonym, or "new: ask") |

## Installing

- `onnxruntime-directml` (Windows) / `onnxruntime` (macOS, Linux) in requirements.txt with
  platform markers; in the PyInstaller build.
- The models are an **optional download** into app-data `ai/` (like ExifTool: Setup checklist
  line + Settings; manual instructions in the install doc). Without them SpeciesViewer works as
  today and the AI buttons say why. Until we host the files: `tools/ml/svr_onnx_export.py` makes
  them on a machine with the experiment environment.

## Order of building (each step testable on its own)

1. **Engine + Editor**: ai_models, ai_measure, yardstick; the Sharpness tab shows box, bars and
   top 3 (read-only). Quick to see on the Eempolder pictures.
2. **eBird + names**: ebird.py, ai_names.py (matching to your list), the region of a day.
3. **Culling AI… read-only**: the day dialog, bins + name labels on the cards, cut-offs.
4. **Accept + undo**: Purple and names written for the day.
5. Installing: requirements, build, Setup checklist, Settings, docs.
   **Done 2026-11-24:** the models are hosted at https://houtekamer.com/speciesviewer/ai/v1/
   (`manifest.json` with size + SHA-256 per file; a new model version gets v2/). Put there by
   `tools/svr_ai_publish.py` (dry run → --apply; the name vectors at half precision: 3.2 GB
   instead of 4.4, no first name changed on 346 pictures). Fetched by `engine/ai_download.py`
   (resumable `.part`, checked before it gets its real name) from *File ▸ Settings ▸ AI
   Models…* and the Setup checklist row. Licences settled: MegaDetector MIT, BioCLIP 2 MIT,
   TreeOfLife-200M names CC0 (NOTICE.md).
   **v2, 16 bit (2026-11-24):** SmallBoy (laptop, built-in graphics) took 2.1 s per picture;
   the models at 16 bit run 1.9x faster there (936 -> 498 ms), same box 99 %, same first name
   97 % on two whole days (the changes mostly "not sure" coin-flips). 8 bit and a smaller
   MegaDetector input lose. Published as ai/v2/ (2.3 GB); `replaces` in the manifest removes
   v1's two models after the new set is complete. Cache keys unchanged on purpose.

## GH 2026-11-24

- A **new day** may be bolder than the master clean-up: insert the names at or above the
  threshold, assign the Purple suggestions, and maybe **Green for good pictures** — with undo.
  (Note: in the Purple rules Green counts as a candidate like no colour; if Green comes to mean
  "good" from the AI, the rules must treat it as a keeper for those pictures — decide in step 4.)
- **Version 2**: use what the master knows as a prior — a species seen on 26 buzzard days in
  the region weighs more than one never seen there.

## Open choices (GH)

- **Region of a day**: from the day's GPS country (the country table exists) → eBird code,
  with an override in the dialog. NL as a whole, not a province (GH 2026-11-23).
- **Non-birds**: eBird lists only birds. First version: a pose whose best name is below the
  threshold within the bird list is tried again over everything BioCLIP knows; a better source
  (regional lists per group) later.
- **Name matching**: BioCLIP uses older names for some species (Musophaga violacea = Tauraco
  violaceus). Match on the species list first, then GBIF synonyms (pygbif is pinned, offline
  cache exists); unmatched = "new species?" for you to confirm.
- **Hosting** of the 4.5 GB model download, and the licence of BioCLIP's name list — to settle
  before step 5.

## Status 2026-11-25 — and next (start here in a new session)

Done (all on SpeciesViewer04):
- **Other animals** (73f6a21): iNaturalist's species of the country per group
  (`engine/inat_lists.py`, 16 groups as on waarneming.nl), added to eBird's birds. Madagascar:
  non-birds right 21/30 → 54/70, birds unchanged. Country packs (`tools/svr_country_pack.py`):
  command line only — fetching a country takes about a minute, so no GUI needed.
- **AI button in Set species** (ad03e3b): while AI… is open, "AI: name · sci  NN %" in both
  Set species panels; a name not in your list is looked up in GBIF and added.
- **Plants** (8d0bc85): a pose with no animal is named from its whole sharpest frame among the
  country's plants, only when BioCLIP's own open first name agrees (≥ 15 %). Madagascar trip:
  264 poses without an animal, 36 named, all credible; missed birds left unnamed.
  `tools/ml/svr_plant_names.py` makes an HTML sheet to judge by eye.
- **Settings ▸ AI species lists**: per country, which groups the AI names (+ Add a country,
  Choose groups with All/None, Fetch lists now, Remove; "All other countries" row).

Next, in this order (GH 2026-11-25):
1. **"AI: name what I see"** in the large view (#1) — name only the part you zoomed in on (a
   manual crop, no file): IMG_7284 of Madagascar 2026-11-07 shows a Moringa drouhardii, the
   whole picture says Fony baobab 32 %. The models stay loaded after AI… → one picture is
   quick. The answer feeds the same AI button in Set species. Test on Madagascar.
2. **AI in Read SD Card** (#6) — the card read only MEASURES (animals, names, sharpness) and
   fills the cache; AI… then opens at once per day, reviewed one day at a time (2000 pictures
   cannot be reviewed in one go). Also slow on SmallBoy. Test on GH's pseudo SD cards of
   "2023 zomer".
3. Open question: on a pipeline day Accept writes colours only, no names (names only on a
   culling day). Accept never overwrites an existing name (by design, #5).
4. SmallBoy: `git pull`, then test the above (#7).

Noted, not planned: birds in bushes are often named wrong (the crop is mostly branches).
