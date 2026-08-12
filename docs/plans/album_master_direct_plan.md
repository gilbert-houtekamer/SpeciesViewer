# Albums read the master directly — finishing the re-architecture

**Raised** 2026-08-09 · **planned for** Wednesday 2026-08-12 · status: **DONE 2026-08-12**
(built and verified headlessly over all 17 albums; awaiting a look in the app)

## The one-line problem

An album is supposed to be a *live date-range window on the master*. Three of the
four things that read an album now do exactly that. The fourth — the **Album
Species Viewer** — still reads the album's extracted per-day CSVs, so what you
see depends on whether those files happen to exist on this machine.

## How it stands today

| Reader | Source |
|---|---|
| Album overview (yellow panel) | master date-range query |
| Biota Browser (Album scope) | master date-range query |
| Website build (`svv4BuildWebsite`) | master date-range query (`batch.album_source`) |
| **Album Species Viewer** | **per-day CSVs**, master query only as a fallback |

The fallback was added in `6330654` because two albums have no day folders at
all and the viewer opened completely empty:

```
2014_oktober_brazilie   0 review days   ->  0 species   (now 1262 pics / 99 species)
kretadb                 0 review days   ->  0 species   (now  609 pics / 84 species)
the other 15            3-30 review days     unchanged
```

Note also that **every** album's `metadata/active/data_*.csv` is header-only
(858 / 367 bytes), so the older "fall back to active" path can never help.

## Why finish it

- **One source of truth.** Fifteen albums currently show whatever was extracted
  at some past moment; two show the live master. Same app, same dialog, two
  different answers about what an album contains.
- It is the exact "stale copy" class the album re-architecture set out to
  delete — see `docs/plans/album_rearch_plan.md`.
- Whether an album works should not depend on which folders survived a cleanup
  or a machine transfer.

## The work

1. Make `_load_album_from_master()` the **primary** path in
   `RatingEditorDialog._load_trip_scope()` when `read_only` (Album scope), not
   the no-CSV fallback.
2. Decide what the per-day CSVs are still for in an album. Candidates: nothing
   (delete the read path), or a cache for offline/no-master use. If they stay,
   say in the code which one is authoritative and why.
3. Check the album **day** viewer (`day_str` set) — it reads the same CSVs and
   has the same exposure.
4. Confirm nothing in Album scope writes back. It is `read_only=True` today, so
   there should be no path, but confirm rather than assume before removing the
   `_day_objects` dependency.

## What to watch

- **Counts will move** on the 15 albums that work today: the query returns every
  master picture in the range, the CSVs a subset frozen at extract time. That is
  the point, but it will look like a change — compare a couple of albums
  before/after and be able to explain the delta.
- `_day_objects` is used for day counts and write-back. Album scope needs the
  first, not the second.
- Speed: `open_master_db()` calls `ensure_fresh()`. Fine for one album; check it
  is not re-opened per day.

## What was built (2026-08-12)

All in `speciesviewer/gui/rating_editor.py`.

1. **The query is the primary source, in every scope.** `_load_trip_scope()`
   reads an album from the master before it looks at any CSV, and Day scope
   (`_load_album_day_from_master`) reads that one day from the same place, so
   switching scope narrows the view instead of changing where the truth comes
   from. The day picker itself is now the master's day labels in range, not the
   `review/` folders that happen to be on this disk.
2. **What the per-day CSVs are for: nothing, unless there is no master.** They
   are read only when this machine cannot answer the query — no master root, or
   no date range on the album. When that happens the status line says so in
   plain words ("read from this album's extracted day files because no master is
   available here, so it may be out of date"), because the numbers are then an
   old extract and not the album. Nothing else in the album path touches them.
   The album's `speciescollection.csv` is no longer read either: it existed for
   species editing, which an album does not do.
3. **The album day viewer** (`gui/virttrip_overview.py`) was already on the
   master query — `_day_data_from_master`. Only this dialog's own Day scope was
   left, and it is item 1.
4. **Album scope did write back — it does not now.** Rating, colour, headline
   and description were already blocked at the item level
   (`RatingEditorItem.can_edit_*`), but four paths were not: the selection bar's
   *Set species…*, and the card menu's *Set species…*, *Move to existing
   species…*, *Set rating…* and *Add new files*. Each would have rewritten a
   master file's XMP and then persisted through per-day CSVs the album does not
   own. They are hidden in the Album viewer. (*Get location* stays: in this
   dialog it is inert — `RatingEditorCollection` inherits the base
   `location_targets`, which returns nothing.)

5. **Where it says so.** The status line could not carry the provenance — the
   next thing every load does is overwrite it with "N species loaded" — so the
   source lives in the **title bar**: *Trip Species Viewer — Album · tanzania ·
   live from the master, 2025-11-01 … 2025-11-25*, or *⚠ old extracted day
   files — no master on this machine*. The status label itself was a hardcoded
   `#555` at 10px, near-invisible on the dark theme; it is now `theme.INK_SOFT`
   at 11px.

Speed: the master DB is opened once per dialog (`_album_master_source`) and
handed to the Master scope as well, so `ensure_fresh()` is not paid per day, per
reload or per scope switch. The per-day frozen probe is skipped for an album —
an album has no days of its own to freeze.

## What actually happened to the counts

The prediction in "What to watch" was the wrong way round: counts went **down**,
not up. The day CSVs in these albums are the *original trip's* full working set,
culls included; the master holds what was committed. So the album now shows what
the master has, which is also what its website publishes.

| album | CSV pics | master pics | species |
|---|---|---|---|
| safaridb | 13952 | 9356 | 248 → 246 |
| gambiadb | 10048 | 8701 | 263 → 237 |
| tanzaniadb | 6749 | 4772 | 309 → 308 |
| costa-ricadb | 6011 | 4615 | 225 → 219 |
| orlandodb | 1440 | 1168 | 51 → 39 |
| 2013_panama, srilanka | unchanged | unchanged | unchanged |
| 2014_oktober_brazilie | 0 | 1262 | 0 → 99 |
| kretadb | 0 | 609 | 0 → 84 |

Every CSV picture the query drops was checked, per album, against the master:
they are **not in the master at all** (orlando 272/272, safari 4596/4596,
tanzania 1976/1977). Whole CSV days vanish for the same reason — orlando
2024-03-11, safari 2024-07-22/23, tanzania 2025-11-01 are days nothing was
committed from. Two single-picture curiosities worth remembering, neither caused
by this change:

- `braakman/bannermeeuw` is in the master with **no DateTimeOriginal**, so it
  falls on no day and can never appear in an album. A banner picture — harmless
  here, but a picture with no camera date is invisible to every date-range read.
- one tanzania picture has **no TargetPath** and is dropped by the documented
  ghost filter in `pictures_in_daterange`.

### Why the Biota Browser shows three photos fewer

Album Species Viewer 4772, Biota Browser 4769, both 307 species — checked on
tanzania: the album viewer shows every picture in the date range, the Biota
Browser only the `biota/` tree. The three extra are landscapes filed under
`topics/landscape/2025/` (`img_2071`, `img_2103`, `nb122096`, 11–12 Nov). Both
numbers are right; they answer different questions. Any album with landscapes or
other topic pictures will show the same small gap.

## Related

`docs/plans/album_rearch_plan.md` · commit `6330654` (the fallback that made
this visible) · `speciesviewer/batch/album_source.py`
