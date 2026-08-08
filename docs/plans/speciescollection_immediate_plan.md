# Immediate update of the species list — plan

Backlog item 11: *"Smarter immediate update of speciescollection — update the
recognition table the moment a species is learned."*

**Status:** decisions taken 2026-08-08 (§6). Not started.

---

## 1. Where a species is learned today

`speciescollection.csv` is the recognition table: 19,315 rows, 2 MB, the
kingdom→species tree with `Up` pointers. It is **not** mirrored into `master.db`
(see [`db/master_health.py:44`](../../speciesviewer/db/master_health.py#L44)) —
the CSV is the only copy, which keeps this simple.

A new species enters it through `sc.getSpecies()` → GBIF backbone lookup →
`addSpecies()`, which inserts the whole ancestor chain
([`speciesclassification.py:361`](../../speciesviewer/engine/speciesclassification.py#L361)).
Four callers do that, and they do **not** agree on what happens next:

| Where | Learns | Persists to |
|---|---|---|
| `sv3WritePictures` (batch recognition) | yes | the **trip** list, once, at end of run ([`sv3WritePictures.py:123`](../../speciesviewer/batch/sv3WritePictures.py#L123)) |
| Culling viewer, Set-species | yes | the **trip** list immediately — or the master's, if no trip is active ([`culling_species.py:848`](../../speciesviewer/gui/culling_species.py#L848)) |
| Species editor (rating editor / large viewer) | yes | **nowhere — the lookup is thrown away** ([`species_editor.py:407`](../../speciesviewer/gui/species_editor.py#L407)) |
| Edit (Trip) Species List dialog | no — edits names only | the file it was opened on |

The master only ever gains species through **Publish Species Names**
(`svr5MergeSpeciesCollections`), which reads the trip list, merges it over the
master's and rewrites the master file. Manual, typically once per trip.

**So the cost being paid:** every species the species editor resolves is
re-looked-up next run. Every species learned in one trip is re-looked-up in the
next until you publish. And the culling viewer writes to whichever list happens
to be in front of it, so where a species lands depends on whether a trip was
active.

---

## 2. The finding that makes this cheap

`readSpeciesCollection` **rebuilds every parent's `Leaves` list from the
children's `Up` pointers** on load
([`speciesclassification.py:190-196`](../../speciesviewer/engine/speciesclassification.py#L190-L196)).
The `Leaves` column is written out but never trusted on read — `Up` is the truth.

So adding a species does **not** require rewriting the file to fix parent rows.
It is enough to **append** the new species' row plus any ancestor rows not
already present:

- no 2 MB rewrite — and the master lives in Dropbox, where rewriting a 2 MB file
  per insect would be a sync-churn machine;
- an append is a single small write, far more tolerant of two processes than the
  current `updateSpeciesCollection` (delete `.old` → rename → rewrite,
  [`speciesclassification.py:249`](../../speciesviewer/engine/speciesclassification.py#L249)),
  which has a window where **neither** file is complete. Given the species.csv
  incident, that pattern should not run per-species.

Duplicate rows are harmless on read (last one wins) and get squashed the next
time the file is rewritten in full.

### Hazard to fix first

**H1 — the link loop is wrapped in a single `try`.** One row whose `Up` is not
yet in the dict raises `KeyError`, and the `except` sits *outside* the `for`
([`speciesclassification.py:186-210`](../../speciesviewer/engine/speciesclassification.py#L186-L210)),
so linking silently stops for **every remaining species** in the file. It logs
one line and carries on with a half-linked tree. Appending makes forward
references possible, so this must be fixed first: append ancestors before
descendants, *and* move the `try` inside the loop.

### On the negative-lookup list

An earlier draft called `names_not_found_list` a class-level-mutable bug. That
was wrong — `__init__` shadows it per instance, with a comment saying why
([`speciesclassification.py:98-106`](../../speciesviewer/engine/speciesclassification.py#L98-L106)).
The class-level declaration is a harmless leftover.

The real gap is narrower and matches what you described: the list is
**per instance**, and the GUI builds a fresh `SpeciesClassification` per viewer
session ([`culling_species.py:82-84`](../../speciesviewer/gui/culling_species.py#L82-L84)).
So a name GBIF does not know is re-queried once per instance, not once per name.
That is the "14 calls for the same invalid name" you want gone.

---

## 3. Design

**One writer.** A single `learn(name)` entry point in the engine that resolves
via GBIF and records the result, replacing three call sites that each decided
for themselves. Culling, species editor and sv3 all call it.

**Buffer during the session, flush once (§6.2).** `learn()` does not touch the
CSV per species. New species accumulate in a staging table for the life of the
viewer / batch run and are written in **one append** when it closes — the same
place other end-of-viewer work already happens. Fewer, larger Dropbox
interactions, and a crash simply means the names are learned again next run.

**Both lists, master required (§6.1, §6.3).** The flush appends to the master's
list and to the trip's working copy. If the master is not reachable, that is an
**error** — not a silent queue.

**Publish Species Names survives** for the case where a machine's list has run
ahead of the master and needs folding in later.

---

## 4. Work packages

**WP1 — safe appending (prerequisite).**
Fix H1. Add `append_species(path, rows)`: ancestors first, atomic append,
skipping rows already present. No behaviour change — the foundation everything
else stands on, independently testable.

**WP2 — one `learn()`, and the species editor stops forgetting.**
Introduce `learn(name)` with the session buffer; route culling and sv3 through
it; wire it into the species editor, which today throws its lookups away.
Smallest visible win: names resolved in the editor stop coming back.

**WP3 — flush to the master.**
The end-of-session flush appends to the master list as well as the trip's, and
errors when the master is unreachable. After this, "learned once, known
everywhere" is true. Retires the planned GBIF disk-cache: with the species in
the master table there is nothing left to re-look-up on the positive side.

**WP4 — one Edit Species List.**
`Trip ▸ Edit Trip Species List` and `Master ▸ Edit Species List` become one
entry. Both already open the same `SpeciesCollectionDialog` on different paths
([`menuspec.py:405`](../../speciesviewer/gui/menuspec.py#L405),
[`menuspec.py:708`](../../speciesviewer/gui/menuspec.py#L708)), so this is menu
wiring plus deciding what it does for a trip with no master.

**WP5 — negative-lookup cache (independent; first or last).**
Cache "GBIF does not know this name" for the whole process, not per instance.
Persisting between runs is a bonus if it falls out for free; it needs an expiry
so a name GBIF later learns is not shut out forever. Scheduled separately.

### Implementation note for WP2

You suggested an SQLite staging table. Worth deciding at build time: since a
crash is explicitly allowed to lose the buffer, a plain in-memory set gives the
identical externally-visible behaviour with less machinery. SQLite earns its
keep only if the buffer should outlive the process. **Recommendation:**
in-memory first, and reach for SQLite only if a real need to survive a crash
appears.

---

## 5. What this does not change

- `master.db` — the recognition table is not mirrored there.
- The website, the map, the counters — all read the collection, none write it.
- `sv1MakeReposity` still seeds a new trip from the master's list.

---

## 6. Decisions taken (2026-08-08)

**6.1 — The trip keeps its own copy.** SmallMaster already establishes that this
scale of duplication is acceptable. No strong feelings either way, but leaving
it in place avoids refactor creep.

**6.2 — Buffer during the run, flush at the end.** Not per species. Other work
already happens at viewer close. Batched writes mean fewer Dropbox interactions,
and a crash just means doing it again next run.

**6.3 — The master must be reachable; otherwise it is an error.** No silent
queue. Where a machine has run ahead, the existing merge dialogs fold it in.

**6.4 — Cache negative lookups, at least within a run.** Avoiding 14 GBIF calls
for one invalid name is the point. Between runs is less important — do it only
if it is nearly free. Note that invalid names are allowed in the GUI *on
purpose*, so they will keep occurring; the culling step's checking makes them
less likely there, not impossible.

### Spun off, not part of this

**Export / import a trip.** Raised under §6.3 and deliberately kept separate.
Belongs in the backlog as its own item.

---

## 7. Order

WP1 → WP2 → WP3 → WP4. WP5 is independent and can go first or last.

WP1+WP2 are worth doing even if WP3 were dropped: they fix the species editor
forgetting, and they end the "whichever list is in front of me" behaviour.
