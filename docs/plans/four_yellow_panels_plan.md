# Four yellow panels: Unfiled, Days, Trip, Album

## Context

Days and Unfiled both work, but neither has a yellow panel. While you work an
ingest, the yellow panel keeps showing the *active trip* — the one thing that is
not what you are doing. Both lanes are driven from a modal dialog (Unfiled) or a
menu acting on an invisible "active day" (Days), so the work happens somewhere
other than where the app's attention is.

`sync_yellow_to_mode()` at `speciesviewer/gui/mainwindow.py:469` already states the
intended end-state and names this gap in its own docstring:

> THE INVARIANT: the left column leads. Whatever the mode strip says, the yellow
> panel shows that scope — **Day excepted, which has no yellow panel of its own
> yet (ch.15 Phase 1, deferred, not dropped)**.

So this is the deferred half of the ch.15 layout pass, plus a fourth lane the
strip does not have yet. Outcome: four pills — Unfiled, Days, Trip, Album — each
with a yellow table that reads the same way and is operated the same way
(double-click a cell = do that thing to that row).

**What stays different, deliberately:** Trip and Album show the days of *one*
selected trip; Unfiled and Days show *every* row there is. The left column stays
the same kind of thing in all four modes — a short, narrow list you think in — it
is just not a *filter* for the two new lanes. Selecting there and selecting in the
yellow table are the same act, mirrored both ways.

## Decisions taken (from the Q&A)

| Question | Decision |
|---|---|
| "Remove from list" for Unfiled | **No such column there**, and no delete anywhere in the lane. See *How a day leaves the inbox*. |
| Where the description lives | **A note file inside the day folder** — it travels with the day when filed. |
| Unfiled actions | **Three:** File under a trip · Track as a Day · Move elsewhere… |
| "Track as a Day" | **Registers the folder where it lies.** No move — a Day has no storage of its own. |
| A tracked day in the inbox | **Leaves the Unfiled list** — `scan_unfiled` subtracts folders already registered as Days. |
| The old Unfiled modal | **Retired.** The menu item switches to the Unfiled pill. |
| Left column, new lanes | **Keeps its list**, mirrored with the table both ways. |

---

## Column layout

Both new tables, double-click to act — the Trip/Album idiom (`cellDoubleClicked`,
never `cellClicked`; see `mainwindow.py:797`). Column 0 carries the full folder
path as its tooltip. Rows newest-first, matching today's inbox.

**Unfiled**

| 0 Day | 1 Pictures | 2 Trip | 3 Day | 4 Move | 5 About this day |
|---|---|---|---|---|---|
| `2026-05-25`, tooltip = folder | count | 📁 | 📅 | → | free text |

- **📁 File under a trip** — the existing `_PickTripDialog`, then `move_day_to()`.
- **📅 Track as a Day** — `days_registry.add_day(folder)` on the folder *where it
  lies*. No move, no `[days] location`, nothing to configure. The row then leaves
  the inbox (see below) and appears under the Days pill.
- **→ Move elsewhere…** — `QFileDialog.getExistingDirectory`, then
  `unfiled.move_day_to(folder, chosen)` unchanged. The family-outing case: the day
  goes off to wherever you keep such things and out of SV's world entirely.

**Days**

| 0 Day | 1 Pictures | 2 Add to Master | 3 Remove from list | 4 About this day |
|---|---|---|---|---|
| `2026-05-25`, tooltip = folder | count | 🔘 ready / ✅ done / ⚪ gone | ✖ | free text |

Column 1 double-click opens **Review & rate** on that row —
`ImagePreviewDialog(mw, image_dir=folder, trip_root_dir=folder, mode="all")`,
exactly what `days_process.review_day` (`gui/days_process.py:77`) and
`UnfiledInboxDialog.on_cull` (`gui/unfiled_view.py:199`) already construct.

Days column 2 reuses the existing sphere vocabulary from
`trip_overview.state_cell_symbol` (`gui/trip_overview.py:66`) so ⚪/🔘/✅ mean the
same thing in all four panels; ✅ comes from `days_registry.STATUS_DONE`, which
`add_to_master.MicroTripReviewDialog._commit` already sets
(`gui/add_to_master.py:204-212`).

Context menu on both tables (right-click): **Open folder**, the row's own
actions, and **Describe this day…**.

## The left column: a short list, mirrored

Because it is narrow, it stays short — one line per day, exactly the shape
`DaysPanel` already has. The lists are the *same collection* the yellow table
shows, which is fine: the left is the thing you scan and think in, the table is
where you act.

- **Days mode** — `DaysPanel` (`widgets/DaysPanel.py`) is kept **unchanged**: the
  `▶` active marker, `✓ done`, grey for a missing folder, its Open Folder /
  Remove from List context menu.
