# One species viewer, three windows on the master

**Raised** 2026-08-12 (user) · step A **DONE** 2026-08-12 · step B **NEXT**

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

## Step B — one dialog (next)

Merge `master_species_viewer.py` (839 lines) into `RatingEditorDialog`: one
dialog, two entry points — Album (opens with a window) and Master (opens with
none). The album already has everything that viewer has except its type-ahead
species field, ↻ refresh and ↻ Web regenerate.

The concrete pull: the Master Species Viewer's card menu should also offer *Set
rating*, *Full resolution*, *Get location* and *Edit headline / description*.
Those handlers live on `RatingEditorDialog` today, so porting them means moving
them onto the shared mixin — which is B's work, not a patch before it.

The Trip viewer stays separate: a trip genuinely owns its pictures (day CSVs,
write-back, frozen days). It is not a window on the master and should not pretend
to be.

## Related

`docs/plans/album_master_direct_plan.md` (the read side, shipped earlier the same
day) · `docs/plans/album_rearch_plan.md` · `speciesviewer/gui/rating_provider.py`
