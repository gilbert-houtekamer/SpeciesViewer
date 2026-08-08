# SpeciesViewer — "Days" (ad-hoc pictures straight to the Master)

A design note, now also the record of what shipped. Companion to
`menu_reorg_plan.md`.

---

## STATUS: SHIPPED (untested end-to-end for card copy — no test card yet)

- **Phase 1** (`beee137`): registry + Days list under Trips + Days menu
  (Register existing day…, Review & rate pictures…, Add to Master, Remove from
  list). Committing to Master auto-marks the Day done. Add-to-Master report
  trimmed to two number groups.
- **Phase 2** (`3c679bf`): "Copy day from card…" reuses the SD-card ingest in
  a days mode (date-bucketing forced on → YYYY-MM-DD folders; registers them
  and selects the newest).

Code: `utils/days_registry.py`, `widgets/DaysPanel.py`, `gui/days_process.py`;
wired in `gui/mainwindow.py`, `gui/menuspec.py`; hooks in `gui/add_to_master.py`
(auto-done), `engine/micro_trip.py` (report), `gui/trip_process.py` (days_mode).

Decisions as-built: (1) lean, no website steps; (2) YYYY-MM-DD only; (3) Register
= existing folder, Copy from card = date-bucketed ingest, both register; (4) Day
stays after Add-to-Master, marked ✓ done (no auto-ageing — explicit Remove);
(5) reuses the Add-to-Master engine; auto-mark-Done on commit was chosen (not a
separate tick); `Master ▸ Add Pictures to Master…` was KEPT as a standalone
one-shot alongside the Days flow.

Open / later: "Copy day from card" needs a real card to verify end-to-end; a v3
might relax "lean" (e.g. a time-based blog).

---

## The idea in one line

A **Day** is a folder of pictures for one date that you review and send to the
**Master** — without building a trip website. It sits beside Trips as its own
small, revisitable thing.

## Two lanes (why Days exists)

Pulling pictures off a card has two different goals. They share the copy, the
review/rating, and the species/GPS editing — they differ only in the **result**.

| | **Trip** | **Day** |
|---|---|---|
| Goal | a trip website + blog | just get keepers into the Master |
| Unit | a Trip (name, many days) | one date's folder |
| Steps | Read & Summarize → Write Blog → Build Website → Publish Day | review → **Add to Master** |
| Website | yes | no |
| Listed & revisitable | yes (Active Trip, day panel) | yes (new **Days** list) |

Days is the *lighter* lane: **review → Add to Master**. Nothing else.

## Locked decisions

1. **Keep it simple (KISS).** No website/blog steps for a Day. (A future v3 might
   add e.g. a time-based blog; not now.)
2. **A Day is always a `YYYY-MM-DD` folder.** No arbitrary folders with an
   assigned date — the folder name *is* the date.
3. **Two ways to make a Day:**
   - **Register** an existing `YYYY-MM-DD` folder (no copying — just start
     tracking it).
   - **Copy from card** — choose where to put it, the pictures land in a
     `YYYY-MM-DD` folder there, and it's tracked.
4. **A Day stays in the list after it's added to the Master** (removing it
   immediately confuses people). **No data is ever thrown away.** It leaves the
   list only by (a) an explicit user action, or (b) optionally, ageing out after
   a while. → *KISS default: explicit "Remove from list"; auto-ageing is a later
   nice-to-have.*
5. **Reuse the existing "Add pictures to Master"** engine, with small tweaks
   (e.g. marking the Day done afterwards).

## What makes it cheap: the folder holds the state

A Day needs **no `metadata/review` repository** like a Trip does. The ratings,
colours and species you set live in each picture's **sidecar (XMP)**, right in
the folder. So we only need:

- a small **list of Days** — `{folder, date, status}`, per machine (like Recent
  Trips), and
- on open, a **re-scan of the folder** — which is exactly what "Add pictures to
  Master" already does today with its temporary workspace.

"Go back to a Day" = re-scan the folder; your earlier work is already in the
sidecars.

## Reuse vs new

- **Reuse:** the review/rating viewer (stars, colours, species — the culling
  view), and the Add-to-Master scan + commit (`micro_trip.py` +
  `add_to_master.py`). This is most of the engine.
- **New:** the Days list (per-machine), the left-panel list + top-level menu, the
  card-copy step that targets a `YYYY-MM-DD` folder, and a tiny status
  (active / done).

## Where it shows up

- **Left panel:** a **Days** list under Trips, each row a date.
- **Top menu:** a **Days** menu (peer of Trip / Master / Virtual Trips).

## Wording — user words, not process words

Draft labels (all tentative — pick what reads plainest):

| Action | Draft label | Avoid |
|---|---|---|
| make a Day from an existing folder | **Register existing day…** | "ingest", "micro-trip" |
| copy a Day off the card | **Copy day from card…** | "SD ingest" |
| open the review/rating view | **Review & rate pictures…** | "culling", "preview dialog" |
| send keepers to the Master | **Add to Master** | (keep — "Master" is our word) |
| take it off the list | **Remove from list** | "un-register", "expire" |
| a Day's state | **Active / Done** | "inactive", "committed" |

Internally the code may still say "micro-trip" — it just never reaches the user.

## Still open (small)

- **Card copy target:** the card-copy step needs a place to create the
  `YYYY-MM-DD` folder — a chosen parent each time, or a remembered "Days
  location"? (Trip lane already remembers a pictures root; mirror that.)
- **"Done" behaviour:** does **Add to Master** mark the Day *Done* automatically,
  or is Done a separate tick? (Leaning: auto-mark Done, still visible.)
- **Relationship to today's `Master ▸ Add Pictures to Master…`:** most likely it
  becomes the **Add to Master** action on the selected Day; whether the old
  standalone menu item stays is a small call.

## Sequencing

Independent of the menu reorg. A self-contained feature: list + registry, the
two make-a-Day paths, wire the existing review view + Add-to-Master to the
selected Day, and the active/done status. The heavy lifting (review, commit) is
already built.
