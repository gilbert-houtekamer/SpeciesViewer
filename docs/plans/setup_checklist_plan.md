# Setup checklist — first run for a new user

*0.4, follows the zero-data simulation (docs/plans/multiple_masters_plan.md).
Skeleton: Gilbert, 2026-10-11. Status: checklist window SHIPPED (gui/setup_checklist.py),
tested by Gilbert 2026-10-11.*

## Who it is for

A birder who has never seen SpeciesViewer, for whom the full product is too
much. SpeciesViewer is not Lightroom or PhotoLab: it is a *species* viewer. For
Gilbert it has taken over what PhotoMechanic (culling) and Lightroom
(cataloguing) did; PhotoLab stays for its JPG rendering and because it needs no
import into a database.

Most birders do not publish to the web. A website is therefore **optional**:
blogs only exist on the web, and the website steps are simply not needed by
someone who only wants their own collection.

## Levels — what a new user needs, in order

| Level | Menus | What it gives | Setup it needs |
|---|---|---|---|
| **1. Start** | File, Browser, Days, Master, Help (maybe Utilities) | Copy a day off a card, cull it, add it to the master, browse the collection. The Master menu is enough to maintain a database. | a master collection |
| **2. Core** | Trips, To Master | The trip pipeline: a trip of days, blogs, the merge into the master. | a trips folder |
| **3. Albums** | Albums | Conceptually built on trips, but also an alternative to them: *Days → Master → Album* works well on its own. | an albums folder |

A level whose setup is missing stays **greyed**, as today. Hiding those menus is
possible later; not proposed now.

```mermaid
flowchart LR
    D["Days<br/><i>card → culled day</i>"] --> M["Master<br/><i>the collection</i>"]
    M --> A["Albums<br/><i>level 3</i>"]
    D --> T["Trips<br/><i>level 2</i>"]
    T -- "To Master" --> M
    classDef start fill:#e3ead9,stroke:#6d8764;
    class D,M start;
```

## The checklist window

Help ▸ Setup checklist, opened by itself while the open master is not set up.
Every mark is derived from the real settings — never from "the user clicked
Next" — so the same window answers *"why is everything grey?"* at any later
moment, not only on day one. Each button opens the dialog that already exists
for that setting.