- **Unfiled mode** — new `widgets/UnfiledPanel.py`, built to the same shape and
  conventions (`_FOLDER_ROLE`, folder as tooltip, `sort_items` support, Open
  Folder on right-click). Rows from `unfiled.scan_unfiled()`. No `▶` active
  pointer — the inbox has no "active" concept, clicking simply selects.

**Mirroring**, both ways: clicking in the left list selects that row in the yellow
table and scrolls to it; selecting a table row highlights it in the left list.
For Days that stays anchored on `main_window.active_day_folder`, so the existing
`has_active_day` menu gate (`menuspec.py:122`) keeps working from either side.

Both are real lists, so the shared `A-Z / Recent / Frequent` strip
(`mainwindow.py:137-163`) needs no special-casing.

Entry points therefore live in the header button row above the yellow table, the
Trip/Album pattern:

- Days — `Register existing day…`, `Copy day from card…`, `Refresh`
- Unfiled — `Read a card…`, `Open inbox folder`, `Refresh`

## The description — `_dayinfo.txt`

New tiny module `speciesviewer/engine/day_note.py`:

```python
NOTE_NAME = "_dayinfo.txt"
def read_note(day_folder: str) -> str          # "" when absent/unreadable
def write_note(day_folder: str, text: str)     # empty text DELETES the file
```

The whole file *is* the description, so it is editable in Notepad and obvious in
Explorer. First line shows in the cell, the full text in the tooltip. `.txt` is
outside `unfiled.PICTURE_EXTS` and outside every image glob, so it is invisible to
the counters and to ingest.

The point of putting it in the folder rather than in a registry: it survives
`move_day_to()`. File "witstaartkievit" under a trip and the note arrives with it
— later it can seed the blog title. It also works across machines, which a
`days_<machine>.json` key would not.

Editable **in place** in the *About this day* column (flags set on that one item
only; the rest of the table stays `NoEditTriggers`), committed on `itemChanged`,
plus the context-menu entry for the same thing.

## Files

**New**

- `speciesviewer/engine/day_note.py` — as above.
- `speciesviewer/gui/overview_table.py` — `make_overview_table(headers)`: the table
  setup + selection stylesheet currently duplicated at `trip_overview.py:594-635`
  and `virttrip_overview.py:501-521`, plus `action_cell(symbol, tooltip, inert=False)`
  wrapping today's `cell_is_inert` idiom (`trip_overview.py:61`).
  There is no shared table class today; this stops the count going 2 → 4.
- `speciesviewer/gui/days_overview.py` — `populate_days_overview(main_window)`,
  built on the shape of `populate_trip_overview` (`trip_overview.py:528`):
  build widget → `self.days_table` → `yellow_tabs.addTab(w, "Days")` →
  `sync_yellow_to_mode()`. Rows from `days_registry.load_days()`.
- `speciesviewer/gui/unfiled_overview.py` — `populate_unfiled_overview(main_window)`,
  tab title `"Unfiled"`. Rows from `unfiled.scan_unfiled()` — still a pure disk
  read every time, no cache.
- `speciesviewer/widgets/UnfiledPanel.py` — the left-column list for the new mode.
  Copy `DaysPanel` and strip it: same `_FOLDER_ROLE` / tooltip / `rebuild(sort_mode)`
  / `_context_menu` shape, rows from `unfiled.scan_unfiled()`, no active-day pointer.

**Modified**

- `gui/mainwindow.py` — add `MODE_UNFILED`; the pill loop at `:113` becomes a
  4-tuple; `_MODE_STACK_INDEX` at `:460` gains Unfiled at index 3 with
  `UnfiledPanel` pushed onto `_left_stack` (`:175-177`); **`sync_yellow_to_mode`
  drops its `MODE_DAY` early-return** (`:486-487`) and maps all four modes to tab
  titles; `_apply_sort_mode` rebuilds the new panel alongside the other three
  (`:552`); both new overviews get populated during startup before
  `_restore_active_left_mode()`.
- `gui/menuspec.py` — `Utilities ▸ Unfiled pictures…` calls `_set_left_mode(MODE_UNFILED)`
  instead of opening a modal. The Days menu (`:1699-1717`) is kept as-is; its
  `has_active_day` gate keeps working because selecting a yellow row sets
  `main_window.active_day_folder`, and `DaysPanel`'s selection stays in step both ways.
- `gui/unfiled_view.py` — delete `UnfiledInboxDialog` (`:83`) and `open_unfiled_inbox`
  (`:296`). **Keep** `_PickTripDialog` (`:30`), `read_card_into_unfiled` (`:302`) and
  `ensure_currentpictures_root` (`:258`) — the new panel calls all three.
- `gui/days_process.py` — the five action functions already resolve
  `main_window.active_day_folder` through `_active_day` (`:22`), so the panel drives
  them unchanged. `add_day_to_master` / `remove_day_from_list` additionally call the
  panel's refresh.
