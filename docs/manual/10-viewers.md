# 10. The viewers

Four picture surfaces, reached from several menus. The menu chapters say *how to
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
| **Culling grid** | Day review, yellow panel | One day's pictures, all species | Yes |
| **Editor** | Double-click any card | One picture, big | Yes |

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
| **Species** | Which species. In the master viewer this is **type-to-search**: any part of a Dutch, English or scientific name. |
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
| **Sort** | `Rate ↓` (best first, the default) · `Time ↑` · `Time ↓` · `Camera` | — |
| **JPG only** | Only pictures that have a JPG file. **On by default.** | Yes |
| **Colour dots** | Only these colour labels — multi-select; **Grey** means *no label* | Yes |
| **Columns** | Cards per row, 1 to 8 | — |

All the filters combine. The colour filter is the one to reach for before a
cull: turn on Blue only, and you are looking at exactly what **✖ Blue** would
delete.

The **sort** choices are not cosmetic. `Time ↑` puts near-identical burst frames
next to each other, which is what makes duplicate-culling quick. `Camera` groups
by body — useful on a trip shot with two.

### Acting on what you see

| Button | Does |
|---|---|
| **Map…** | Plot the *shown* pictures of this species on a map |
| **✦ Species…** | Open this species' page in the Biota Browser (hero, grouped photos, siblings) |
| **↻** | *(master-backed)* Re-read this species from the master and repaint |
| **↻ Web** | *(master-backed)* Rebuild this species' website page and upload it |
| **✖ Blue** | Delete all Blue-tagged pictures (to trash) |
| **✖ Purple** | Delete all Purple-tagged pictures (to trash) |

> **The ↻ button exists because of a real trap.** A fresh DxO JPG sitting in the
> species folder is invisible to the master until something scans for it — which
> is why regenerating a page can come back looking unchanged. **↻** does that
> scan, and ingests anything new it finds.

Select one or more cards and a **selection bar** appears:

| Button | Does |
|---|---|
| **Set species…** | Identify the whole selection at once |
| **Move to species…** | Reassign the selection to a *different existing* species — the two swans mislabelled among the ducks. Constrained to the trip's species list: no new species, no GBIF lookup. |
| **Clear selection** | |

> **"look in trip names"** in the Set species dialog narrows its as-you-type
> suggestions to species already recorded on this trip, instead of the full
> ~30k-entry reference list — much shorter once a trip has a few days behind
> it. Off by default (a brand-new trip has nothing to narrow to yet);
> remembers your last choice across sessions.

### When things are locked

Two independent locks hide the editing actions, each with a banner saying so:

- **A committed (frozen) day** — 🔒 the day is published; reset the trip to unlock.
- **A read-only master** — in an album, on a machine not permitted to write the
  master.

In Trip scope with only *some* days frozen, the actions stay available: they
operate on the editable pictures and skip the frozen ones.

> **In an album, a cull deletes MASTER pictures.** They are JPG-protected, go to
> the master's `.trash`, and are undoable — but this is the real collection, not
> a working copy. The album viewer therefore starts with the delete buttons
> hidden until a load has confirmed this machine may write.

---

## The culling grid

The per-day contact sheet — one card per picture — opened by **Review & rate
pictures**, or from a day row in the yellow panel. This is where a card of 800
frames becomes 40 keepers with names on them.

Unlike the Species Viewer, it shows **one day across all species**.

### The four view filters

These four are **mutually exclusive** — exactly one is active:

| Button | Shows |
|---|---|
| **⚠️** | Only pictures with a wrong / unresolved species name. **Right-click it** to switch the button to **No species yet** — the pictures with nothing tagged at all. Left-click always returns to the wrong-name set. |
| **📍** | Only pictures **without** GPS coordinates |
| **All** | Everything |
| **Species** | Only pictures that **have** a species — i.e. what an add-to-master commit would take |

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

Selecting cards raises the same selection bar, with **Set species…** and
**Clear selection**.

> **🔄 Refresh is the answer to "I changed it in PhotoLab and nothing happened."**
> The colour and rating live in the sidecar; SpeciesViewer re-reads on focus, but
> if you have both windows visible at once, press it.

### A workable culling order

Not prescriptive, but this is the order the filters are built for:

1. **All** + colour dots — first pass, tag rejects Blue/Purple, keepers Green up.
2. **✖B** / **✖P** — clear the rejects out of the way.
3. **⚠️** — fix the questionable identifications.
4. **Species** — see what will actually be committed.
5. **📍** — mop up the pictures with no coordinate.

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
| **✂ Crop** | Rough-cut a crop to hand to PhotoLab / Lightroom as a starting point. Aspect locked to the camera's nominal ratio. |
| Navigation | `←` / `→` / `Space` / mouse wheel |

Two behaviours worth knowing because they are deliberate and initially
surprising:

- **The 1:1 point stays put as you move through a group.** A set of similar
  shots therefore compares at the same crop, and the sharpest one stands out.
- **While the 100% tab is active, navigation steps over Blue and Purple
  pictures.** You flip only through the live candidates. The Fit tab and the
  grids keep normal navigation, so a reject is still reachable to re-judge.

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

## The master card context menu

Right-click a card backed by the master — in the Species Viewer, the album day
viewer, or the Biota Browser's species page — for the master operations. This is
one shared implementation, so the same menu appears wherever it applies.

| Action | Does |
|---|---|
| **Move to existing species…** | Reassign this picture to another species |
| **Sex / age / plumage…** | Set the picture's qualifiers |
| **Add new files (scan folder)…** | Pick up renders that appeared beside the picture |
| **Choose the website picture** | Pin which file represents the species, or let the code choose (biggest wins) |
| **Delete picture (move to trash)** | |

Two guards you will meet:

- *"Delete / Move blocked — picture has a JPG (protected)"* — a picture already
  published on the website is not deletable from here.
- *"Master is read-only — enable in Repository Settings"* — this machine is not
  permitted to write the master.

---

## Name-language profiles

Both the Biota Browser and the Species Viewers have a **Names** selector. It sets
which name reads out under each picture, as a **fallback chain** rather than a
single choice — so a profile like *EN/Sci* shows the English name where there is
one and the scientific name otherwise, and never falls back to Dutch.

The selector is a **per-session override**. The lasting default is in
*File ▸ Settings ▸ Species Settings*.

> Known gap: the album preview still shows scientific names regardless of the
> profile.

---

## What is missing from this chapter

Written from the code rather than from use, so the following are the parts most
likely to need your correction:

- The **culling order** above is my inference from what the filters make easy,
  not an observed workflow.
- **Sex / age / plumage** qualifiers are listed but not explained — they deserve
  their own section once the vocabulary is settled.
- The **map** views (from both viewers) are mentioned but not documented; they
  are arguably a fifth viewer.
