# 10. The viewers

Five picture surfaces, reached from several menus. The menu chapters say *how to
open* them; this chapter says *what they can do* — and in particular what each
one's filters mean, because the filters are how a day of 800 frames becomes a
decision.

The keys are documented separately and are not repeated here:
[viewer_keybindings.md](../viewer_keybindings.md).

## Telling them apart

| Viewer | Opened from | Shows | Edits? |
|---|---|---|---|
| **Biota Browser** | [Browser](02-browser.md) menu | A taxonomy tree, or one species' page | **No** — read-only |
| **Species Viewer** | Trips / Master / Albums | One species at a time, across a scope | Yes |
| **Culling grid** | Day review, the Days / Trip / Album panels | One day's pictures, all species | Yes |
| **Editor** | Double-click any card | One picture, big | Yes |
| **Map** | [Browser](02-browser.md) menu, or **🗺 Map** in a grid or panel | Where the pictures were taken, and your route | Only to place pictures without GPS |

They share a card, a selection model and a key set, so what you learn in one
transfers. The differences are in the filters, and those follow from the job:
the culling grid filters *within a day across species*; the Species Viewer
filters *within a species across dates*.

---

## What every card tells you

Cards are the common currency of all four viewers. A card carries the picture, a
caption, and a row of small signals.

| Signal | Means |
|---|---|
| ★★★☆☆ | Star rating (0–5). Click to set. |
| Colour edge / swatch | Colour label: **Red · Yellow · Green · Blue · Purple** |
| ✅ | Species is set and the picture is fine |
| ⚠️ | Species information is wrong or uncertain — hover for the message |
| ❔ | **No species set** — the pipeline will skip this picture |
| 📍 / ❓ | GPS present / missing |
| 📝 | Has a headline or description. Where editing is allowed it is a link to the editor; on a read-only card it is just an indicator. |
| ⌗ Crop | The picture carries a crop, from SpeciesViewer, PhotoLab or Lightroom |
| 🔒 | On a committed (frozen) day — read-only |
| + JPG | A fresh DxO/Lightroom render is on disk that the master does not track yet. Click to register it. |

Which detail sits in each **corner** of a card — capture time, GPS, place — is
yours to choose per viewer: *File ▸ Settings ▸ Look & Feel ▸ Card corners*.

### The Attention model

In the culling grid, a card that **needs a decision** gets a red border and a
badge in the top-right corner. A card needs a decision when it has no species,
has a species error, or has no GPS. The badge names the first unresolved issue,
in that order:

| Badge | Means | Click it |
|---|---|---|
| **❔ species** | Nothing identified yet | Opens the species dialog |
| **⚠ ID** | The identification is questionable | Opens the species dialog |
| **❓ GPS** | Identified, but no coordinate | Jumps to the GPS fix |

Frozen cards are never flagged — there is nothing you could do about them.

This is the fastest read in the application: scan for red borders, and you have
your work list for the day.

---

## The Biota Browser

**Read-only.** The desktop twin of the website's biota tree, over a Trip, an
Album or the Master. Walks Kingdom → Phylum → … → Species as grids of
olive-framed cards; the frame is the site's own olive, so it reads as the same
collection.

It has **two modes in one window**:

- **Tree** — cards for the child taxa at this level. Click one to drill in.
- **Species page** — the clicked species' complete picture set, inline: hero
  photo, grouped shots, siblings.

### Controls

| Control | Does |
|---|---|
| **←** | Back to the previous view (the browser keeps a history) |
| **All species** | Switch to one flat, searchable list of every species — abandons the tree |
| **Rank:** | Restrict to one taxonomic rank, or *Any rank* |
| **Search names at this rank…** | Substring search, honouring the rank filter |
| **Names:** | Which name shows under each card — the name-language profile (see below) |
| **Columns:** | Cards per row, 2 to 8 |
| **Rank rail** (left) | Jump straight to any ancestor rank |
| **Breadcrumb** (above the grid) | Where you are: `biota › … › species` |
| **✦ Species page** | From a species' pictures, back to its page |

The **Rank + Search** pair together mirror the website's "search at a selected
taxonomic level": pick *Family* and type `Ardeidae`, or leave rank on *Any* and
type a name.

