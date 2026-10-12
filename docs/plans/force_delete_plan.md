# Force delete — an unfinished trip or album, and a whole test master

*Opened 2026-10-12. Status: part A (trips AND albums) BUILT, uncommitted —
awaiting Gilbert's test; then part B. It DELETES, so
every step is shown before it happens.*

## Why

A test or a mistake leaves pieces behind that today's tools cannot clear.
Found in the test master SVlocal3:

- `madagascar` — its folder was deleted by hand, but it is still a row on the
  trip list, so its web name stays taken and the list shows a dead entry.
- `north-spaindb` — an album whose dates hold no pictures.

And when a test master has done its job, Gilbert wants to delete it **as a
whole** (e.g. SVlocal2).

## What exists today

| Tool | Removes | Leaves |
|---|---|---|
| Retire trip (delete data) | the trip folder, after the trip is in the master and its album exists | — (the normal end of a trip) |
| Remove trip from this machine (Retire's override) | the trip folder | the trip-list row and the published pages — **on purpose** (below) |
| Delete Album | the album folder (zip to `.trash`) and, optionally, its published pages | the trip-list row |
| Manage Trip/Album Publication (unadvertise) | the trip-list row | — but it finds the name by reading the folder, so it cannot help once the folder is gone |
| Master ▸ Masters ▸ remove | the master's **settings, caches and local website** (zipped to `.trash`) | **its collection, its trips, its albums, its pictures** — all of it |

### Found while checking: the test masters share their trips

| Master | Collection | Trips folder | Pictures folder |
|---|---|---|---|
| SVv3 | `C:\SV_tests\SVcreated` | `C:\SVtest\Trips` | `C:\SVtest\pictures` |
| SVlocal | `C:\temp\SVlocal` | `C:\SVtest\Trips` | — |
| SVlocal2 | `C:\temp\SVlocal2` | `C:\SVtest\Trips` | `C:\SVtest\pictures` |
| SVlocal3 | `C:\temp\SVlocal3` | `C:\SVtest\Trips` | `C:\SVtest\pictures` |

Every test master sees every other test master's trips (`najaar`, `autumn`,
`autumndb`, `spaindb`, …) — the source of "we are debugging a situation that
cannot exist" (2026-10-11). Each trip.ini does record its own master
(`master_repository_disk`), so the owner of a trip CAN be told.

## Part A — Delete Trip Completely / Delete Album (decided 2026-10-12)

**Two menu entries, one worker underneath** — logical separation for the user:

- **Trips ▸ Advanced ▸ Delete Trip Completely…** (new)
- **Albums ▸ Delete Album…** (existing, extended)

Each first **looks**, then shows what it found, one checkbox per part:

| Part | What | Default |
|---|---|---|
| 1. SpeciesViewer's files | the data folder; its **size is always shown** | ticked when found |
| 2. The row on the trip list | removed by **name** (works without the folder), index regenerated | see the web rule |
| 3. The published pages | `/trips/<name>/` | see the web rule |
| (Backup) | *Keep a backup zip (1.2 GB)* | **unticked** — only when asked |

One confirmation naming every ticked item, then a report. A failed part is
reported; the others still run; running again finishes the rest.

**A starting list of names** — folders on this computer AND rows on the trip
list — so `madagascar` (no folder) can be picked.

### The web rule — never delete the real website from the wrong computer

Gilbert's example: delete Brazil on the laptop, together with its web pages,
and the live site loses BigBoy's latest and greatest. So:

- **Website on this computer** (each master has its own local website):
  parts 2 and 3 are offered and ticked — that website belongs to this master
  alone.
- **Website on a server** (shared by every computer): parts 2 and 3 are
  **not offered**. The dialog says: *"Your website is on a server that all
  your computers publish to. Remove the trip there with Trips ▸ 5. Manage
  Trip Publication, on the computer that publishes it."*

### Never touched

- **Pictures** — SpeciesViewer never deletes pictures; the report names the
  folder.
- **The master** — a trip already added to it is allowed (decided), with the
  note that its pictures stay in the master.
- **An album made from this trip** — its web address belongs to the album:
  parts 2 and 3 greyed with that reason.

