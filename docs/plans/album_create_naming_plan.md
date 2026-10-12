# Album naming — the same ease as Create Trip, in both roles

*Opened 2026-10-12, after docs/plans/trip_create_naming_plan.md shipped
(9f03fec). Status: BUILT 2026-10-12, uncommitted — awaiting Gilbert's test.*

## The two roles an album plays

| Role | Made with | Origin | Blogs |
|---|---|---|---|
| **A. Album over an old period** (Panama 2013 — never a trip in SpeciesViewer) | Albums ▸ Create Album (or from *Find Trips in the Master*) | none | written for the album |
| **B. Album that follows a trip** (the trip is done and in the master) | To Master ▸ Retire Trip to Album | the trip (`source_trip_id`) | moved over from the trip |

Both draw their pictures from the **master** by date range; an album never has
a pictures folder of its own. That makes an album simpler than a trip: two
names, not three.

## Role B — already almost right

Retire Trip to Album already derives everything from the trip: the same web
address (fixed, because the trip's pages are already published there), the
same dates and texts, the link back to the trip. Nothing to type except,
optionally, the titles. Gaps:

1. **No Albums folder set** → it silently uses a `virttrips` folder beside the
   Trips folder, while the Setup Checklist suggests `…\albums`. Proposal: use
   the Albums folder; when none is set, say so and offer the suggested one
   (the checklist's "Use suggested"), instead of inventing a third name.
2. **The clash check must allow it**: a retired album takes over its trip's
   web name on purpose. Only Create Album (role A) checks for clashes.

## Role A — Create Album gets the Trip treatment

Today: *Album Root folder*, *New album folder* (`e.g. birds2024`) + **Create
Folder**, *Album Web root* — the same three hand-made names Create Trip had.

Proposal: the first tab becomes **Name & Dates**, reusing the Trip panel
(`gui/trip_name_panel.py`) without the pictures row.

| What | Suggested from "Panama 2013" | |
|---|---|---|
| SpeciesViewer's files | `<Albums folder>\panama-2013db` | the album's own data and web pages |
| Web address | `…/trips/panama-2013` | albums and trips share `/trips/` |
| Titles NL / EN | `Panama 2013` | editable on the Text tab |

- Same checks as a trip: the data folder must be new or empty; the web name
  must be free among trips, albums and the website's trip list — the message
  says where it is used.
- **Year.** An album is usually about the past, and a place you return to
  (Spain) clashes with the trip of that name. When the web name is taken and
  the name has no year in it, offer one click: *"Use spain-2015"* (the start
  year). For trips you chose no year; for albums the year is what tells two
  visits apart.
- **Check Range** sits beside the dates (as for trips) — for an album it
  matters even more: the range IS the content. Its one-line summary
  ("412 pictures on 9 of 11 days") tells you before Create whether the dates
  are right.
- **Create Album** makes the folder, copies the default picture and banner,
  titles the album, then reads *Created ✓*.
- From *Find Trips in the Master*, the picked row prefills name and dates —
  unchanged, it just lands in the new panel.

## Not in this plan

- Force delete of a trip or album that never finished (its own plan): the
  existing *Remove trip from this machine* override deliberately leaves the
  website's trip list alone — that is why `madagascar` stayed behind.
- Renaming an existing album.

## Decided (2026-10-12)

1. Album data folder: `<web name>db`, like trips.
2. Year help: the one-click "Use Spain 2015" on a clash — though in practice
   Gilbert picks a descriptive name ("Ebro and Pyrenees", "North Spain").
3. Retire Trip to Album without an Albums folder: offer the suggested one.

## Built (2026-10-12)

- `gui/trip_name_panel.py` gained `kind="album"`: data folder in the Albums
  folder (explicit key only), no pictures row; the year button serves both.
- Create Album = create only (name panel, Check Range on tab 1, Pick from the
  master fills the panel instead of creating a folder at once). The old
  Create Folder row and its helpers are gone.
- New **Albums ▸ Select or Update Album…** = the old dialog minus Create —
  without it, Create Album was the only place to edit an album's settings.
- Retire Trip to Album: no Albums folder → asks, then uses the suggested one.
- Manual chapter 7 updated.

### After Gilbert's first test (2026-10-12)

- Create Album refuses up front when the master holds no pictures in the
  range (it used to stop half-way, in the blog step, with a traceback).
- A pipeline step that stops with a user message logs that sentence, not a
  traceback, and the message box gives the reason.
- An album with no pictures in its range still gets its Album panel, saying
  why it is empty — it used to leave the other scope's panel on screen and
  repeat a warning on every refresh.
- Second test: Add Trip Pictures to Master no longer shows an orange "already
  committed" line right after committing there (it read as blame); opened
  later, it says so neutrally and names the right step. The trip list shows a
  trip without a title under its name instead of an empty link.