`Ctrl+Home` / `Ctrl+End` scroll to the top / bottom. The browser's grid has no
focused card — it is navigated by clicking — so those keys scroll rather than
move a cursor.

> **Why read-only matters.** The browser is the surface you can hand to a
> visitor, or open while you are thinking, without any risk of changing data. To
> edit what you are looking at, use the Species Viewer — the **✦ Species…** button
> in that viewer is the bridge in the other direction.

---

## The Species Viewers

One dialog, three scopes: **Trip Species Viewer**, the master's **Species
Viewer**, and the **Album Species Viewer**. Same machinery, different source and
different write rules. It shows **one species at a time**, as cards sorted best
first, and writes rating and colour changes straight to the XMP/JPG sidecars and
the CSVs.

This is where identifications actually get corrected.

### Choosing what you are looking at

| Control | Does |
|---|---|
| **Scope: Day / Trip / Trip+Master** | Which pictures of this species to show |
| **Day picker** | Which day — visible only in Day scope |
| **Species** | Which species: **type any part** of a Dutch, English or scientific name (3 letters or more) — in the Trip, Album and Master viewer alike. |
| **Names** | The name-language profile — which of NL / EN / Scientific reads out under each picture, as a fallback chain |

The third scope button changes meaning with the viewer:

- **Trip+Master** (trip viewer) — shows the species' existing *master* pictures
  alongside the trip's, greyed and read-only. The question it answers: *is this
  new shot better than what I already have?*
- **Master** (album viewer) — the species' **complete** master collection, every
  date, with the album's own pictures tagged.

> With ~2000 species in the master, the species box is editable and filters as
> you type, matching anywhere in the name — not just the start.

### Filters and order

| Control | Does | Combines? |
|---|---|---|
| **Camera** | Only pictures from one camera body | Yes |
| **Sort** | `Rate ↓` (best first, the default) · `Rate ↓ (re-sort)` · `Time ↑` · `Time ↓` · `Camera` | — |
| **JPG only** | Only pictures that have a JPG file. **On by default.** | Yes |
| **Colour dots** | Only these colour labels — multi-select; **Grey** means *no label* | Yes |
| **Columns** | Cards per row, 1 to 8 | — |

All the filters combine. The colour filter is the one to reach for before a
cull: turn on Blue only, and you are looking at exactly what **✖ Blue** would
delete.

The **sort** choices are not cosmetic. `Time ↑` puts near-identical burst frames
next to each other, which is what makes duplicate-culling quick. `Camera` groups
by body — useful on a trip shot with two.

The order **stays put while you cull**: give a card a star or a colour and it does
not jump away. Only `Rate ↓ (re-sort)` reorders after every edit. Pick an order
again to re-sort. The choice is remembered per viewer.

### Acting on what you see