## Part B — delete a whole test master (to decide)

Today *remove master* clears only the settings, caches and local website. For
a test master Gilbert wants it gone **as a whole**. Proposal: the remove
dialog gets one extra, unticked option (sizes are examples):

> ☐ **Also delete this master's data** — 3.4 GB
> - collection `C:\temp\SVlocal2` (412 MB)
> - trips that belong to it: `autumndb`, `spaindb` (2.9 GB)
> - albums that belong to it: `north-spaindb` (2 MB)
> - Not deleted: the pictures folders `C:\SVtest\pictures\…` — delete those
>   yourself if they were test copies.

"Belongs to it" = the trip.ini / virttrip.ini names this collection as its
master. A trip of another master in the same folder is left alone and listed
as such. Refused for the main master, as remove already is.

**And stop the sharing for new test masters:** New Master suggests folders
of its own, all under one folder per master — e.g. `C:\SVtest\<name>\trips`, `…\albums`, `…\pictures`, and the
collection `C:\SVtest\<name>\nature` — so deleting one later is clean and
test masters stop seeing each other's trips.

## Built on what exists

- Part A: `engine/album_delete` (zip optional now, rename, remove — reused for
  trips), `engine/publish_index.unadvertise` split to take a name,
  `rmtree` on both site writers, `engine/trip_naming.used_web_names` for the
  list, `engine/trip_retire_scan` for the "open trip" handling.
- Part B: `utils/masters.remove` + the owner field already in every ini.

## Testing

On the current play-master SVlocal3: `madagascar` (trip list + pages, no
folder), `north-spaindb` (folder only), a published trip. Then Part B on
SVlocal2.

## Decided (2026-10-12)

1. Behind Advanced: *Trips ▸ Advanced ▸ Delete Trip Completely…*.
2. Separate dialogs for trips and albums, one shared worker.
3. A trip already in the master: allowed, with a note.
4. No backup zip unless asked; always show how much storage is involved.
5. Web parts only for a website on this computer (from his Brazil example).

## To decide

6. Part B: "Also delete this master's data" as an unticked option on remove
   (recommended), or a separate *Delete test master completely…*.
7. Part B: New Master suggests its own folders under `C:\SVtest\<name>\`
   (recommended), or leave the folders shared.
8. Order: Part A first, then B (recommended — B reuses A's worker for the
   trips and albums it deletes).

## Decided (2026-10-12, later)

6. Part B: delete everything of a test master EXCEPT the pictures (its
   website and blog data are test data too).
7. New masters get their own folders — sharing was convenient for testing,
   but should not be encouraged (a security exposure, in IT terms).
8. Part A first, then B.

## Built (2026-10-12) — part A, trips

- `engine/force_delete.py` — scan (folders + trip list + published pages,
  owner, album-made-from-trip), delete_folder (backup only when asked),
  remove_from_trip_list, remove_pages; the website parts refuse unless the
  website is on this computer.
- `engine/publish_index.unadvertise_name` — the trip-list removal by NAME;
  `unadvertise()` now shares its core.
- `gui/force_delete_dialog.py` — Trips ▸ Advanced ▸ Delete Trip Completely….
- Found while testing: the trip index banner pointed into whichever trip was
  open when it was regenerated (token 48) — after deleting that trip's pages
  it broke. It now uses the master's web root.
- Manual: chapter 4, Advanced.

### Rule 5, refined (2026-10-12, Gilbert: yes)

On the laptop (read-only SmallMaster) an album is only metadata and blogs —
only its folder may go; Create Album already needs the full master. So the
website parts are offered on a computer that OWNS the website: a website on
this computer, or a server website from the full master (BigBoy). Never from
a read-only SmallMaster. Same rule for trips and albums.

- Delete Album now opens the same dialog (kind "album"); it lists every album,
  starts on the open one, removes the trip-list row too, and no longer zips
  unless asked. `gui/album_delete_dialog.py` is gone; `engine/album_delete`
  stays as the folder worker.
- A trip/album whose ini names a master that does not exist on this computer
  (imported from BigBoy) is NOT "another master's".
- The server's rmtree only ever gets `<site>/trips/<plain name>`.