- `engine/unfiled.py` — **no new move function needed**: `move_day_to(day_folder,
  target_root)` (`:185`) is already generic, so both "File under a trip" and
  "Move elsewhere…" are the same call with a different destination.
  `scan_unfiled()` (`:102`) gains one subtraction: skip a day whose folder is
  already in the Days registry. Extend the module docstring at `:9-18` while doing
  it — the invariant is that nothing here *writes* a claim, and reading someone
  else's registry to avoid double-listing does not break it. Say so, or the next
  reader will think it did.
- `utils/days_registry.py` — add `registered_folders() -> set[str]`, normalised
  with `normcase(normpath(...))`. `DaysPanel._norm_reg` (`widgets/DaysPanel.py:25`)
  already does this comparison privately; one shared implementation beats a second
  copy inside `unfiled.py`.

## "File under a trip" — offer to make the trip

Today, zero candidate trips means a dead-end message box ("Create the trip first
… then come back", `unfiled_view.py:226`). Add a **`New trip…`** button to
`_PickTripDialog` that launches `TripCreationDialog(mw, trip_create_spec, mode="create")`
(`gui/trip_process.py:107`), then re-runs `unfiled.trips_covering(day)` and
preselects the trip that now covers the day.

Optionally seed the new trip's from/to dates with the unfiled day. This does **not**
break that dialog's "create starts EMPTY" rule (`trip_process.py:113-126`) — that
rule exists to stop the *active trip's identity* being inherited, and a date
derived from the day you are filing is not another trip's identity. It must touch
only the two date fields, never the folder or web-root fields.

## How a day leaves the inbox — and SV never deletes one

Three exits. Two move the folder, one just claims it:

1. **File under a trip** — moved into that trip's pictures folder.
2. **Track as a Day** — *not* moved. Registered where it lies, and dropped from
   the inbox listing because something now claims it.
3. **Move elsewhere…** — moved to a folder you pick, and out of SV's world.

Exit 3 also happens without SV: move the folder in Explorer and the inbox is
simply smaller next time it scans. That is the folder-is-truth invariant working
as designed, not a case to defend against.

**Deleting an unfiled day is not an option SpeciesViewer offers.** No Discard, no
`.trash`, no confirm-and-remove — not in the columns, not in the context menu. A
day only disappears because *you* deleted the directory outside SV. This is a
rule, not a missing feature: the inbox holds the only copy of pictures that are
not yet in any trip, any Day, or the master.

Exit 2 is the one that needs care: because the folder does not move, the inbox
listing and the Days registry now describe the same directory, and only the
registry says so. Unregister the day (`Remove from list`) and it correctly
reappears in the inbox — nothing was lost, because nothing was moved. That
round-trip is worth an explicit test.

## Sequencing

1. `day_note.py` + `overview_table.py` (no UI change yet).
2. Days panel — smaller lane, registry already has everything.
3. Unfiled panel + retire the modal.
4. `New trip…` in `_PickTripDialog`.
5. *Follow-up, only once 1-4 test clean:* point `trip_overview.py` and
   `virttrip_overview.py` at `make_overview_table` too, so all four tables are one
   piece of code. Held back because those two are shipped and tested.

## Verification (F5, click-path)

1. **Unfiled** pill → left list and yellow table show the same 8 days, newest
   first, status line reads 299 pictures; hover a Day cell → full path.
   Click `2026-06-01` in the left list → that table row selects and scrolls into
   view; click a different table row → the left list follows. Try the
   `A-Z / Recent / Frequent` strip — the left list re-sorts, nothing breaks.
2. Double-click **Pictures** on `2026-06-18` → Review & rate opens on 59 pictures;
   close → row still correct.
3. Double-click **About this day**, type `witstaartkievit`, Enter → `_dayinfo.txt`
   appears in that folder; `Refresh` → text still there; open it in Notepad.
4. Double-click **Day** on `2026-05-31` (9 pictures) → **nothing moves**; confirm in
   Explorer the folder is still in `2026 unfiled\`. The row leaves the Unfiled list
   and appears under the **Days** pill, note and all.
5. Double-click **Trip** on `2026-05-25` → existing dialog, no covering trip → the
   amber warning and the new `New trip…` button; cancel out.
6. Double-click **Move** on a throwaway day → folder picker → move to a scratch
   folder → row leaves Unfiled, appears nowhere else, pictures are at the new path.
7. **Days** pill → `Add to Master` on the day from step 4 → commit → cell turns ✅,
   DaysPanel shows `✓ done`.
8. `Remove from list` on that day → confirm → row goes, folder still on disk, **and
   it reappears under Unfiled** (it is back to being unclaimed). Re-track it.
9. Switch Unfiled → Days → Trip → Album → restart → the last pill is restored and
   the yellow panel matches it (the `sync_yellow_to_mode` invariant, now for all four).
10. Move a day in Explorer with the app open → `Refresh` → the table agrees with disk.