| Button | Does |
|---|---|
| **Map…** | Plot the *shown* pictures of this species on a map. In the **Master** Species Viewer, when pictures in view have no GPS (and the master may be changed on this computer), you can **place** them: a tray holds the pictures without GPS — drag one onto the map, or right-click it → *Find a place by name…* (it starts with the place already written on the picture). Right-click → *Select this outing* (or **o**) selects every picture shot in the same run, so one placement covers them all (see [Placing pictures without GPS on the map](#placing-pictures-without-gps-on-the-map)). When the species also has GPS points, Map… first asks which of the two maps you want. A place you set can be dragged again later; a camera's own coordinate never moves. |
| **✦ Species…** | Open this species' page in the Biota Browser (hero, grouped photos, siblings) |
| **↻** | *(master-backed)* Re-read this species from the master and repaint |
| **↻ Web** | *(master-backed)* Rebuild this species' website page and upload it |
| **✖ Blue** | Delete all Blue-tagged pictures (to trash) |
| **✖ Purple** | Delete all Purple-tagged pictures (to trash) |

> **The ↻ button exists because of a real trap.** A fresh DxO JPG sitting in the
> species folder is invisible to the master until something scans for it — which
> is why regenerating a page can come back looking unchanged. **↻** does that
> scan, and ingests anything new it finds.

Select one or more cards and **right-click** one of them; the menu acts on the
whole selection. To build a selection without holding a key, right-click a card
▸ **Add to selection** (and *Remove from selection*). The usual **Ctrl+click**
and **Shift+click** work too; a plain click never clears the selection. Two of the menu's entries
name the pictures:

| Menu item | Does |
|---|---|
| **Set species…** | Identify the whole selection at once |
| **Move to existing species…** | Reassign the selection to a *different existing* species — the two swans mislabelled among the ducks. Constrained to the trip's species list: no new species, no GBIF lookup. |

In a trip, the same menu also has the place and time entries of the culling
grid (see [Fixing where and when a picture was taken](#fixing-where-and-when-a-picture-was-taken)):
**Copy / Paste GPS + place**, **Remove GPS + place…**, **Copy date/time** and
**Set date/time…**. Only **Move to day…** is not here: when a new date falls on
another day of the trip, the Species Viewer says so, and you move the picture
from that day's culling grid.

**Esc** clears the selection.

In the **Master** Species Viewer, **Uncertain only** narrows the species list to
the ones with an open identification (see
[Naming a picture](#naming-a-picture)), grouped by family, and switches *JPG
only* off.

### When things are locked

Two independent locks hide the editing actions, each with a banner saying so:

- **A committed (frozen) day** — 🔒 the day is published; reset the trip to unlock.
- **A read-only master** — in an album, on a machine not permitted to write the
  master.

In Trip scope with only *some* days frozen, the actions stay available: they
operate on the editable pictures and skip the frozen ones.

> **In an album, a cull deletes MASTER pictures.** They go to the master's
> `.trash` and are undoable — but this is the real collection, not a working
> copy. The album viewer therefore starts with the delete buttons hidden until a
> load has confirmed this machine may write.

**Pictures with a JPG.** Blue/Purple delete leaves master pictures that have a
JPG alone, unless you tick **Also delete the … pictures that have a JPG** in the
confirm window (off by default). Use it for a species with many JPGs of the same
pose. What happens:

- Published blogs — trip and album — keep their own copies and do not change.
  An album blog you *rebuild* later reads the master, so a deleted picture is no
  longer in it.
- The website page of each species involved is rebuilt and uploaded straight
  away. Without a connection you get a warning naming those species; open each
  later and press **↻ Web**.
- Nothing is removed from the website itself: the old picture files stay there.
- If a species would have no photo left on its page, the confirm window names it.
- **Master ▸ Master Trash…** puts a picture back with its counts and its
  place on the species page, and offers to update those pages.

---

## Naming a picture

The species dialog opens from a card's species badge, from **Set species…** in
the right-click menu, and from the Editor. It is the same everywhere.

**Type any name.** Dutch, English, scientific, or a local name you taught it —
the suggestions match anywhere in the name. **look in trip names** narrows the
suggestions to species already on this trip (off by default; remembered). The
recently used species sit as buttons above, for one-click naming.

**A name it does not know** gets a question with these choices:

| Choice | Does |
|---|---|
| **Yes** | Keep the name as typed; it is looked up (GBIF) at the next Read & Summarize |
| **No** | Back to the box, to correct it |
| **Local name…** | The name a guide used. Pick the species it means: the picture gets that species, and the name is remembered for it — in the master and the trip, and on every computer the trip travels to |

**When you are not sure.** Two ways to say so; neither counts as a species in any
total, on any page or in any species count, until you settle it:

- **A group name** — type the genus with *sp.*: "*Oplurus* sp.". It is filed with
  its family.
- **Certainty** — in *Sex / age / plumage / certainty*, choose **probable**
  (shown as "cf.") or **possible** (shown as "?"). Leave it empty for a certain
  identification.

To find the open ones again: the purple **?** filter in the culling grid, or
**Uncertain only** in the Master Species Viewer.

**A landscape** is not a species: press **Landscape / context photo** in the
species dialog or the Editor. The card then shows a green *landscape* badge until
it has a headline — a landscape goes into the blog only with a JPG *and* a
headline.

**Faster naming in a grid:**

- `a` gives the selected pictures the species they already agree on.
- Copy a species name from one picture and paste it onto others, also across
  days.

---

## The culling grid

The per-day contact sheet — one card per picture — opened by **Review & rate
pictures**, or from a day's *Pictures* cell in the Days, Trip or Album panel.
This is where a card of 800 frames becomes 40 keepers with names on them.

Unlike the Species Viewer, it shows **one day across all species**.

### The view filters

These are **mutually exclusive** — exactly one is active:

| Button | Shows |
|---|---|
| **⚠️** | Only pictures with a wrong / unresolved species name. **Right-click it** to switch the button to **No species yet** — the pictures with nothing tagged at all. Left-click always returns to the wrong-name set. |
| **📍** | Only pictures **without** GPS coordinates |
| **All** | Everything |
| **Species** | Only pictures that **have** a species — i.e. what an add-to-master commit would take |
| **?** (purple) | Only pictures whose identification is still open: a group name ("sp."), *probable* or *possible* |

> **A greyed filter button is information.** 📍 greys out when no picture is
> missing GPS. ⚠️ stays available even on a warning-free day, because its
> right-click **No species yet** is still useful there.

### The filters that combine

These stack on top of whichever view filter is active, and on each other:

| Control | Shows |
|---|---|
| **Find species…** | Only pictures whose species name contains this text |
| **Find file…** | Only pictures whose file name contains this text (e.g. `309`) — works even when the species is unrecognised, so it finds a problem picture by number |
| **JPG** | Only pictures that have a JPG |
| **Colour dots** | Only these colour labels — multi-select; **Grey** means *no label* |

### The rest of the bar

| Control | Does |
|---|---|
| **🔄 Refresh** | Re-read colours, ratings and metadata changed by another tool (PhotoLab, Lightroom). Also `F5`, and automatic when the window regains focus. |
| **🗺 Map** | Plot the pictures now shown, all species, with the travel path |
| **✖B** / **✖P** | Delete all Blue / Purple pictures (to trash) |
| **Per row:** | Cards per row, 1 to 10 |
| **Cancel** | Stop the scan in progress |

Select cards and **right-click** one to act on the whole selection;
**Esc** clears it. Right-click ▸ **Add to selection** picks pictures one by one
without holding a key; **Ctrl+click** and **Shift+click** work as in Windows.
In the same menu, **Use as album/trip banner…** cuts the
wide banner for the album's or trip's web pages from that picture (see
[Albums](07-albums.md#select-or-update-album)).

`z` opens **Quick View** — the camera's own JPEG, big and zoomable, with the
culling keys (`1`–`5` colours, `q`/`w`/`e`/`r`/`t` stars) still working.

> **🔄 Refresh is the answer to "I changed it in PhotoLab and nothing happened."**
> The colour and rating live in the sidecar; SpeciesViewer re-reads on focus, but
> if you have both windows visible at once, press it.

### Fixing where and when a picture was taken

The right-click menu of a trip day has three pairs of entries for pictures with
a missing or wrong place or time (all but **Move to day…** are in the trip's
Species Viewer too). Each acts on the selection, or else on the
picture you clicked.

| Menu item | Does |
|---|---|
| **Copy GPS + place** / **Paste GPS + place** | Give a picture without GPS the position and place name of a neighbour (also `Ctrl+Shift+C` / `Ctrl+Shift+V`). |
| **Remove GPS + place…** | Undo a wrong paste or a wrong drop on the map: removes the position *and* the place name. Only a position placed by hand (pasted, dropped on the map, looked up from a place name) can be removed; GPS the camera recorded never is, so the item is greyed out for those pictures. Asks first, naming the place that goes. |
| **Copy date/time** / **Set date/time…** | Give a picture **without a capture date** one. WhatsApp downloads and pictures received from others usually have none, and such a picture is left out of the blog, out of every Album and out of the GPS fill-in from neighbouring pictures. Copy the time from a neighbour, then adjust the date, time and seconds separately. Several pictures get one second apart each, never a second another picture already has. |
| **Move to day…** | Move pictures that sit in the wrong day folder to another day of this trip. |

**A date you set is an estimate, and stays one.** It is marked as set by hand,
so **Set date/time…** stays available for that picture: when you later remember
better, set it again. A date the camera recorded can never be changed.

**When the new date is another day of the trip**, SpeciesViewer offers to move
the picture to that day's folder (the same move as **Move to day…**). The
picture's files travel together, including its `.xmp` and `.dop`. Nothing is
overwritten: if a file with the same name is already there, that picture stays
where it is. A published day refuses, in both directions. Afterwards this day
re-reads when you close the viewer, and the other day's **Read & Summarize**
shows as needed in the trip overview.

### In what order?

Any order. Some do one thorough pass at home; others pick the five best of the
day for a first blog on the road and come back to the rest later. The filters
work the same either way.

---

## The Editor

Double-click any card. One picture, as big as the window, with a filmstrip.

Its three tabs, the full-resolution view and the crop tool are documented in
detail in [viewer_keybindings.md](../viewer_keybindings.md); in summary:

| Feature | Notes |
|---|---|
| **Fit** tab | The embedded preview, fitted to the window |
| **Camera JPEG** tab | The JPEG the camera itself wrote inside the RAW — on the Nikons full size, and it appears instantly. `f` toggles Fit ⇄ this tab, and a double-click on the Fit image lands here. |
| **100%** tab | Decoded from the RAW at full sensor resolution — the embedded preview is only ~3200×2400. Lazily decoded, then cached. Click the tab to open it; its label shows the configured render scale. |
| **✂ Crop** | Rough-cut a crop to hand to PhotoLab / Lightroom as a starting point. Aspect locked to the camera's nominal ratio. Also on master pictures, when the master may be changed on this computer. |
| **🖼 Make JPG** | A JPG beside the RAW from the camera's preview + the crop. On a **master** picture it is added to the master straight away and becomes the picture's website photo (change that under *Use this JPG on the website*); then you are asked to update the species page. It never replaces a file: when `<name>.jpg` exists, the new one is `<name>-JPG-2.jpg`. |
| **↻ Rescan** | Master pictures only. After an export from PhotoLab: adds the new JPG (and its `.dop`) to the master, shows it, and offers to update the species page. |
| Navigation | `←` / `→` / `Space` / mouse wheel |
| **Right-click** | On the image, the panel or a filmstrip picture: **the same menu as the grid you opened the Editor from**, acting on the selection (or on that one picture). See below. |

Two behaviours worth knowing because they are deliberate and initially
surprising:

- **The 1:1 point stays put as you move through a group.** A set of similar
  shots therefore compares at the same crop, and the sharpest one stands out.
- **While the 100% tab is active, navigation steps over Blue and Purple
  pictures.** You flip only through the live candidates. The Fit tab and the
  grids keep normal navigation, so a reject is still reachable to re-judge.

### Right-click: the grid's own menu

The Editor has no menu of its own. Right-click anywhere in it and you get the
**exact menu of the grid it was opened from** — the culling grid's, or the
Trip / Album Species Viewer's — with the same entries, the same rules about
what is locked, and the same target: the selection when you right-click inside
it, else that one picture (on the image: the picture on screen).

So a new action only has to be added to a grid to appear in the Editor too, and
the two can never disagree about what you may do to a picture.

What the action changes shows up in both windows: a new rating, species or
exposure repaints the Editor; a deleted picture — or one moved to another
species — leaves its filmstrip and the Editor lands on a neighbour. Selection
is shared: clearing it in either window clears it in both.

> **Sex / age / plumage** for the picture on screen stays where it was — the
> button in the panel's Species section. The menu only carries it where the
> grid's menu does (an album).

Opened from the Biota Browser or a map, which have no card menu, the Editor
keeps short menus of its own (location, headline / description, species,
rating, exposure, delete where allowed).

### Working with PhotoLab open

SpeciesViewer and PhotoLab can both be open while you cull. They share the
pictures' sidecar files, and each is careful not to overwrite the other's work.

| What you change in SpeciesViewer | When PhotoLab sees it |
|---|---|
| **Colour, rating** | The next time PhotoLab opens that folder: at startup, when you click the folder, or when you press **Open in PhotoLab**. |
| **Crop, exposure** | When you press **Open in PhotoLab** in the Editor. If PhotoLab is closed, they go straight to its sidecar. |

**Open in PhotoLab** first brings PhotoLab to the folder, so it reads your
colours and ratings, and a few seconds later hands over the crops and exposure.
You see SpeciesViewer refresh, and a moment later PhotoLab. The pause is
`[Global] photolab_handover_delay_s` (default 5 s).

PhotoLab makes a picture's recipe (its `.dop`) only when it processes that
thumbnail, so on a freshly read day a cropped picture may have none yet. Such a
crop waits: SpeciesViewer keeps an eye on it for 90 seconds and hands it over
the moment PhotoLab has made the recipe. The log sums it up per picture, with
the species:

```
PhotoLab handover 2026-09-07: 1 handed over, 1 waiting for PhotoLab
   ✓ 2026 Brazilie - N9074161  orange-backed troupial — crop
   … 2026 Brazilie - N9075264  crane hawk — crop: PhotoLab has no recipe for it yet
   ✓ 2026 Brazilie - N9075264  crane hawk — crop (PhotoLab made its recipe after 14 s)
```

A picture still waiting after 90 seconds is named with ✗: in PhotoLab, select it
and *Write sidecars*, then press **Open in PhotoLab** again.

> **Best: let PhotoLab make all recipes at once.** After reading a day, open it
> in PhotoLab, select all (Ctrl-A) and *Write sidecars*. Every picture then has
> its recipe and no crop ever has to wait. SpeciesViewer cannot do this step: a
> recipe must come from PhotoLab itself.

> **Why crops wait.** Handing a crop to a running PhotoLab wakes it, and it then
> writes its own copy of the colours back. If it has not yet seen a colour you just
> set, that colour is replaced by its old one. Holding the crop until PhotoLab has
> read the folder avoids this.

**A colour that disappears outside SpeciesViewer is kept.** If a colour is
removed from a sidecar by another program, the trip viewer keeps it and notes it
in the log. A Day has no store of its own, so the colour does go, but the log
says which picture lost which colour. The price: a colour you remove on purpose in
PhotoLab or Lightroom does not reach a trip; remove it in SpeciesViewer instead.
`tools/svr_label_audit.py` lists any picture whose colour differs between the
trip and its sidecar.

*Background and the tests behind this: `docs/plans/photolab_sidecar_fight.md`.*

---

## The map

The map shows **where** the pictures were taken. Lightroom and PhotoLab show a
map too, but not the things that matter on a trip: the route you took, the
pictures that ended up without GPS, and a quick way to give them a position.
When every picture has GPS it is simply a viewer; when some have none, it is
where you place them.

### Opening it

| From | Shows |
|---|---|
| *Browser ▸ Trip Map…* · **🗺 Map whole trip** on the Trip panel | Every picture of the active trip, with the travel path |
| *Browser ▸ Album Map…* · **🗺 Map whole trip** on the Album panel | Every picture of the active album. Right-click a day on the Album panel ▸ **🗺 Map this day** for one day. |
| **🗺 Map** in the culling grid | The pictures the grid shows now, all species |
| **Map…** in a Species Viewer | The pictures of this species now shown |
| *Browser ▸ Species Map…* | One to four species of the master, each in its own colour |

The map is a window of its own. Keep the grid and the Editor open beside it:
on a map opened from a grid, a picture you place gets its 📍 in that grid
straight away.

### Reading it

- **Bubbles.** Pictures taken at the same spot share one bubble, with the number
  of pictures in it. Zoomed out, nearby spots merge; zoom in (mouse wheel) to
  split them. Double-click a bubble that covers several spots to zoom in on it.
- **Click a bubble** to see its pictures in the strip below the map; click one
  there to open it in the Editor.
- **Right-click a bubble ▸ Hide** takes its pictures off the map (a picture with
  a wrong position that is in the way). **Show all** brings them back. Hiding
  changes nothing in the files.
- **Travel path.** A line through the pictures in time order, coloured by day
  from light (first day) to dark (last day). Hover a leg for its date. On by
  default on a whole-trip map.
- **All / Species.** Every picture, or only those that carry a species (without
  the landscapes and context shots).
- **Basemap.** *Terrain* (relief; the Species Map starts with it) or *Standard*
  (OpenStreetMap; the trip and album maps start with it). **Google Maps** opens the same view in your browser, where
  place names can be read and copied. **Fit** zooms to all pictures.
- In a Trip Species Viewer the trip's own pictures are **pink**, the master's
  pictures of the same species teal.

### Moving pictures

Drag a bubble to a new spot: a pin appears there, and **Place here** (or
`Enter`) moves all pictures of that bubble; **Cancel** (or `Esc`) leaves them.
When the bubble covers several spots you are warned first, so zoom in if you
meant one.

On a trip map any bubble can be moved. On an Album map and in the master, only
a position **placed by hand** can be moved; GPS the camera recorded never is.
A position placed by hand can also be removed again: right-click the picture in
a grid ▸ **Remove GPS + place…**.

### Placing pictures without GPS on the map

A map opened from a trip, an Album or the Master Species Viewer shows a strip
of the pictures **without GPS** above the map, in the order they were taken.
What you can do depends on what the picture still knows:

| The picture has… | Then |
|---|---|
| **A capture time**, with pictures with GPS shortly before and after | **Suggest a position** (`p`) puts it between them. The pin's colour says how sure the guess is: green confident, amber approximate, red a rough guess. |
| **A place written on it** (typed in, or from an old trip) | **Find a place by name…** (`n`) starts with that text, so often one click finds it. Several pictures with the same text: **Select same place** (`s`), then place them all at once. |
| **Nothing** | Drag it onto the map yourself. **Zoom map to around this time** in its right-click menu shows where you were just before and after, if it has a capture time. |

The actions, in the right-click menu of a picture in the strip, or as a key:

| Action | Key | Does |
|---|---|---|
| **Suggest a position** | `p` | Guesses the spot from the pictures with GPS taken just before and after. |
| **Find a place by name…** | `n` | Looks a place up on OpenStreetMap. It starts with the place already written on the picture, if any. |
| **Select this outing** | `o` | Selects the whole outing the picture belongs to (see below). |
| **Select same place** | `s` | Selects every picture in the strip with the same place written on it (case and spacing don't matter). Offered when there are at least two. |
| Peek | `z` | Shows the pictures large, read-only, to remind you where they were taken. |
| Select a range | `[` … `]` | Click the first picture, press `[`, click the last, press `]`. |
| Select several | `Ctrl+click`, `Shift+click` | The usual Windows clicks. |

Each of these acts on all selected pictures. Nothing is written yet: a pin
appears that you can still drag to the exact spot. Then **Place here** (or
`Enter`) writes the position, and **Cancel** (or `Esc`) drops it.

**An outing** is a run of pictures taken in one go: each picture was taken at
most **30 minutes** after the one before it. A longer gap starts a new outing.
An afternoon on one beach is usually one outing; a drive to the next spot
usually starts a new one. A picture without a capture time is an outing of its
own. Placing a whole outing with one pin is right for old pictures, where the
spot only needs to be roughly correct. When the outing caught pictures from two
places (a short drive), click the extra ones out of the selection before you
place.

A position placed by hand can be dragged again later, or removed. GPS the
camera recorded never moves. In the Master Species Viewer the map only shows
the pictures of that species; to place several species at once, open the map
of an Album that covers those days.

In the grid, right-click ▸ **Get GPS from location** does the same lookup as
**Find a place by name…** for a whole selection at once, without the map: each
picture gets the coordinate of the place written on it (to about 30 m), marked
as placed by hand.

Before writing, `z` shows the selected strip pictures large, read-only, as a
reminder of where they were taken.

---

## The master card context menu

Right-click a card backed by the master — in the Species Viewer, the album day
viewer, or the Biota Browser's species page — for the master operations. This is
one shared implementation, so the same menu appears wherever it applies.

| Action | Does |
|---|---|
| **Move to another species…** | Reassign this picture to another species — all its files move, its JPGs too, and it keeps its sex / age / plumage words. The species may be one with no pictures in the master yet: it is added from your species list. **Master ▸ Undo Last Species Move** puts it back. |
| **Sex / age / plumage…** | Set the picture's qualifiers |
| **Add new files (scan folder)…** | Pick up renders that appeared beside the picture |
| **Choose the website picture** | Pin which file represents the species, or let the code choose (biggest wins) |
| **Delete picture (move to trash)** | |

Two guards you will meet:

- *"Has a JPG — remove it with blue delete"* — this menu does not delete a
  picture with a JPG; Blue delete with the JPG box ticked does.
- *"Master is read-only — enable in Repository Settings"* — this machine is not
  permitted to write the master.

---

## Name-language profiles

Both the Biota Browser and the Species Viewers have a **Names** selector. It sets
which name reads out under each picture, as a **fallback chain** rather than a
single choice — so a profile like *EN/Sci* shows the English name where there is
one and the scientific name otherwise, and never falls back to Dutch.

The selector is a **per-session override**. The lasting default is in
*File ▸ Settings ▸ Global*, under *Species names and text translation*.

> Known gap: the album preview still shows scientific names regardless of the
> profile.

---

> Not described yet: the **sex / age / plumage** qualifiers (backlog 1.20).
