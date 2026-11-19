# 1. File

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
| Developer Tools ▸ … | | *Only visible when debug is enabled* |
| Quit | `Ctrl+Q` | |

---

## Settings… — `Ctrl+G`

One window, one tab per subject. A tab is built the first time you open it, and
**OK on any tab closes the window**.

> Which settings live in which file — and which ones travel with a trip versus
> stay on this machine — is documented separately in
> [configuration_inventory.md](../configuration_inventory.md).

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
  (species pages, blogs). The website is optional: everything else works without
  one. Choose where it goes:
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
> [Appendix D](appendix-d-camera_support.md).

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

### Species settings — removed

The old Species tab is gone. Its name display is on the Global tab; the other
two settings (how far a name correction reaches, which languages the name box
accepts) were never changed from their defaults and are no longer offered.

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

---

## Developer Tools

**Only present when "Enable debug functionality" is on** (Settings ▸ Global), and
only after a restart. These are diagnostics: they report, they do not repair.
The repair tools live in [Master ▸ Maintain Master Database](06-master.md).

### Trip Status Console…

*Needs: a configured master.*

Scans **this machine's** trips and albums, scores each against the master
(which pictures are already integrated, which would be lost if the trip were
deleted now), writes a per-machine CSV shard, renders a private HTML console and
opens it in your browser.

It asks two questions first:

- *Generate picture thumbnails for the drill-down?* — slower, because it extracts
  RAW previews, but makes the console browsable.
- *Upload this machine's status shard to the web?* — so the other machine can
  merge it in later.

The "is it safe to delete this trip?" question is now mostly answered
automatically during retire; this console is the manual fallback, mainly for the
two-machine (laptop + master) case and for development.

### Merged Status (all machines)…

*Needs: a configured master.*

Pulls every machine's uploaded shard down from the web and renders the
integrated overview — one row per trip, one column per machine. No local scan, so
it is fast. Useful when a trip was worked on the laptop and you are sitting at
the desktop.

### Audit Species Counters (dry-run)…

*Needs: a configured master.*

Compares the species counts in `species.csv` against the pictures actually
present, and lists ghosts (species whose pictures have vanished, with recovered
last-known filenames), count drift, and orphaned best-picks.

**Report only — it never modifies anything.** The report is also written to
`<master>\admin\status\species_counter_audit.txt`.

### Clean Orphan Sidecars (move to trash)…

*Needs: a configured master.*

Finds `.xmp` / `.dop` sidecars whose image is gone and moves them to a
recoverable trash folder. It shows the count and extension breakdown, and asks
before moving anything.

Their now-dangling rows in the master admin are cleared by the next
**Refresh Master Database — Apply**, so the natural order is: clean orphans,
then refresh-apply.

### Call Testbed for trip day

A developer hook that runs the testbed batch against a chosen trip day. Not
useful in normal operation.

---

## Quit — `Ctrl+Q`

Closes the application. Window geometry and the active selections are persisted,
so you come back to where you were.
