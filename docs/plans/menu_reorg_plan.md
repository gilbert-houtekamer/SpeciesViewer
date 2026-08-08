# SpeciesViewer vNext — Menu reorganization plan

Synthesis of the 3-round menu_overview review. Source of truth for menu items:
`gui/menuspec.py`. This document = the conclusion + testable work packages.

---

## Conclusion

What started as "describe each menu line" turned into a coherent product
re-shaping. The through-lines that emerged from your annotations:

1. **Scope-based menus.** Every function has a natural scope — Day, Trip,
   Master, Virtual-trip, App. The menus should read that way instead of by
   accident-of-development.
2. **The day pipeline is a numbered flow** that ends in one **"Publish day"**
   (Freeze + Commit merged). The dev-era split steps (Freeze, Write Commit,
   Read Directory, Write Summary) get demoted to a legacy submenu, ready to
   delete.
3. **Retirement becomes one clean arc.** Delete-local + Add-to-Master freezes
   the data; then **Retire runs everything** (virttrip + blog + website +
   species-hyperlink finalize).
4. **File hosts the chrome.** Settings → File submenu; Admin (debug tools) →
   File submenu, shown only when an **"Enable debug functionality"** flag is on.
   One flag de-clutters several menus.
5. **Names are decoupled, not forced equal.** Three explicit identifiers per
   (virt)trip — internal id/folder, display name, web slug. This is the real
   fix for the ebro bug, and the Retire dialog should *suggest an editable name
   and show the read-only web pointer*.
6. **One review surface.** "Rating Editor" → **"Trip Species Viewer"**, promoted
   above Edit Species List, and eventually shared across trip/master/virttrip
   (viewer-convergence).
7. **The descriptions are the manual.** This CSV is the single source for both
   in-app Help and the written manual.

**Sequencing recommendation: finish the in-flight retirements on the CURRENT
code first, then build vNext.** Reasoning in the last section.

---

## Work packages (independently shippable + testable)

Each WP is sized to build, verify, and commit on its own. Risk = chance of
breaking a working data operation. UI-only WPs are near-zero risk.

### WP0 — Finish retirements on current code  (do FIRST)
- **Goal:** ebro, gambia retired (add-to-master → virttrip → retire); omandb,
  orlandodb retired (virttrip exists → just retire).
- **Acceptance:** each trip db gone; virttrip live + correct on site; master
  intact; status console shows "no local data".
- **Risk:** n/a (existing, proven path). The virttrip_mode fix already removes
  the ebro extract trap.

### WP1 — File menu becomes the home  (UI-only)
- **Changes:** Settings → `File ▸ Settings` submenu. Add `Open / Switch Active
  Trip / Recent Trips`. Move Admin items under `File ▸ Admin`.
- **Acceptance:** every relocated action still launches; active-trip switch works
  from the menu; nothing orphaned.
- **Risk:** low. Pure menuspec wiring.

### WP2 — "Enable debug functionality" flag + Admin grouping  (low)
- **Changes:** checkbox in Global Settings. Gate Testbed, Trip/Merged Status
  consoles, Audit Admin, Audit Species, Rebuild, Clean Orphans behind it (shown
  in `File ▸ Admin` only when on). **Heal Admin stays visible** (real fix).
- **Acceptance:** toggle hides/shows the gated items; gated items still run; Heal
  reachable with flag off.
- **Risk:** low.

### WP3a — Trip menu reorder + renames + yellow-panel columns  (UI-only)
- **Changes:** order Trip as Day-pipeline → Maintenance → Retire. Renames:
  Sync Sidecars → "Sync Lightroom & PhotoLab metadata"; Rating Editor → "Trip
  Species Viewer" (moved above Edit Species List); number the day steps. Group
  Manage Blogs + Empty Trip Data + Recreate Trip as "Maintenance". Update the
  yellow trip-overview panel column headers to match.
- **Acceptance:** menu + yellow panel read consistently; all actions still fire.
- **Risk:** low (labels/layout). Yellow-panel column rename must map to the same
  state keys — verify spheres still light.

