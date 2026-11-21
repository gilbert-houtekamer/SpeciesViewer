# 6. Master

The master **as a collection**: the shared name list, adding pictures directly,
the viewers, and keeping it healthy.

The trip-to-master merge is not here — it lives in [To Master](05-to-master.md).
Browsing by taxonomy is not here either — it lives in [Browser](02-browser.md).
What remains is what you do *with* and *to* the collection itself.

| Item | Needs |
|---|---|
| Masters ▸ … | Nothing |
| Edit Species List | A master |
| Other Species Names… | A master |
| Add Pictures to Master… | A master |
| Species Viewer | A master |
| Species Map | A master |
| Species Without a Website Picture… | A master |
| Undo Last Species Move… | A move to undo |
| Geotag from Tracklog (GPX/GPS log)… | A master |
| Add Missing Location Names… | A master |
| Manage Trip & Album Publication… | A master |
| Master Trash… | Deleted master pictures |
| Rebuild Websites… | A master |
| Check the Master… | A master |

---

## Masters

*Needs: nothing.*

One computer can hold several masters — for example one for birds and one for
plants, or one to try things in. Each has its own settings, its own trips and
albums and its own database cache, so working in one never touches another.
Exactly one master is your **main master**; any other one shows its name in the
window title, and — the first time you open it — an orange strip under the
menu bar.

| Item | What it does |
|---|---|
| *the list of masters* | The open one is ticked. Click another to switch: SpeciesViewer saves, closes and starts again with it. |
| New Master… | A name, and how it starts: with **SpeciesViewer's default settings**, or with **this computer's settings** copied from the main master (PhotoLab, window sizes; no data folders, no website). Ticked by default: **also create a new, empty collection** in an empty folder, so the new master is ready in one go. The folder follows the name — `C:\SpeciesViewer\<name>\nature` (`~/SpeciesViewer/<name>/nature` on the Mac) — and the master then gets its **own** `trips`, `albums` and `CurrentPictures` folders beside it, so masters never share each other's trips. Without a collection, the Setup Checklist opens to guide you; its suggestions also stay inside the master's own folder. |
| Make "…" the Main Master… | Shown when the open master is not the main one. Moves the "main" flag; nothing is moved or deleted. |
| Rename Master… | The name only; nothing on disk moves. |
| Remove Master… | Not for the main or the open master. You choose with the buttons: **Remove settings only** zips its settings, remembered state and local website to the trash and leaves its data on disk; **Remove with SpeciesViewer's files — <size>** (for a test master) also deletes what SpeciesViewer made for it — its collection and the trips and albums that belong to it: lists, blog texts, web pages and the pictures it copied into them — permanently and without a backup, listed with their sizes and picture counts first and confirmed once more. Your pictures folder, with the ratings, species names and landscape codes stored with the pictures, is never touched. But a master's collection is the backup of what you added to it: if you cleared those pictures from your pictures folder afterwards, the collection holds the only copies — the confirmation says so when it holds pictures. Cancel is the default. Never deleted: its pictures, trips or albums of another master in the same folders, and any folder another master still uses. |
| Restore Master… | Greyed until a master has been removed. Brings it back from the trash under its old name (or "… (restored)" when that name is taken), never as the main master. The zip is deleted once the master is back. |
| Create Master Collection… | Greyed while this master already has a collection. Choose an **empty** folder: it becomes a new, empty collection with the starter species list, and this master's database. No website needed. |
| Repository Settings… | Where the open master's data lives, one tab each: the master, the pictures folder (with the GPS-log folder), the trips folder, the albums folder. The single most consequential settings dialog — most "nothing is enabled" states trace back to a path here being empty. |

---

## Edit Species List

*Needs: a master.*

The species name list: scientific name, English name, Dutch name. You can edit
the English and Dutch names. The same one list as
[Trips ▸ Edit Species List](04-trips.md) — see there for why there is only one,
and what happens to the active trip's copy when you save.

The list also keeps **local names**: the name a guide used ("koereiger" for a
cattle egret). They are not shown in this editor, but in **Other Species
Names…** (below). You add one when you name a picture and SpeciesViewer does not
know the name — choose **Local name…** and pick the species it means. From then
on that name finds the species everywhere.

Each master has its own list. A new master starts with SpeciesViewer's starter
list (birds and mammals in the order of the world lists, with their Dutch and
English names), and learns every new species you name from then on.

## Other Species Names…

*Needs: a master.*

Every other name that leads to a species, one row per name: the name, its
language, the species it means with that species' English and Dutch name, what
kind of name it is, and where it came from. Two kinds live here:

