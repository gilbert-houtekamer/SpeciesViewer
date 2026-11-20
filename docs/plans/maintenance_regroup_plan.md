# Maintenance regroup — proposal (backlog 1.9)

*Status: proposal, 2026-11-20. Found while reviewing manual chapters 5–6.
Related: backlog 1.3 (restore in Settings), 1.8 (Rescan master EXIF cache), 4.9 (SQLite as working store).*

## The problem

Maintenance items are spread over five places: *Master ▸ Maintain Master
Database*, the Master menu itself, *Settings ▸ Database*, *Utilities* and
*File ▸ Developer Tools*. Most of them are not maintenance: they repair
something that crept in earlier. To a user they read as debug tools.

## The proposal — sort by what the user is doing

| Group | Items today | Where they go |
|---|---|---|
| **(a) Normal work** | Geotag from Tracklog · Add Missing Place Names · Undo Last Species Move · Empty Master Trash · Species Without a Website Picture · Manage Site Publication | The normal menus, beside the work they belong to |
| **(b) After an update** | Rebuild Entire Website (all pages) · Utilities ▸ Rebuild all Albums and the Master website | One item, *Rebuild websites…*, with its choices |
| **(c) Repairs** | Refresh Master Database — Review / Apply · Recount Species Counters · Fill Picture GPS from Files · Audit Species Counters · Clean Orphan Sidecars · album Sync Placed GPS into the Index · the daily health check · Settings ▸ Database ▸ Restore | One window, *Check the master…* |
| **(d) Developer only** | Trip Status Console · Merged Status · Call Testbed | Developer Tools, documented in Appendix G only |
| **Remove** | Rescan master EXIF cache | Nothing reads that cache (1.8) |

### *Check the master…*

1. Runs every check as a **report** first — nothing is changed.
2. Lists the findings, one line per kind ("12 pictures name a file that is
   gone", "3 species counters too high", …).
3. Offers each fix separately, **review then apply**, as every bulk repair does today.
4. Restoring a daily backup lives here too, not in Settings.

### The question behind each repair

Every repair in (c) exists because some write path once let the master drift.
For each one, ask: *which write let this happen, and can an invariant stop it at
the source?* Every one stopped at the source is one repair the user never needs.
This is where 4.9 (SQLite + a change log, CSVs as the record) comes in.

## Open

- Your reading of this proposal (the grouping, the names).
- Which group (a) items go to which menu.
- Whether *Check the master…* also covers albums and trips, or the master only.
