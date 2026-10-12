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
| Add Pictures to Master… | A master |
| Species Viewer | A master |
| Species Map | A master |
| Species Without a Website Picture… | A master |
| Maintain Master Database ▸ … | A master |

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

The shared species name dictionary: scientific name, English name, Dutch name.
The same one list as [Trips ▸ Edit Species List](04-trips.md) — see there for why
there is only one, and what happens to the active trip's copy when you save.

This list is application-global. It is not master-scoped data in any meaningful
sense; it simply lives in the master because the master is the thing that is
always there.

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
[Maintain Master Database ▸ Fill Picture GPS from Files…](#fill-picture-gps-from-files)
is for.

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

---

## Maintain Master Database

Master-data repair, grouped. Everything here except **Apply** is
non-destructive, and everything here is review-first: you see what would change
before anything changes.

| Item | Destructive? |
|---|---|
| Refresh Master Database — Review… | No |
| Refresh Master Database — Apply… | **Yes** — the only destructive action in this submenu |
| Add Missing Place Names… | Additive only |
| Fill Picture GPS from Files… | Additive only |
| Geotag from Tracklog (GPX/GPS log)… | Review-first |
| Rebuild Entire Website (all pages)… | HTML only — no data changed |
| Manage Site Publication… | Changes the site index only |

### Refresh Master Database — Review…

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

### Refresh Master Database — Apply…

Applies what Review described: removes dead rows, repoints stale paths,
reclassifies pictures to their folder's species, and drops phantom pictures whose
files are all gone.

Both CSVs are backed up first to `admin/status/backup/`. You get the counts and
an explicit confirmation before anything is written.

Afterwards, re-run **Count Species** to refresh the per-species totals; the
master's SQLite cache rebuilds itself.

> More detail, including when the admin drifts in the first place, is in
> [the Refresh how-to](howto_refresh_master_database.md).

### Add Missing Place Names…

Looks up the place for master pictures that have GPS but **no location, city or
country** — the ones that show as bare coordinates on the species map.

Review-first: nothing is written until you confirm the table. It only touches
pictures that have no place name at all.

> **It never overwrites a hand-entered location.** There is one truth for a
> picture's place, and a name you typed wins over a name a geocoder guessed.

### Fill Picture GPS from Files…

Copies a coordinate that a picture's **camera files** carry up into the
**picture row** itself.

This matters because everything that displays a location — the species
distribution map, an album's No-GPS counts — reads the picture row, not the file.
The merge does not always propagate the camera's fix upward, so a picture can
read as having no GPS while its RAW holds a perfectly good one.

**Additive only.** It never changes or removes an existing coordinate. If every
picture already carries its own, it says so and does nothing.

### Geotag from Tracklog (GPX/GPS log)…

The third GPS repair tool, and the only one that brings in **outside**
information. Where the other two fill a coordinate from something the master
already has, this matches pictures to an external **GPX or OM-1 GPS log** by
time.

It is the only way to cover:

- a day the camera's own GPS was never switched on, and
- **altitude**, which the camera never records at all.

Review-first, like its siblings.

### Rebuild Entire Website (all pages)…

Forces a full re-render and upload of **every** master species and taxon page,
not just those that changed — for when a shared template or the rendering code
changed and the fix has to reach all pages at once.

**HTML only.** It never freezes or commits master data; it is deliberately
separate from the data pipeline.

Its album-and-master counterpart is
[Utilities ▸ Rebuild all Albums and the Master website…](08-utilities.md).

### Manage Site Publication…

The site index seen whole: **everything currently listed** on the website's trip
index, whether or not you can still open the thing behind it.

Use it to remove a listing whose trip or album no longer exists locally — the
case the per-trip and per-album publication dialogs cannot help with, because
they act on something you must first be able to select.

The live index is always the base for any write, so listings published from
another machine are not silently dropped.