- **Your local names** — added with *Local name…* while naming a picture, or
  with **Add a name…** in this window.
- **Names the taxonomy update left behind** — old scientific names, older Dutch
  names, subspecies that became a species. Untick *Only names you added* to
  see them.

These names only help you **find** a species when you type one; they never
appear on a website.

The **filter** looks in every column, so typing *wilde eend* shows every name
that leads to the mallard. To remove a wrong name: right-click it ▸ **Remove…**
(or the Delete key). *Add to selection* in the same menu removes several at once.

A removed name **stays in the list, greyed, with the date**. The names table
travels with every trip and to the laptop, and is merged back each time; a name
that was simply deleted would come back with the next merge. Marked as removed,
the removal travels too. Adding the same name again brings it back.

---

## Add Pictures to Master…

*Needs: a master.*

Ingest **any folder, or a dragged pool of files**, straight into the master —
no trip involved. The folder is treated as an ephemeral micro-trip: scanned,
identified, ingested, copied.

The dialog is **non-modal**. It stays open while you edit in the Trip tab, so the
loop of *scan → fix identifications → re-scan → commit* is workable rather than
a series of separate sessions.

> **This item is more capable than its name.** It is the shortcut route into the
> collection, bypassing the trip repository entirely, and the same copy rules
> apply as a full merge. Combined with the master's own website build, it is
> enough on its own to grow a complete species website — no trip ever needs to
> exist.
>
> It is also the **rescue tool**: identified local pictures that a trip's delete
> step refuses to remove (because the master does not hold them) get in this way.

Uses for it, roughly in order of how often they come up:

- A "working day" that never justified a trip
- Leftovers from a moved or abandoned trip
- Old flat folders of mixed dates — the recommended route for these, rather than
  the card reader
- Rescuing anything the master is missing

---

## Species Viewer

*Needs: a master.*

The master's editable species view — every species in the collection with its
pictures, where ratings, colour labels and species assignments can be changed
with write-back to the files.

First choose a species with the selector at the top left. Then you can cull,
correct mistakes, and more. What only the master view does:

