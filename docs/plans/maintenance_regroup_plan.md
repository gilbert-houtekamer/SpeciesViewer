# Menu regroup — by design vs. repair (backlog 1.9, with 1.3, 1.5, 1.17)

*Status: **DONE 2026-11-21** (steps 1–3 + 1.17), agreed 2026-11-21 (decisions in the Excel's last column). Every menu item, one row each, with its
proposed place and name: [`menu_regroup.xlsx`](menu_regroup.xlsx) — the
**Your thoughts** column is for your remarks. 1.8 (Rescan master EXIF cache) is
done and no longer listed.*

## The problem

Maintenance items are spread over five places: *Master ▸ Maintain Master
Database*, the Master menu itself, *Settings ▸ Database*, *Utilities* and
*File ▸ Developer Tools*. They mix two different things:

- tools that are **needed by design** — geotagging from a tracklog, a trash, a
  website rebuild after an update, a backup — and
- **repairs** — tools that only exist because something drifted: dead rows,
  wrong counters, a GPS that stayed in the file. A user should rarely need them,
  and each one points at a write path that let the master drift.

Mixed together, the normal tools look like debug tools, and the repairs look
like normal work.

## The rule

Every item gets one of five kinds (sheet *Kinds* in the Excel):

| Kind | By design or repair | Where it lives |
|---|---|---|
| **Work** | By design | The normal menus, beside the work it belongs to |
| **Upkeep** | By design | The normal menus too — setup, trash, caches, backups, rebuild after an update |
| **Repair** | Repair | **Only** in one window: *Master ▸ Check the Master…* |
| **Developer** | — | *File ▸ Developer Tools* (debug on), Appendix C |
| **Legacy** | — | *File ▸ Developer Tools ▸ Legacy steps* |

**No repair sits in a normal menu, and no by-design tool hides in the repair
window.** Today 11 items are repairs (of 103 menu items and Settings buttons).

## What moves

- **Master ▸ Maintain Master Database** disappears.
  - By design → straight into the Master menu, in this order: *Geotag from
    Tracklog*, *Add Missing Location Names* (also runs by itself after a geotag
    when online; the item stays for catching up offline), *Manage Trip & Album
    Publication* (was Manage Site Publication — in the Master menu because it
    covers all trips and albums), *Master Trash…*, and *Rebuild Websites…*
    (one window: master and/or all albums; replaces *Rebuild Entire Website*
    and *Utilities ▸ Rebuild all Albums and the Master website*).
  - Repairs → *Check the Master…*: Refresh Review/Apply, Recount Species
    Counters, Fill Picture GPS from Files.
- **Master Trash…** (1.3): one window to restore or empty, the mirror of
  *Trips ▸ Trash*. Replaces *Empty Master Trash…* and the two trash buttons in
  *Settings ▸ Database*.
- **Settings ▸ Database** keeps its status lines, *Back up now* and *Flush
  now* (it sits beside the counter that shows a stuck copy-back). *Check now*
  and *Restore backup…* go to *Check the Master…*; *Rebuild master DB* goes to
  *Manage Caches…* — it is a cache.
- **Developer Tools** keeps only diagnostics. *Audit Species Counters* and
  *Clean Orphan Sidecars* are repairs → *Check the Master…* (Audit merges into
  Recount, which already reports first).
- **Albums ▸ Sync Placed GPS into the Index** is a repair (1.17) → *Check the
  Master…*, master-wide, counting only what will change.
- **Trips ▸ Advanced disappears.** Legacy → *Developer Tools ▸ Legacy steps*;
  *Recreate Trip Repository* is removed (Reset Trip + re-running the days does
  the same); *Delete a Blog…* (was Manage Blogs) goes right after *Reset Trip…*;
  *Delete Trip Completely…* takes Advanced's place.
- **Call Testbed** is removed (menu item, window, `svu7Testbed.py`): one-off
  checks run as scripts.

The proposed Master menu is drawn on the sheet *Proposed Master menu*.

### *Check the Master…*

1. Runs every check as a **report** first — nothing is changed.
2. Lists the findings, one line per kind ("12 pictures name a file that is
   gone", "3 species counters too high", …).
3. Offers each fix separately, **review then apply**, backing up first.
4. *Restore the Master's Lists from a Backup…* (pictures, files, species —
   the daily backups) lives here too. Master only: albums are views on the
   master, and trips are expected to have rough edges.

### The question behind each repair

Every repair exists because some write path once let the master drift. For each
one, ask: *which write let this happen, and can an invariant stop it at the
source?* Every one stopped at the source is one repair the user never needs.
First candidates: species counters (added at a merge, never subtracted at a
delete or move) and GPS left in the files (the merge should carry it up). This
is where 4.9 (SQLite + a change log, CSVs as the record) comes in.

## Names (1.5, including the old 1.4)

- **Look-alikes:** *Count Species* (Trips, Albums) → **Species per Day…**;
  *Recount Species Counters* leaves the menus for *Check the Master…*. *Delete
  Trip…* (To Master) → **Delete Retired Trip…**; *Delete Trip Completely…*
  stays.
- **Uneven wording:** Title Case everywhere (*Manage Caches…*, *Connection
  Status…*, *Read Card into Unfiled…*, *Unfiled Pictures…*); no sentences with a
  full stop (*Bird Checklist (Excel)…*, *PhotoMechanic Keywords…*); *Read
  SD-card or Folder…* → *Read Card or Folder…* (the window's title and heading
  too, and its label *Project pictures* → *Trip pictures folder*); *Manage
  Blogs* → *Delete a Blog…*.
- **Ellipsis rule:** an item that opens a window before it does anything ends
  in "…" (*Create Trip…*, *Export Trip…*, *Reset Trip…*, *Create Album…* …).

## Order of work

1. Names (1.5) — one commit.
2. The menu moves (Master, Trips ▸ Advanced, Developer Tools, Master Trash,
   Rebuild Websites).
3. The *Check the Master…* window.
