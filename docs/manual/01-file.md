# 1. File

The application's own chrome: how it is configured, what it caches, and how to
leave. Nothing here touches your pictures except the cache actions, and those
only touch caches — which are always rebuildable.

| Item | Shortcut | Needs |
|---|---|---|
| Settings ▸ … | | (see below) |
| Developer Tools ▸ … | | *Only visible when debug is enabled* |
| Quit | `Ctrl+Q` | |

> GH: kind of strange we follow the File menu entries, but nothing can be opened or saved.
> We might use multi-master support that we plan as ch 20 to make SV installable (rather than grown), and at least have open Select and Create master,

---

## Settings

Nine dialogs and one submenu. They are separate windows rather than one big
preferences panel because they are edited at very different moments: repository
paths once per machine, decode quality when you are unhappy with a preview.

> Which settings live in which file — and which ones travel with a trip versus
> stay on this machine — is documented separately in
> [configuration_inventory.md](../configuration_inventory.md).

### Global Settings — `Ctrl+G`

The machine-wide basics, and the home of the **Enable debug functionality**
checkbox that reveals *File ▸ Developer Tools*. That flag is read when the menu
is built, so **toggling it takes effect after a restart**.

### Look & Feel…

Fonts, colours and layout density for the GUI. .

> GE: "Cosmetic only" to "By configuring the viewer can show the data that helps the most with culling"

### Repository Settings

Where the repositories are: the master, the trips root, the albums root, and the
local pictures root. This is the single most consequential settings dialog —
most "nothing is enabled" states trace back to a path here being empty.

> GH: This needs a chart or diagram.  Probably done after ch 20 installation is done.

### Species Settings

How species names are handled: which languages are shown and in what order, and
how names are resolved. *(inferred — verify against the dialog.)*

> GH: the dialog has 3 settings:
> The first defines what the scope is of a species change.  When we have a misspelled name, this can by changed for just that occurrence, all that day or the entire trip.  The latter is probably never tested.  I suggest to remove the functionality, the viewers make it hard to enter wrong species name.
> The second allows to accept just one language.  Again, the search is so fast that all languages can be supported.  It also sometimes allow for short names, eg "koereiger" instead of "western cattle egret" 
> the third option is needed, but Look & Feel may be an OK place? 
### Database Settings

The master's SQLite read-cache and related database behaviour. The cache is a
derived artefact; it can always be rebuilt.

### Decode Quality…

How RAW files are turned into the pictures you look at — the render scale and
brightness handling used by the viewers. Raise it if previews look soft, lower it
if browsing feels slow.

> Whether a given camera body decodes at all is measured, not assumed — see
> [Appendix D](appendix-d-camera_support.md).

### Location Settings…

Geocoding and place-name behaviour: how a coordinate becomes "Lesvos, Greece".

### Interfaces to Lightroom and PhotoLab

Three settings that are really one subject — how pictures are handed to DxO
PhotoLab and Lightroom, and what those two write back. They were scattered
across the menu before being grouped here.

| Item | Sets |
|---|---|
| **PhotoLab Location…** | Where DxO PhotoLab is installed |
| **PhotoLab Denoise…** | Which denoise method is stamped into the `.dop` on a crop |
| **Sidecar Behaviour…** | What is written to the `.dop` and `.xmp`, and how PhotoLab is launched |

> **A rule worth knowing:** SpeciesViewer writes an `.xmp` freely, but only ever
> writes a `.dop` when PhotoLab itself authored one first. It never invents a
> `.dop` — doing so would hand PhotoLab a development recipe you never asked for.

### Manage caches…

Shows the per-machine caches — the EXIF cache, the preview cache — with their
sizes, and lets you clear them. Caches live under `%LOCALAPPDATA%\SpeciesViewer\`
and are deliberately *not* in Dropbox.

Clearing a cache is never destructive to data. It only costs time on the next
read.

### Rescan master EXIF cache

*Needs: a configured master.*

Walks every picture under `<master>\biota` and reads its EXIF into the master's
own cache file (`exif_cache_master.json`). This is kept separate from the
day/trip EXIF cache precisely so that the large master cache never slows down a
routine Read & Summarize.

Run it after a large external change to the master's files. It is slow — it is a
full pass over the collection.

> GH: Rescan master EXIF cache is somewhat similar to the "Maintain Master Repository" in
> master and "Rebuild all Albums and the master website". There are no duplicates, but we >  /> need think about how to present / organize.  Not all functions should be enabled for a r/o 
> master (smallmaster).  Clearly a Backlog item.

---

## Developer Tools

> GH: review r/o master vs real master.

**Only present when "Enable debug functionality" is on** in Global Settings, and
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

This is the answer to "is it safe to delete this trip?" across a two-machine
setup.

> gh: That question is now mostly automated, although the complexity of r/o laptop and a master is not. This is really debug for the developers.

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

> GH: the smallmaster has still many XMPs without images. Another example only the r/w master?

### Call Testbed for trip day

A developer hook that runs the testbed batch against a chosen trip day. Not
useful in normal operation.

> GH: we should remove this from the code.  You do these things with standalone tests.

---

## Quit — `Ctrl+Q`

Closes the application. Window geometry and the active selections are persisted,
so you come back to where you were.