### WP3b — "Publish day" = merge Freeze + Commit  (medium, engine)
- **Changes:** one "Publish day" action runs Freeze then Commit. Keep old Freeze
  / Write Commit (and Read Directory / Write Summary) in a `Trip ▸ Advanced
  (legacy)` submenu for the freeze-only edge case, flagged delete-when-unused.
  Optionally merge the .py modules.
- **Caveat:** Freeze and Commit are separate statemachine steps — collapse their
  state keys or the progress spheres break ([[statemachine]]).
- **Acceptance:** Publish day on a fresh day produces identical output to today's
  Freeze→Commit; spheres correct; legacy freeze-only still works.
- **Risk:** medium (touches the pipeline + statemachine).

### WP3c — "Ingest pictures" unify SD + folder  (medium)
- **Changes:** one "Ingest pictures" entry handling SD card *or* folder (it
  already takes any file). Require an active trip (small new guard). Confirm the
  uniqueness check behaves for the folder path.
- **Acceptance:** ingest from a folder and from an SD card both land correctly;
  no active trip → clear prompt; no duplicate UPNs.
- **Risk:** medium (the uniqueness check is the unknown you flagged).

### WP3d — "Add species hyperlinks to trip blogs"  (medium)
- **Changes:** rename Redo Blogs → "Add species hyperlinks to trip blogs". It is
  a *required finalize* step (next/prev blog links + scientific names) run once
  all species in the trip are known — not a beautify nicety. Add the same step
  to Virtual Trips.
- **Acceptance:** after running, trip blog pages have working prev/next + species
  links; virttrip equivalent works.
- **Risk:** medium (clarify "all days in a trip" scope; ensure idempotent).

### WP4 — Naming decouple + Retire-dialog name/web pointer  (medium, the ebro fix)
- **Changes:** make 3 identifiers explicit per (virt)trip — id/folder, display
  name, web slug — stored once. Retire dialog suggests an *editable* name + shows
  the *read-only* web pointer. Retire matcher keys on the explicit link, not the
  basename. Add explicit `tripsrootdirectory` / `virttriprootdirectory` config
  keys ([[project_trip_root_inconsistency]]).
- **Acceptance:** create a virttrip with name ≠ slug; extract + retire still
  match it; an ebro-style case round-trips without the wrong-root bug.
- **Risk:** medium. Touches the retire/extract path — build it *after* WP0 so
  you're not refactoring the tool while using it to delete data.

### WP5 — "Publish to Master" group  (medium, some design open)
- **Changes:** relabel the four add/merge items as a visible progression:
  (1) "Publish species names (preview)" → move to Trip, (2) "List trip on master
  site" → move to Trip, (3) "Merge trip pictures into master", (4) ad-hoc "Add
  pictures (no trip)". *Open:* the (4) micro-trip how-to is still unclear — design
  before relabel.
- **Acceptance:** each stage does exactly its one job; names read unambiguously.
- **Risk:** medium; (4) needs a design pass first.
- **DONE (partial, 2026-07-06):** "Add Trip Pictures to Master" (the 3-step
  merge dialog) gained a **"Run all"** button — runs ingest → build+freeze →
  commit in order, auto-advancing on success and stopping on the first failure
  (or a declined re-commit confirm). The relabel/move of the four items is still
  open.

### WP6 — Unified Retire (runs all steps)  (medium-high, after WP0)
- **Changes:** sequence Delete-local + Add-to-Master → data frozen → "Retire"
  performs virttrip + blog + website + species-hyperlink finalize in one arc.
- **Acceptance:** a fresh trip goes from "frozen" to fully-retired virttrip in one
  guided flow; master untouched.
- **Risk:** medium-high (destructive path) — do only once you no longer depend on
  the current Retire for live work.
