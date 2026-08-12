# One species viewer, three windows on the master

**Raised** 2026-08-12 (user) · step A **DONE** 2026-08-12 · step B **DONE** 2026-08-12

## The idea, in the user's words

> "Why can't we re-use the master viewer, with the time-restrictions in the view
> range: day, album, or none. That requires a slightly different skin, but other
> than the queries nothing is different?"

Right, and the code agreed more than we did. `MasterSpeciesViewerDialog` never had
a data layer of its own: it builds an **empty** `fpr` shell and hands it to the
same `RatingEditorCollection` the album viewer uses, with `full=True`. In that
mode `rebuild()` ignores the loaded rows entirely and takes every card from
`db.pictures_for_species(sci)`. So the album viewer's *Master* scope was not
*like* the Master Species Viewer — it **was** it.

The only real difference was which query filled the card list. Which means an
album is that same query plus a where-clause, and no new query is needed at all:
`pictures_for_species(sci)` ∩ *the UPNs in the window*.

| scope | restriction |
|---|---|
| Day | that day's UPNs |
| Album | the whole date window's UPNs |
| Master | none — the species everywhere, album members tagged |

## Step A — the restriction IS the mechanism (done)

`RatingEditorCollection.set_master_scope(restrict_to, album_keys)` narrows the
per-species master query; `rebuild()` applies it. The album viewer then loads
**nothing**: it resolves its window once (which UPNs, per day and in total,
which species) and passes a set. For tanzania that replaced **4772 picture rows
+ 19655 file rows in memory** with one range query and two sets. `fpr` holds 0
rows. `_MasterSink`, `_load_album_from_master` and `_load_album_day_from_master`
— written that same morning — were deleted.

Editing followed for free, because an album's cards are now master cards and the
master write path already existed (XMP → `.dop` → recompute Rate →
`MasterDB.apply_rating`). It is gated on `master.readonly`, the same gate the
album *day* viewer uses, because the write goes to the same place. The old
`read_only=True` flag was doing two unrelated jobs and is split:

- `album_mode` — which master, which scopes, which species page, which lineage
- `_view_read_only` — may you edit, decided per load from the machine's policy

### What that fixed and found on the way

- **Album scope had write paths that could not work.** Set species / Move / Set
  rating / Add files would have rewritten a master file's XMP and then persisted
  through per-day CSVs an album does not own.
- **A latent crash in `rebuild()`** — it dereferenced `self._master_db` whenever
  a species name was known, which with `forced_sci` can happen before the master
  is attached. Only the Master Species Viewer could ever have reached it.
- **The grid emptied after every edit** (and after closing the large view):
  `_resort_if_needed` called `rebuild(filter_name)` **without** `forced_sci`, so
  in album mode the query returned nothing and every card was taken out of the
  layout. All rebuilds now go through one `_rebuild_collection()`; a re-sort that
  meets a key it has no card for falls back to a full rebuild instead of quietly
  dropping it. **Rule: never call `collection.rebuild()` directly.**
- **The card menu was two different menus.** In an album every card is a master
  card, so the code bailed to the master menu and lost *Set rating*, *Full
  resolution*, *Get location*, *Edit headline / description*. It now carries both
  halves — and `_editable_keys` had to stop treating master keys as
  non-editable, which in an album is every key.
- **One order, one wording.** All three card menus follow one block sequence
  (name it → judge it → look at it → describe it → its files → remove it), with
  the master-only actions slotted into the matching blocks rather than appended.
  "Add new renders to the master" is now "Add new files (scan folder)…", the
  same words the trip uses: it is the same job, and the destination is not the
  user's problem.
- **Green means "one of mine"** in both viewers — from Album, or from trip —
  against the grey "from master" references. (Two copies of the card UI exist;
  the framed builder returns early, so the legacy unframed one is dead code for
  these viewers. Both were updated.)
- **Ctrl+Home / Ctrl+End** (and arrows) in the Master Species Viewer, which had
  no `keyPressEvent` at all.

### The JPG rule, quantified

A picture with a JPG is never deleted or moved — the hard invariant. "JPG only"
therefore shows **exactly** the pictures that cannot be culled:

```
tanzania album window: 4772 pictures
  with a JPG (protected):   640
  without a JPG (deletable): 4132
```

With "JPG only" on, ✖ Blue can find nothing *by construction*. The dialog now
says so instead of a flat "No blue pictures". User's decision: **keep the JPG
safety.** Open question left deliberately: whether ✖ Blue should scan the
unfiltered set (the delete preview already shows thumbnails of exactly what
would go).

## Step B — one dialog (done)

`RatingEditorDialog` now takes `mode="trip" | "album" | "master"`, and the last
two are the same thing at different widths:

```python
self._album_mode  = mode in ("album", "master")   # cards come from the master
self._master_only = mode == "master"              # …with no window
```

Master mode is an album that never narrows — `_album_restrict` stays `None`.
`master_species_viewer.py` (839 lines) is **deleted**;
`open_master_species_viewer` builds the merged dialog.

What crossed over: the species type-ahead (an *editable combo* with a
contains-filter completer, not a bare line edit — so every `currentData()` caller
in the dialog is untouched and the list stays browsable), ↻ refresh, ↻ Web
regenerate, and the species-distribution map (master mode maps every point the DB
holds for the species, not the points of the pictures on screen; with no date
window those are different questions).

What the Master viewer gained: *Set rating*, *Full resolution*, *Get location*
and *Edit headline / description* on the card menu, in the same order as Album
and Trip.

Details that had to be preserved rather than merged away:

- **Per-mode window geometry** (`RatingEditor` / `AlbumSpeciesViewer` /
  `MasterSpeciesViewer`) for the dialog AND the large view, so the three windows
  stop overwriting each other's size and column count.
- **No placeholder row in master mode.** "— Select a species —" is noise in front
  of 2246 species where the type-ahead is the picker; the field starts empty on
  **no** selection (index -1), deliberately not on whatever species sorts first.
  Album and Trip keep theirs. `_species_count()` stops assuming the row exists.
- **The cull-confirm wording**, which would otherwise have said "this trip — all
  species" in the master.
- **Refresh reports counts, not an inventory** — "Refreshed — 1 new file."
  Refreshing one picture used to emit a paragraph naming every file; the names go
  to the log.

### The bug worth remembering

The dialog crashed on construction in master mode: the Master radio was checked
*after* `buttonToggled` was connected, so the handler ran and touched the day
combo eleven lines before it existed. The existing code sets the initial radio
**before** connecting for exactly that reason. PyQt aborts the process on an
exception in a slot, so there was no traceback — found with `sys.settrace`.

The Trip viewer stays separate: a trip genuinely owns its pictures (day CSVs,
write-back, frozen days). It is not a window on the master and should not pretend
to be.

## What is left

- ✖ Blue / ✖ Purple only see what the JPG filter shows, and a picture with a JPG
  is never deletable — so the cull finds nothing while "JPG only" is on. The
  dialog explains it; whether the cull should scan the unfiltered set is still
  open (see step A).
- *Get location* is inert in this dialog: `RatingEditorCollection` inherits the
  base `location_targets`, which returns nothing. Pre-existing in all three modes.

## Related

`docs/plans/album_master_direct_plan.md` (the read side, shipped earlier the same
day) · `docs/plans/album_rearch_plan.md` · `speciesviewer/gui/rating_provider.py`
