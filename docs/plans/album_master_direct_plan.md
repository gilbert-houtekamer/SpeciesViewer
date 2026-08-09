# Albums read the master directly — finishing the re-architecture

**Raised** 2026-08-09 · **planned for** Wednesday 2026-08-12 · status: OPEN

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

## Related

`docs/plans/album_rearch_plan.md` · commit `6330654` (the fallback that made
this visible) · `speciesviewer/batch/album_source.py`
