# 1. File

## Start here: the Setup Checklist

To begin, you only need **Help ▸ Setup Checklist…**. It opens by itself
whenever something essential is missing, so also the first time you start
SpeciesViewer. Fill it in, and you can start working.

The checklist has one row for each thing SpeciesViewer needs, with a mark that
shows whether it is set. Each row has a button that opens the place to set it.
The words in it — master, trips, albums, website — are explained in
[Concepts](00-concepts.md).

- **Needed to start:** the master collection, where the website goes, and
  exiftool. *On this computer* is a fine choice for the website: nothing is
  uploaded, and you can change it later.
- **Needed for their own menus:** the folders for pictures, trips and albums.
  For an empty row, **Use suggested** makes the folder and uses it in one
  click.
- **Optional:** DxO PhotoLab, and the decode quality, which works on its
  defaults.

**Fill in every row anyway, in one sitting.** For the folders, *Use suggested*
is one click each, and a menu whose folder is missing stays grey — later you
would wonder why.

The checklist also answers *"why is this menu grey?"*, so it is worth opening
again later. Every row is described in
[Help ▸ Setup Checklist](09-help.md#setup-checklist).

---

## The main window

The window has three parts:

- **The mode strip** (left) — four pills: **Unfiled · Day · Trip · Album**. Left
  to right, this is the order pictures actually travel. The strip is the master
  control: whichever mode is selected, the rest of the window follows.
- **The left column** — the list for that mode: unfiled days, registered days,
  the trips you have, or your albums. Clicking an entry makes it the *active*
  one, and a great many menu items are enabled or greyed out purely on the
  strength of what is active here.
- **The panel** (right) — a table of the active thing's contents, one row per
  day: the Unfiled, [Days](03-days.md#the-days-panel),
  [Trip](04-trips.md#the-trip-panel) or [Album](07-albums.md#the-album-panel)
  panel. This is where most day-by-day work actually happens: double-click a cell
  to act on that day, right-click for more, or double-click a column header to
  run it for every day.

> **The rule:** the left column leads. The panel always shows the scope the mode
> strip is on.

Two habits that pay off immediately:

- **Right-click everything.** Rows in the panel and entries in the left column
  carry context menus with actions that have no menu-bar equivalent.
- **Watch the bubbles** in the pipeline columns — they tell you which steps a day
  has been through, and hovering explains why a step is not yet available.


### A note on "active"

Many menu items depend on there being an **active trip**, **active album**, or
**active day**. Active means *selected in the left column* (and stored in the
config, so it survives a restart). If a whole run of menu items looks dead, the
usual cause is that nothing is active in the scope they belong to.

The other common cause is an unmet prerequisite — asking for a pipeline step
before its predecessor has run, for instance trying to Publish a day that has
only been read. Hover the greyed item and the tooltip says what is missing. See
[Appendix A](appendix-a-greyed-out.md).

**The rest of this chapter can wait.** It covers the settings you change once
you know what you want: how pictures look, caches, place names, and the
interfaces with DxO PhotoLab and Lightroom.

---

## The File menu

The program itself: how it is set up, what it caches, and how to leave. Nothing
here touches your pictures, with one exception: the master's deleted pictures,
which the Database tab can restore or delete for good. The cache actions only
touch caches, which can always be rebuilt.

There is no *Open* or *Save*: SpeciesViewer works on collections it is pointed
at, not documents. Choosing, creating and switching collections lives under
[Master ▸ Masters](06-master.md#masters).

| Item | Shortcut | Needs |
|---|---|---|
| Settings ▸ Settings… | `Ctrl+G` | |
| Settings ▸ Manage caches… | | |
| Settings ▸ Rescan master EXIF cache | | a master |
| Developer Tools ▸ … | | *Only visible when debug is enabled* — [Appendix C](appendix-c-developer-tools.md) |
| Quit | `Ctrl+Q` | |

---

## Settings… — `Ctrl+G`

One window, one tab per subject. A tab is built the first time you open it, and
**OK on any tab closes the window**.

Where the settings are kept, and what travels with a trip, is at the end of
this section: [Where your settings and data are kept](#where-your-settings-and-data-are-kept).

| Tab | What you set there |
|---|---|
| **Global** | Splash screen, debug tools, species names under each picture, translation key, and where your website goes |
| **Look & Feel** | Label colours, card corners, the culling grid, Make JPG size |
| **Database** | How the master's fast read-copy is doing, and the master's deleted pictures |
| **Decode quality** | How a RAW is turned into the picture you look at |
| **Location** | How a GPS position becomes a place name |
| **Lightroom & PhotoLab** | Three sub-tabs: PhotoLab location, PhotoLab denoise, Sidecar behaviour |

### Global

- **Enable Splash** — the picture shown while SpeciesViewer starts.
- **Enable debug functionality** — reveals *File ▸ Developer Tools*. The menu is
  built at start-up, so **this takes effect after a restart**.
- **Species names and text translation**
  - *Shown under each picture* — which of Dutch, English and scientific names
    labels a picture, tried in order. "English / Scientific" never shows Dutch.
  - *Google Translate key* — optional. With a key, species names and blog texts
    are translated by Google's paid service. Without one, SpeciesViewer uses
    Google's free translation page, and MyMemory when that one refuses. Every
    translation is kept on this computer, so nothing is translated twice.
- **Website and publishing** — SpeciesViewer makes a website of your pictures
  (species pages, blogs). Choose where it goes:
  - **On this computer** — nothing is uploaded. SpeciesViewer shows the website in
    your browser at `http://127.0.0.1:8765/` while it runs. Each master has its
    own. It works without internet; only name lookups, place names and
    translation need a connection.
  - **On my web server** — fill in the website address, upload server, account,
    password and the website's folder on the server, as your web host gave them.
  - *Contact e-mail* — shown on the website.

### Look & Feel

Not merely cosmetic: cards that show the fields you actually judge on make
culling faster.

- **Picture label colours** — the five colours (Red, Yellow, Green, Blue, Purple)
  as they look in every viewer. Applies after a restart.
- **Theme** — light or dark. Applies after a restart.
- **Card corners — per viewer** — which detail sits in each corner of a picture
  card (capture time, GPS, place, …), chosen per viewer, plus the frame colour.
- **Culling grid** — pictures per row, how wide the grid is, and what a day
  opens showing.
- **Make JPG — resize** — a JPG longer than the first size is scaled down to the
  second (3200 → 2000 px by default). A crop is applied before the resize.

Card and grid settings apply the next time you open a viewer.

### Database

SpeciesViewer keeps a fast read-copy of the master's lists on this computer (a
SQLite database). It is rebuilt by itself whenever the master's lists change, so
you normally never look here. The tab shows:

- **Master SQLite cache** — how many rows, how big, when it was last built, and a
  *Rebuild* button.
- **Writing changes back to the master** — an edit goes into the read-copy first
  and is then written back to the master's lists. A count that stays above zero
  means that write-back is stuck — usually because the master's drive cannot be
  reached. *Flush now* forces it.
- **Trashed images** — master pictures you deleted that can still be restored.
  *Restore images…* brings them back; emptying the trash makes the delete final
  (also [Master ▸ Empty Master Trash…](06-master.md)).

### Decode quality

How RAW files become the pictures you look at: quick presets, the demosaic
method, noise reduction, brightness, render scale, and the size and JPEG quality
of the previews in grids. Raise it if previews look soft; lower it if browsing
feels slow.

> Whether a given camera body decodes at all is measured, not assumed — see
> [Appendix B](appendix-b-camera-support.md).

### Location

How a coordinate becomes "Lesvos, Greece", using OpenStreetMap: the language of
the place name, how detailed it is (village or region), and whether a lookup may
replace place text you already have.

### Lightroom & PhotoLab

How pictures are handed to DxO PhotoLab and Lightroom, and what those two write
back.

| Sub-tab | Sets |
|---|---|
| **PhotoLab location** | Where DxO PhotoLab is installed |
| **PhotoLab denoise** | Which denoise method is stamped into the `.dop` on a crop |
| **Sidecar behaviour** | What is written to the `.dop` and `.xmp`, and how PhotoLab is launched |

> **A rule worth knowing:** SpeciesViewer writes an `.xmp` freely, but only ever
> writes a `.dop` when PhotoLab itself authored one first. It never invents a
> `.dop` — doing so would hand PhotoLab a development recipe you never asked for.

### Where your settings and data are kept

You never need to open these files, but it helps to know what sits where — for
a backup, a second computer, or a new disk.

| What | Where | Copy it? |
|---|---|---|
| **Settings of this computer** — every folder, the website password, the translation key, window positions | `%LOCALAPPDATA%\SpeciesViewer\config\` (on the Mac: `~/Library/Application Support/SpeciesViewer/`) | **No.** Each computer has its own, because it holds passwords. |
| **Your collection** — the master's lists of pictures, files and species, its trash and its daily backups | the master folder | **Yes** — this is what you back up and share between your own computers. |
| **A trip** — its name, dates, blog texts and species | the trip's folder | Travels with the trip. |
| **Beside your pictures** — the `.xmp` and `.dop` files with rating, colour, species and crop; a day's blog draft | the day folder | Travels with the pictures. Never delete them: they are your work. |
| **Caches** — previews, EXIF, the fast read-copy of the master | `%LOCALAPPDATA%\SpeciesViewer\` | No — rebuilt by themselves. See [Manage caches](#manage-caches). |

Three rules follow from this table:

- **What lasts is the master and your pictures with their sidecars.** Back those
  up; everything else is either rebuilt or quickly re-entered.
- **The master protects itself.** Once a day, at start, it takes a compressed copy of
  its lists and keeps the last 14; *Settings ▸ Database ▸ Restore…* puts one back.
- **Caches never go in Dropbox.** They are large, change constantly, and are
  per computer.

> The technical inventory — every file, its scope and what losing it costs —
> is [configuration_inventory.md](../configuration_inventory.md).

## Manage caches…

Shows every cache this computer keeps, with its size, and lets you clear it or
move it to another folder (a faster or bigger disk). Caches live under
`%LOCALAPPDATA%\SpeciesViewer\` and are deliberately *not* in Dropbox.

- **Local caches** — full-resolution pictures for the 100% view, preview
  thumbnails for grids and the Editor, and the two EXIF caches (days and trips;
  the master scan). Previews that have not been looked at for 90 days, or whose
  picture is gone, are removed by themselves.
- **Upload caches** — one per website: what has already been uploaded, so a
  publish only sends what changed. Clearing one makes the next publish send
  everything again.

Clearing a cache never loses data. It only costs time on the next read.

## Rescan master EXIF cache

*Needs: a configured master.*

Walks every picture under `<master>\biota` and reads its EXIF into the master's
own cache file (`exif_cache_master.json`). This is kept separate from the
day/trip EXIF cache precisely so that the large master cache never slows down a
routine Read & Summarize.

Run it after a large external change to the master's files. It is slow — it is a
full pass over the collection.

> GH: is this related to "rebuild the master"?  Does the rebuild include the
> Rescan master EXIF cache
> As you already noted, recount, rebuild etc are all maintenance tasks that 
> are scattered in the menu.
>
> Claude: not related — and worse: nothing in the program reads the cache this
> item fills. Only *Manage caches* shows its size. It is a leftover; proposal:
> remove the menu item and the cache (backlog 1.8). Gathering the maintenance
> items (Recount, Refresh, Rebuild, Empty Trash, Undo) in one place is 1.9.

---

## Developer Tools

Only present when *Enable debug functionality* is on (Settings ▸ Global), and
only after a restart. These are diagnostics written to find faults in the
program; you will not need them. They are described in
[Appendix C](appendix-c-developer-tools.md).

---

## Quit — `Ctrl+Q`

Closes the application. Window geometry and the active selections are persisted,
so you come back to where you were.