- **Move to another species…** (card menu) — for a wrong identification. All
  files of the picture move, JPGs included, and the species pages follow. Works
  on a whole selection, and the target may be a species the master never had.
  [Undo Last Species Move…](#undo-last-species-move) puts it back.
- **Uncertain only** — shows only the species with a picture marked *probable*
  or *possible*, or named only to a group ("*Oplurus* sp."), to settle them one
  by one.
- **Map…** — place master pictures that have no GPS, on the map.
- **Crop, Make JPG and Rescan** in the Editor, on a master this computer may
  write to. Make JPG adds the new JPG to the master as the website picture and
  offers to update the species page; Rescan picks up a JPG you exported from
  PhotoLab.
- **Blue / Purple delete** — moves the pictures to the master's trash. Pictures
  that have a JPG are left alone unless you tick *Also move the N pictures with
  a JPG*. The page of each species involved is rebuilt right after.

It is the same viewer as the [Trip Species Viewer](04-trips.md) and the
[Album Species Viewer](07-albums.md), pointed at a different scope. What you
learn in one applies to all three.

Its scopes, filters and the master card context menu are in
[10. The viewers](10-viewers.md#the-species-viewers); its keys are in
[viewer_keybindings.md](../viewer_keybindings.md).

## Species Map

*Needs: a master.*

A distribution map: pick one to four species and see their GPS points, on a
native OpenStreetMap slippy map.

This is the screen that makes missing GPS visible. Pictures whose *picture row*
carries no coordinate simply do not appear here, even if the RAW file itself has
one — which is exactly what
[Check the Master ▸ GPS in the Files, Not in the List](#check-the-master)
is for.

Pictures of the chosen species **without** GPS wait in a tray beside the map.
Drag one onto the map to place it, or find a place by name (it starts from the
place already written on the picture). Right-click a tray picture — or press
`o` — for **Select this outing**: every tray picture taken in the same run (no
gap over 30 minutes), so one drop places the whole outing. A position the camera
recorded itself is never moved.

*Suggest a position* guesses the spot from the pictures with GPS taken just
before and after. A guess is only a guess: when there is a GPS log for the day,
[Geotag from Tracklog](#geotag-from-tracklog-gpxgps-log) gives the real
position.

## Species Without a Website Picture…

*Needs: a master.*

A report of species the master knows about but cannot show on the website,
classified by *why*:

- **stale** — the picture it points at is no longer where the record says
- **raw only** — the species has pictures, but none of them is a JPG the website
  can display
- **orphan** — the record has no usable file at all

This is a report with a viewer attached, so you can look at what it found. It is
the practical answer to "why is this species page empty?"

## Undo Last Species Move…

*Needs: a species move to undo; grey otherwise.*

Puts the pictures you last moved to another species back where they were — files,
counts and pages — including a species the move emptied or created. Repeat it to
undo earlier moves, newest first.

## Geotag from Tracklog (GPX/GPS log)…

Brings in **outside** information: it matches master pictures to an external
**GPX or OM-1 GPS log** by time. Not a repair — the camera simply did not know.

It is the only way to cover:

- a day the camera's own GPS was never switched on, and
- **altitude**, which the camera never records at all.

Review-first: nothing is written until you confirm. When pictures got a
position, *Add Missing Location Names* opens next and starts looking up their
place names straight away — still review-first. Without internet it says so, and
you run it later from the Master menu.


## Add Missing Location Names…

Looks up the place for master pictures that have GPS but **no location, city or
country** — the ones that show as bare coordinates on the species map.

Review-first: nothing is written until you confirm the table. It only touches
pictures that have no place name at all.

> **It never overwrites a hand-entered location.** There is one truth for a
> picture's place, and a name you typed wins over a name a geocoder guessed.


## Manage Trip & Album Publication…

The site index seen whole: **everything currently listed** on the website's trip
index, whether or not you can still open the thing behind it.

Use it to remove a listing whose trip or album no longer exists locally — the
case the per-trip and per-album publication dialogs cannot help with, because
they act on something you must first be able to select.

The live index is always the base for any write, so listings published from
another machine are not silently dropped.

## Master Trash…

*Needs: deleted master pictures; grey otherwise.*

A delete in the master only moves the pictures to the master's trash. This window
shows them, newest first, with a picture of each — the mirror of
*Trips ▸ Trash (deleted pictures)*:

- **Restore selected** puts the ticked pictures back, with their counts and their
  place on the species page.
- **Empty trash…** makes every delete final. It says what will go in plain
  numbers — "Permanently delete 1,256 pictures of 11 species?", with the file
  count, size and delete dates — and Cancel is the default.

*Settings ▸ Database* opens the same window.

## Rebuild Websites…

*Needs: a master.*

Re-renders and re-uploads the **master site** and/or **every album's site**, so a
fix to a page template or the rendering code reaches all of them. You choose:
**the master site** (every species page — it can take an hour), and **the
albums**, with their species pages (uploads pictures; slow the first time, then
only what changed) and/or their blogs (text only).

**Show what would be rebuilt** lists them first; **Rebuild…** does it, after a
confirmation. Which album is active does not change, and an album that fails is
reported while the sweep carries on. HTML and pictures only — no master data is
changed.

Reach for it after a SpeciesViewer update that changed how pages look. A single
album is rebuilt with [Albums ▸ Build Album Website](07-albums.md).

---

## Check the Master…

*Needs: a master.*

The **repairs**, in one window. You only need one when something drifted — every
one of them is a question for SpeciesViewer: which write let this happen, and can
it be stopped at the source?

The window runs every check as a **report** when it opens — nothing is changed —
and shows one line per check: ✅ nothing to repair, ⚠ what it found, or why it
could not check. Each line has its fix: the tool below, which shows what it would
do and asks first. After a fix, every line checks itself again, because one fix
can change another line.

**Hand-Placed GPS Not in the List** is the exception: it reads sidecars and takes
a minute or two, and rarely finds anything, so it waits until you press its
**Check now**.

| Check | What it looks at | Fix |
|---|---|---|
| **Lists vs. Files on Disk** | Rows whose file is gone, has moved, or sits under another species | *Review…* and *Apply…* (Refresh Master Database) — Apply is the only destructive fix |
| **Species Counters** | The species list's picture counts against the pictures really there | *Recount…*; *Full report…* lists every difference |
| **GPS in the Files, Not in the List** | A camera position that never reached the picture itself | *Fill…* — additive only |
| **Hand-Placed GPS Not in the List** | A place name or movable flag in a picture's sidecar, but not in the list | *Check now*, then *Copy…* — copies only what the sidecars hold |
| **Orphan Sidecars** | `.xmp`/`.dop` files left after their picture was deleted | *Move to trash…* |
| **Lists vs. Read-Copy** | The master's lists against SpeciesViewer's fast read-copy, and against themselves | *Details…* |

At the bottom: **Restore the Master's Lists from a Backup…** — see below.
*Settings ▸ Database* opens the same window.

The quick checks (all but the on-request one) also run at every start, silently
unless something drifted: *File ▸ Settings ▸ Global ▸ Validate the master's lists
at start*.

### Lists vs. Files on Disk — Review…

Compares the master's admin CSVs against the pictures actually on disk, and
reports what a refresh would change:

- **REMOVE** *n* dead file rows — the file is nowhere
- **REPOINT** *n* stale paths — the file has moved
- **RECLASSIFY** *n* pictures — the folder disagrees with the admin
- **ADD** *n* files not in the admin — reported, but **never applied** (they are
  mostly DxO renders and junk)

Diff reports are written to a timestamped folder under `admin/status/`, kept so
runs can be compared over time.

It then offers a slower second pass: the full **suggested** `data_files.csv` and
`data_pictures.csv` for a row-by-row comparison in Excel, plus a per-picture
`display_status.csv` — the black-image scan, which tells you how many pictures
currently show nothing.

**The master is never touched by Review.**

### Lists vs. Files on Disk — Apply…

Applies what Review described: removes dead rows, repoints stale paths,
reclassifies pictures to their folder's species, and drops phantom pictures whose
files are all gone.

Both CSVs are backed up first to `admin/status/backup/`. You get the counts and
an explicit confirmation before anything is written.

When it dropped or re-filed pictures, it then recounts the species counters by
itself (species.csv is backed up first) and says which species changed — so they
do not show up as the next warning. The master's read-copy rebuilds itself.

> More detail, including when the admin drifts in the first place, is in
> [the Refresh how-to](howto_refresh_master_database.md).

### Species Counters — Recount…

Makes the species list's picture counts say what the pictures say. The counts
are added up at each merge and not recounted, so deletes and moves can leave
them too high. This recounts every species from the pictures actually there —
pictures, pictures with a JPG — redoes a species' best picture only when that
picture is gone, and adds up the groups. It shows what would change and asks
first; the species list is backed up before it is written.

The **Full report…** beside it lists ghosts (species whose pictures have
vanished, with their last-known file names), count drift and best pictures that
no longer exist. It never changes anything.

### GPS in the Files, Not in the List — Fill…

Copies a coordinate that a picture's **camera files** carry up into the
**picture row** itself.

This matters because everything that displays a location — the species
distribution map, an album's No-GPS counts — reads the picture row, not the file.
The merge does not always propagate the camera's fix upward, so a picture can
read as having no GPS while its RAW holds a perfectly good one.

**Additive only.** It never changes or removes an existing coordinate. If every
picture already carries its own, it says so and does nothing.

### Hand-Placed GPS Not in the List — Copy…

A picture you placed by hand on the map has its coordinate, its place name and
a *placed by hand* flag in its sidecar. Older versions of SpeciesViewer copied
only the coordinate into the master's list, so such a picture showed raw
coordinates on its card, and the map would not let you move it. This copies the
name and the flag from the sidecar into the list, for the whole master. It
invents nothing, and a position the camera recorded is left alone. You only
need it for pictures placed before July 2026; new placements are copied in full.

The check reads the sidecars of the pictures that could need it — every picture
with a position but no place name at all (no place, town, region or country),
and every hand-placed one without the flag. It takes a minute or two on a large
master.
A position the camera recorded never gets the flag, so those are not counted
(the old Albums item counted them: "8,599 missing the flag"). *Copy…* uses
that read (if it is less than 15 minutes old), says how many names and flags
**will** change, and asks first.

### Orphan Sidecars — Move to trash…

Finds `.xmp` / `.dop` sidecars whose image is gone and moves them to a
recoverable trash folder. It shows the count and extension breakdown, and asks
before moving anything.

Their rows are still in the master's list, so straight after the move it opens
**Lists vs. Files on Disk — Apply**, which shows what it would remove and asks
first. After any fix, Check the Master checks every line again.

### Lists vs. Read-Copy — Details…

SpeciesViewer keeps a fast read-copy of the master's lists on this computer. This
check compares the two — row counts, pictures naming a JPG that has no file row,
orphan file rows, and pictures that would publish as a broken image — and
*Details…* lists what it found. The read-copy rebuilds itself when the lists
change; *File ▸ Settings ▸ Manage Caches ▸ Master read-copy ▸ Rebuild* forces it.

### Restore the Master's Lists from a Backup…

Once a day, at start, the master takes a compressed copy of its three lists —
pictures, files, species — and keeps the last 14. This puts one back: pick the
day, confirm, and the read-copy is rebuilt. The lists you replace are kept first
(`backup/pre-restore-*`), so a restore can be undone. Anything added to the
master since that day is lost, and the window says so before it starts. Restart
SpeciesViewer afterwards — it keeps the master in memory.