Rows (Gilbert's order; no order is strictly required). Marks: ✗ needed and
missing, ✓ done, – optional, *defaults* when a setting works untouched. Level
= which level the row unlocks.

```
Setting up SpeciesViewer                                                  level
 ✗  Master collection   where your pictures and species list live    [Create… / Choose…]   1
 –  Website publishing  optional — only to publish blogs and pages   [Set up…]
 ✗  Pictures folder     your working copies (e.g. D:\CurrentPictures) [Choose…]           2
 ✗  Trips folder        where trips are kept                         [Choose…]           2
 ✗  Albums folder                                                    [Choose…]           3
 ✓  exiftool            found: C:\Strawberry\…\exiftool.bat
    Decode Quality      defaults                                     [Change…]
 –  DxO PhotoLab        optional                                     [Locate…]
```

## Decisions (Gilbert, 2026-10-11)

1. **Website is optional**, and shown early in the list. What breaks without it
   is a separate question with its own plan: `docs/plans/website_optional_plan.md`.
2. **Create a new master** is close to creating a trip — most likely an option in
   the module they would share, not a new mechanism. Investigate first.
3. **Levels of the rows:** master collection → 1; pictures folder and trips
   folder → 2; albums folder → 3. The rows need no strict order.
4. **Decode Quality:** works on defaults; the row says so.
5. **exiftool** comes from the PATH today; the finished ZIP must ship it. Until
   then this is the row a new user fails on.

## Finding: Create Master (2026-10-11)

`batch/sv1MakeReposity.py` — the Create Trip module — already has a master
path: with `[master] MasterBiotaDatabase` empty it treats the folder as *the*
master (`Project.isMasterRepository()`). Tried in a scratch folder, no website:

- the data skeleton is created completely — `metadata/active/` with the empty
  CSVs and the 18,408-row starter `speciescollection.csv`, `admin/`, `biota/`,
  a local `web/` — and **opens as an empty master** (`MasterDB.ensure_fresh`:
  0 pictures, 0 files);
- it then **fails on the blog index** (`bloglib.updateIndexPage` needs a Trip):
  a trip-only step the master path never skipped — that path was started, never
  finished;
- the upload part is already skipped when the web root is empty — but
  `generate_svconfig_ini(master_mode=True)` falls back to `/nature/` and returns
  early when no master is configured, which is exactly a new user's situation;
- `species.csv` gets the correct header (`empty_species.csv` = `SPECIES_FIELDS`,
  just without a final newline — which is why a line count said 0).

## Built: Create Master Collection (2026-10-11, tested by Gilbert)

- `batch/sv1MakeReposity.py`: the master path finished — no blog index for a
  master; no upload while no website is set up (also lets **Create Trip** work
  without a website, where it used to fail on connecting).
- `engine/master_create.py`: empty or new folder only (refuses a folder with
  files — the module copies empty CSVs over whatever is there); sets the open
  master's database, read-only off; puts the setting back if creation fails.
- Master ▸ Masters ▸ **Create Master Collection…** — greyed while this master
  already has a collection (one master, one collection).
- `db/master_health.py`: a collection that never held a picture (empty tables,
  CSVs agree, **no files under `biota/`**) is "a new, empty collection" — the
  daily backup skips quietly instead of three red REFUSED lines on a new user's
  first start. The 2026-07-14 guard stands: empty tables with pictures on disk
  are still an error and still refuse the backup (tested both ways).

## Further findings

- **The starter `trip.ini` is personal and stale** (`dist/installation files/`):
  Gilbert's South-Africa trip, `c:/laptop/…`, and without `fromdate`/`todate`/
  `oneblog`. Create Trip writes its own, so it is not used there — but it ships
  in the ZIP. `Trip.write_TripAdmin` takes its columns from the FIRST trip, so a
  trip.ini without those fields fails on the first trip of a new master.
- **`tests/` cannot run**: `conftest.py` imports `sview`, a module that no
  longer exists. Nothing covers repository creation.
- **The master has no day view** (Gilbert, 2026-10-11): after adding a day
  straight into the master (level 1, no trip), there is no way to look at that
  day the way a trip shows its days. Matters for the Days → Master route.
- In the Master Species Viewer you can delete pictures (Gilbert deleted most of
  his 154 there, 2026-10-11) — but there is no day viewer for the master.

## Built: collection as part of New Test Master (2026-10-11, awaiting test)

Gilbert: creating the collection felt like configuration in the checklist,
while it belongs to creating the master. New Test Master… now has "Also create
a new, empty collection in this folder" (ticked by default) — one dialog, one
restart. `engine/master_create.py` split: `make_collection` (touches no
settings) + `create_master_collection` (open master, checklist) /
`point_master_at` (a new master's settings file). Name and folder are checked
before anything is made. `[Global]` joined the always-present config sections:
a settings file holding only `[master]` would otherwise crash the start.

## Review round (Gilbert, 2026-10-11)

- "New Test Master" → **New Master**; all GUI text written TO the user, not
  about "the new user" (standing rule from today). A second master may be a real
  one — birds and plants — so no "TEST" label: the title shows the master's name.
- The orange strip shows on a master's **first start only** (`masters.first_start`,
  flag `started` in masters.json); no nag on every start of a master you keep.
- Checklist: **no Create… button** — creating a collection is not configuration;
  it lives in Master ▸ Masters (and in New Master…). The row's text says where.
- **Pictures folder** row kept — Unfiled scans it and the GPS-log folder
  defaults to `<pictures>/gpslogs` — "needed for: unfiled days, trips". Its two
  fields moved from the Trip tab to a new **Pictures** tab in Repository Settings.

## Idea, not planned: permanent and temporary masters

Gilbert: maybe several "mains" one day — then the real distinction is
**permanent** (a collection you keep: birds, plants) versus **temporary** (to
try something), as a category the user can change. Today one master carries the
"main" flag. Decide once more than one master has been used for real.
