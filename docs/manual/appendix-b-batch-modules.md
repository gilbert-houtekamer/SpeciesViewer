# Appendix B — Menu items and the batch modules they run

When something goes wrong, the log talks in module names (`sv6Freeze`,
`svr9RedoBlog`) while the menu talks in verbs ("Publish Day", "Make the Blogs
Clickable"). This table translates.

It is also the honest picture of which menu items are *one* action and which are
a chain — useful when a step half-fails.

> What these steps read and write — the four CSVs and their columns — is
> [Appendix E](appendix-e-data_structures.md).

## The trip pipeline

| Menu item | Runs | Statemachine key |
|---|---|---|
| Create Trip | `sv1MakeReposity` | `MakeRepository` |
| Sync Lightroom & PhotoLab Metadata | `svu5SyncSidecars` | `SyncSidecars` |
| **1. Read & Summarize** | `sv2ReadDirExif` **then** `sv3WritePictures` | `ReadDirExif`, `WritePictures` |
| **2. Write Blog** | `sv4WriteBlog` | `WriteBlog` |
| **3. Publish Day** | `sv6Freeze` **then** `sv7WriteCommit` | `Freeze`, `WriteCommit` |
| **4. Make the Blogs Clickable** | `svr9RedoBlog` | — |
| Count Species | `svr2CountSpecies` | — |
| Publish Species Names | `svr5MergeSpeciesCollections` | — |

### The legacy singles

| Menu item | Runs |
|---|---|
| Advanced ▸ Legacy ▸ Read Directory | `sv2ReadDirExif` |
| Advanced ▸ Legacy ▸ Write Summary | `sv3WritePictures` |
| Advanced ▸ Legacy ▸ Freeze Data | `sv6Freeze` |
| Advanced ▸ Legacy ▸ Write Website Commit | `sv7WriteCommit` |

> `sv5BuildWebsite` no longer has its own menu entry. Freeze builds the website
> itself, so a separate step was redundant — but the module is still wired in and
> still runs (it produces the all-species index that `sv7` reads).

### Trip maintenance

| Menu item | Runs |
|---|---|
| Advanced ▸ Reset Trip | `svu6EmptyTripData` |
| Advanced ▸ Recreate Trip Repository | `svr8RecreateTripRepository` |
| Export Trip | `svr6ExportTrip` |

## Merge Pictures into Master

The four steps of the master pipeline dialog. Steps 2 to 4 run in **master
mode**, which points the same code at the master repository instead of the trip.

| Step | Runs | Mode |
|---|---|---|
| 1. Count Species (final verdict) | `svr2CountSpecies` **then** `svr7ArchiveVerdict` | trip |
| 2. Read & Summarize | `sv2ReadDirExif` **then** `sv3WritePictures` | master |
| 3. Build Master Website & Freeze | `sv5BuildWebsite` **then** `sv6Freeze` | master |
| 4. Commit to Master | `sv7WriteCommit` | master |

> Step 1 is deliberately *not* in master mode. In master mode it would scan the
> master's own review tree and report on the whole collection; in trip mode it
> reads the trip's day summaries against a still-pre-trip master, which is what
> makes the lifer verdict true.

## Albums

| Menu item | Runs |
|---|---|
| Create Album | `svv1MakeVirtualRepository` |
| Write Album Blogs | `svv3WriteBlog` |
| Make the Blogs Clickable | `svv6RedoBlog` |
| Build Album Website | `svv4BuildWebsite` |
| Export Album | `svv7ExportVirtualTrip` |

**Retire Trip to Album** runs the whole arc in one worker thread:

```
svv1 (create)  →  svv2 (extract master days)  →  migrate blogs
     →  svv3 (write blogs)  →  svv4 (build website)
     →  svv6 (make clickable)  →  svv5 (add to master)
```

It stops on the first error. Completed steps are safe to repeat, so a failed
retire can be re-run from the top.

## Publication

Both publication dialogs are the same class, and each direction is its own
module — listing and un-listing are separate operations, not one toggle.

| Menu item | List it | Remove the listing |
|---|---|---|
| Trips ▸ 5. Manage Trip Publication… | `svr4UpdateTrips` | `svrUnadvertiseTrip` |
| Albums ▸ Manage Album Publication… | `svv5AddToMaster` | `svv8UnadvertiseAlbum` |

## Master and utilities

| Menu item | Runs |
|---|---|
| Maintain ▸ Rebuild Entire Website (all pages)… | `svMasterRebuildWeb` (master mode) |
| Utilities ▸ Rebuild all Albums and the Master website… | `svrRebuildAllWebsites` |
| Create an Excel to keep track of birds seen. | `svu2avibase` |
| Create PhotoMechanic keyword files for next trip. | `svu8PhotoMechanic` |
| Create Small Master Database | `svu9SmallMaster` |
| Developer Tools ▸ Call Testbed for trip day | `svu7Testbed` |

## Actions with no batch module

These are implemented directly in the GUI layer and will not appear in the batch
log at all: the browsers and viewers, the delete and retire scans, all of
*Maintain Master Database* except the website rebuild, the Days lane, the
unfiled lane, card reading, Empty a Card, and Restore Work from Card.

> `sv0ReadSDCard` exists in `batch/` but the card ingest is driven from the GUI
> dialog, not through the batch runner.

## Naming conventions

| Prefix | Means |
|---|---|
| `sv1` … `sv7` | The core per-day pipeline, in order |
| `svr*` | Trip-level operations (report, merge, export, recreate, retire verdict) |
| `svu*` | Utilities |
| `svv*` | Album (virtual trip) operations |