- **DONE (2026-07-06):** "Retire Trip to Virtual Trip" now runs the whole arc
  from one confirm: svv1 make → svv2 extract → migrate blogs → svv3 write+upload
  blogs → svv4 build website → svv6 make clickable → svv5 add to master. Runs in
  a worker thread with one live spinner, stops on the first error (completed
  steps are safe to repeat). Master untouched. Verified end-to-end on Braakman.
  (svr7 local-file delete still stays a separate, later action.) Also fixed a
  latent bug: `_trip_picture_root` failed to find the CurrentPictures root
  because the relativized `WindowsFileName` column is no longer absolute — it
  now derives the root from `[project] picturedirectory`. Also fixed the trip
  banner/picture not reaching the site: retire wrote the image *names* into
  virttrip.ini but never copied the actual JPGs from the trip root into the
  virttrip folder, so svv5 uploaded nothing and the site used a default banner —
  it now copies both files across (skips an already-present image). Two follow-on
  publishing fixes (6ad9893): (a) the left-panel click handler reverted the web
  root to `/virttrips/<name>` on every select (double-published + stale `/trips/`
  pages) — now `/trips/<name>` like everywhere else; (b) svv3/svv4/svv6 ran with
  `inittrip=False` so `trip_dict` was empty and the banner fell back to
  keerkringvogel on blog + species pages — now load `trip_dict` read-only via
  `Trip.read_TripAdmin()` (NOT `inittrip=True`, which for a trip.ini-less virttrip
  fabricates keerkringvogel defaults and writes them into the master
  trip_admin.csv). Also recovered the missing banner/picture JPGs for all 8
  pre-existing virttrips by downloading them from the live site.

### WP7 — Virtual Trips: two scenarios + blog bubbles  (larger)
- **Changes:** Create Virtual Trip asks "from existing trip" vs "retroactive from
  master dates" and branches; fold Extract into Create. Blog bubbles: amber =
  auto-created/unreviewed, green = reviewed/has-text (mirrors translation spheres
  [[project_translation_override_deferred]]). "Add Virtual Trip to Master" detects
  the scenario-1 no-op.
- **Acceptance:** build a retroactive ("Sri Lanka") virttrip day-by-day; bubbles
  track review state; scenario-1 add-to-master is greyed.
- **Risk:** larger (new flow), but additive — does not threaten existing paths.
- **Status (2026-07-06):** the *step* code (Extract → Write Blogs → Build Website →
  Make Clickable → Add to Master) all exists and now produces correct banners/links.
  Banner is order-independent for retroactive trips (6042ae6): svv3/svv4/svv6 fall
  back to virttrip.ini's own banner via `Trip.ensureTripRegistered()` when the trip
  isn't in the master registry yet. STILL UNBUILT (the real WP7 work): the Create
  dialog scenario branch ("from existing trip" vs "retroactive from master dates")
  + fold Extract into Create; amber/green blog review bubbles; scenario-1
  Add-to-Master no-op greying. Needs an end-to-end scenario-2 test. NOT for tonight.
- **DONE (scenario 1, 2026-07-06):** the "from existing trip" (retire) scenario
  now completes end-to-end via WP6's elaborated Retire — including its own
  Add-to-Master as the final step. Still open: the scenario-2 retroactive
  day-by-day builder, the amber/green blog bubbles, and greying scenario-1's
  standalone Add-to-Master.

### Backlog (not scheduled)
- Trip-to-Master dashboard as a 3rd yellow-panel view.
- Decode Quality: 75% inspect vs 100% A/B ("not urgent but we should").
- Mac/secondary machine: full-DB migration (maybe a non-GUI script) + the
  "Set up SpeciesViewer on a travelling computer" wizard (Small Master + Install).
- Viewer convergence (the big one behind "Trip Species Viewer").

---

## Sequencing

**Phase 0 (now, current code):** WP0 — finish ebro, gambia, omandb, orlandodb.

**Phase 1 (vNext, safe wins):** WP1, WP2, WP3a — File/Settings/Admin, debug flag,
Trip reorder/renames/yellow-panel. Zero engine risk, immediately satisfying,
makes the app *look* like the new version.

**Phase 2 (pipeline behavior):** WP3b Publish day, WP3c Ingest unify, WP3d species
hyperlinks. These change behavior but not the destructive paths.

**Phase 3 (after trips retired):** WP4 naming decouple, WP6 unified Retire. Both
touch retire/extract — safest once retirement is off the critical path.

**Phase 4:** WP5 Publish-to-Master, WP7 virtual-trip scenarios. Then backlog.
