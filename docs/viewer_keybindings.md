# Viewer key bindings

These keys work the same in all three picture viewers — the **Species Image
Preview** grid, the **Rating Editor** grid, and the **Editor** — so
culling feels identical whichever one you're in.

`Ctrl+Home` / `Ctrl+End` additionally work in the **Biota Browser**'s card
grid (tree, search results and the flat all-species index). That grid has no
focused card — it is navigated by clicking — so there the pair scrolls to the
top / bottom rather than moving a cursor.

Implementation: [`speciesviewer/gui/viewer_keys.py`](../speciesviewer/gui/viewer_keys.py) (one
definition, called from each viewer's `keyPressEvent`). Keep this file in sync
when bindings change.

## Selection

| Key | Action |
|-----|--------|
| `[` | Set the start of a selection range at the focused picture |
| `]` | Set the end of a selection range at the focused picture |
| `Ctrl+A` | Select all pictures (grids) |
| `Ctrl+D` | Deselect all (grids; also Editor, same as `Esc`) |
| `Esc` | Clear the selection |

(`[` … `]` select an inclusive range in display order.)

In the **Editor**, right-clicking the image, the panel or a filmstrip
thumbnail opens the **same menu as the grid it was opened from**, for the
current selection (or just that one picture, if nothing's selected) — no need
to remember `[` / `]` first.

## Moving the cursor (grids)

| Key | Action |
|-----|--------|
| `←` / `→` | Move the cursor one picture left / right |
| `↑` / `↓` | Move the cursor one **row** up / down |
| `Ctrl+Home` | Jump to the **first** picture |
| `Ctrl+End` | Jump to the **last** picture |

## Rating, colour, and moving on

These apply to the **focused / current** picture and then **advance to the next
picture** (across group boundaries). Use the toolbar "Set rating…" / "Set
species…" for bulk edits over a multi-selection.

| Key | Action |
|-----|--------|
| `Ctrl+1` … `Ctrl+5` | Set rating to 1–5 stars |
| `q` `w` `e` `r` `t` | Set rating to 1–5 stars (aliases — easier than `Ctrl+5`) |
| `Ctrl+0` / `p` | Clear rating (0 stars) |
| `1` … `5` | Set colour label: **1 Red · 2 Yellow · 3 Green · 4 Blue · 5 Purple** |
| `0` | Clear colour label |
| `Space` | Move to the next picture (no change) |
| `←` / `→` | Previous / next picture (Editor) |
| Mouse wheel | Over the image in the Editor: up → next, down → previous |
| `f` | Editor: toggle **Fit ⇄ Camera JPEG** (the quick zoom double-click uses). The **100%** tab is a third tab, reached by clicking it |
| Double-click image | Editor: open the 100% tab centred on the clicked spot |
| `v` | **Play the movie** — in the grids and the Editor alike. On a movie, clicking the picture plays it too |

## 100% (full-resolution) view — Editor

A **100%** tab shows the picture decoded from the RAW at full sensor resolution
(the embedded preview is only ~3200×2400). It's decoded lazily on first view and
cached, so later views are instant. **Blue/Purple** pictures are skipped (already
rejected). Drag with the left mouse button to pan; the 1:1 point **stays put as
you move through a group** (→ / Space), so a set of similar shots compares at the
same crop and the sharpest one stands out.

While the 100% tab is active, **navigation steps over Blue/Purple** pictures
(→ / ← / Space / wheel) — you flip only through the live candidates. The Fit tab
and the grids keep normal navigation, so a reject is still reachable to re-judge.

## Crop suggestion — Editor

The **✂ Crop** button (top-right) rough-cuts a crop to hand to DxO PhotoLab /
Lightroom as a starting point — SpeciesViewer isn't a precise editor, it just
seeds the recipe. Click it and the viewer swaps to the **uncropped** frame with
an overlay:

- **Left-drag** to draw; drag a **corner** to resize; drag **inside** to move.
- The aspect is **locked to the camera's nominal ratio** (4:3, 3:2, …) so a 4:3
  body gives true 4:3 crops (not the sensor's 1.3344 → e.g. 1200×900, not
  1200×899). The crop's approximate size in RAW pixels shows under the rectangle.
- **⤢ Snap size** resizes the crop so its long side is the nearest standard —
  **1000 · 1200 · 1500 · 2000** px — keeping the aspect and centre. A long side
  within 5 % above a preset rounds **down** to it, otherwise it ceilings up; a
  long side past 2000 (+5 %) is left free with a note.
- **Enter** applies · **Esc** cancels · double-click also applies.

Applying writes the crop to **both** the DxO `.dop` and the Lightroom `.xmp`
(creating the sidecar if none exists), then the grid, filmstrip, and large image
re-decode to show it. Note: PhotoLab reads a sibling `.dop` on **first
discovery** of a RAW — write the crop before the folder is opened in PhotoLab, or
force a "read from sidecar" there. The tool is offered for RAW pictures during
culling.

## Copy and paste (all viewers)

| Key | Action |
|-----|--------|
| `Ctrl+C` | Copy the species name |
| `Ctrl+V` | Paste it onto the selection (else the focused picture) |
| `Ctrl+Shift+C` | Copy GPS + place |
| `Ctrl+Shift+V` | Paste GPS + place likewise |

The two buffers are **trip-wide**: they live in the app, not in a viewer, so a
name or a fix copied while culling 2026-09-13 pastes straight into 2026-09-14.
They last for the session and are not written to disk — copying a name again is
cheap, and a coordinate pasted from a buffer restored weeks later would be a
data-integrity bug wearing a convenience's clothes.

What travels is the **stored subject**, not the displayed name: `greater ani,
male`, so the sex/age/plumage qualifier goes with it, exactly as it does for
`a`. Pasting the resolved Dutch display name would write a different string
than the source picture carries.

`Ctrl+C` with several pictures selected copies the species they **agree** on. If
they disagree it copies nothing and **leaves the buffer alone** — a copy that
silently picked one of two names would be worse than one that refuses, because
the wrong name then travels to every later paste. Blank pictures among named
ones don't count as disagreement.

`Ctrl+Shift+C` is singular by nature, so on a selection it takes the first
picture **in display order** that actually has a fix — on a range, that reads as
"the place this group was taken".

`Ctrl+V` writes exactly like `a` does, through the same path: one batched
exiftool call, a sidecar authored where a RAW has none, the name bumped to the
front of the **recent species** list, and a message box if nothing reached the
disk.

The Species Image Preview's right-click **Copy GPS + place** / **Paste GPS +
place** use the same buffer as the keys — the menu and the key are two doors
into one drawer.

A paste writes the **whole selection in one pair of exiftool calls**, not one
pair per picture, so twelve pictures cost the same two calls as three hundred.
(The right-click paste used to do it per picture; it now goes through the same
batched path.) The cursor shows a wait while it writes.

**Where paste-GPS does nothing:** the **Rating Editor** can copy GPS but not
paste it. Its collection keeps coordinates in memory for the map but has no
location-writing plumbing, so a paste there reports *"Nowhere to write GPS on
these pictures"* rather than pretending. Species copy and paste work there
normally.

## The map's no-GPS strip

Separate from the picture viewers above: these work in the **Map** dialog, on
the strip of pictures that have no coordinates yet.

| Key | Action |
|-----|--------|
| `Ctrl+A` | Select every picture still in the strip |
| `[` / `]` | Select the range between two thumbnails |
| `p` | **Suggest a position** — interpolated from the photos taken before and after |
| `n` | **Find a place by name…** |
| `z` | **Peek** — the picture full-size, read-only |
| `Enter` | **Place here** — confirm the pending placement |
| `Esc` | Cancel the pending placement (and, with none pending, close the map) |
| Mouse wheel | Scroll the strip **sideways**, one thumbnail per notch (it is one row; there is no other direction to go) |

`p` and `n` act on the selection, using the last-clicked thumbnail as the
reference — the keyboard equivalent of the cell under the pointer when you
right-click. Neither **commits** anything: both land in the same draggable pin
and *Place here / Cancel* bar as every other placement route, so the spot is
still yours to nudge or reject. `p` estimates from **one** picture (a time
interpolation needs one capture time) but places **all** of them, and the
confirm bar names which picture the estimate came from.

`z` opens the Quick view in **peek** mode: arrows, zoom, `Esc`, and nothing
else. Every culling key is off there — while geotagging, an `x` or a `3`
pressed out of habit would label a picture you were only checking — and one
pressed anyway says so in the badge rather than doing nothing. It steps through
the whole strip, not just the selection, because the neighbours are usually
what remind you where a run was taken.

When the confirm bar opens, **Place here** takes the focus and is filled in the
accent colour, and the bar itself says *Enter places · Esc cancels* — so a whole
run can go `[` … `]` → `p` → `Enter` without touching the mouse. The pin stays
draggable throughout; taking the keyboard doesn't take the mouse.

After a placement the strip **lands on the picture that followed the ones you
placed**, back at the left edge. (It used to keep its scroll position while the
content shrank underneath it, so placing fifteen skipped fifteen more.)

## Filtering and refresh (Species Image Preview only)

Unlike everything above, these three are **not** shared — they exist only in the
Species Image Preview grid, which is the one that carries the colour-filter bar.

| Key | Action |
|-----|--------|
| `g` | Toggle the **keepers** filter — Red + Yellow + Green in one grid rebuild; press again to clear it |
| `h` | Clear the colour filter |
| `F5` | Re-read the day **from disk** and repaint the grid |

`g` exists because picking the three keeper colours from the toolbar is three
separate grid rebuilds; `g` is one.

## Dialogs (apply to the selection, else the focused picture)

| Key | Action |
|-----|--------|
| `s` | Open the **species** dialog |
| `a` | **Quick species** — copy the species the selection already agrees on, no dialog (falls back to `s`) |
| `Ctrl+C` / `Ctrl+V` | **Copy / paste a species name** between pictures — and between days |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | **Copy / paste GPS + place** likewise |
| `c` | Open the **rating / colour** dialog |
| `f` | **Full resolution** — pre-decode to the 1:1 cache |
| `l` | **Get location** — reverse-geocode GPS → write `Location/City/State/Country` to the XMP |
| `z` | **Quick view** — the camera JPEG only, zoomable; `←`/`→` step, `Esc` closes |
| `v` | **Play the movie** under the cursor, in the Quick view (with sound; `Space` pauses). Does nothing on a still |
| `x` | Set colour to **Blue** — no dialog, one write, then moves on (see below) |

`z` opens a stripped-down viewer — just the camera's embedded JPEG, no RAW
decode and no metadata panel, so it appears instantly. It exists for a fast
"zoom in to check the species" without the Editor's startup cost.
Scroll to zoom, double-click for 1:1, drag to pan. The **same culling keys as
the grid** work here — `1`–`5` colour, `q w e r t` / `Ctrl+1`–`5` stars, `0`
clear, `x` quick-Blue — each writes and steps to the next picture, so you can
cull while pixel-peeping. On close the grid cursor lands on the last picture
you looked at and any card you changed repaints; nothing else changes.

`a`, `s` and `c` work in the Quick view too. The view itself owns no species or
rating UI — building one would be exactly the startup cost it exists to skip —
so it hands the action up to the grid that opened it, which runs its own
handler. Two things follow from that:

- Any dialog opens **as a child of the Quick view**, so it can't end up behind
  it. The Quick view stays open underneath.
- They act on the `[` … `]` **range** when one is marked, else on whatever the
  grid already had selected when you pressed `z` (a `Ctrl+A` or a click
  selection counts — it needn't be bracket-made), else on the picture on
  screen. Marking a fresh range here replaces the inherited one. That's
  deliberately unlike `x` and the digit keys, which are write-and-advance keys
  and always act on just the current picture.

The Quick view's title carries **every name the picture has** — Dutch, English
and scientific, ordered by your Preferred-names profile, with the sex/age
suffix — so an `a` or `s` there is visible without a panel, and the name can be
checked against a field guide in either language.

`a` also reports itself in a **top-right badge** — *"Species name set for 23
pictures"* — in the same style as the top-left interaction hint. It clears on
the next key, typically the `←` / `→` that moves you on, rather than on a
timer, so it is still there if you looked away. `s` and `c` get no badge: their
dialogs report their own result and then close, which is the acknowledgement.
`a` has neither, and silence after a write that touched thirty pictures is
indistinguishable from a key that did nothing. A successful dialog-free `a` also clears the `[` … `]` range,
so the next key press lands on one fresh picture instead of re-acting on the
group; a fall-back to the dialog leaves the range marked, so cancelling doesn't
lose your selection.

`a` is `s` minus the dialog, for the one case that needs no decision. Name one
guide frame of a burst, select the burst with `[` … `]`, press `a`: the name is
copied onto all of them in one batched write. Any **sex / age / plumage**
qualifier rides along — it lives in the subject string next to the name
(`greater ani, male`) — so the whole burst gets the attribute too.

A name applied with `a` is bumped to the front of the **recent species**
quick-pick list, exactly as a dialog commit would — the qualifier is stripped
first, so a token like `juv` never lands in the list as if it were a species.

`a` opens the regular **Set species** dialog instead whenever the selection
doesn't say one unambiguous thing:

- **nothing** in the selection is named — there'd be nothing to copy;
- **several different** names among them;
- the names agree but the **qualifiers don't** (`greater ani, male` beside
  `greater ani, female`) — picking a sex for the whole burst is a decision, not
  a keystroke;
- only **one** picture is targeted — there's nothing to copy it onto.

`l` reverse-geocodes each picture's GPS (OpenStreetMap/Nominatim, English) and
writes the place name into the XMP. It's throttled and **deduplicated by
distance** — consecutive shots within ~100 m of the last fix reuse it — so
`Ctrl+A` then `l` does a whole day with only a handful of lookups. Pictures
with no GPS (or nowhere to write) are skipped.

`x` is a shortcut for the common "prep this batch for delete" step: select a
range with `[` … `]`, press `x` to stamp them all **Blue** in one write and
move the cursor past the group (clearing the selection), then delete the
Blue-tagged pictures from the toolbar — no rating/colour dialog in between.
With nothing selected, `x` stamps just the focused picture and advances to
the next one, same as the digit colour keys. Rating is left untouched.

Notes:
- Keys are ignored while a **text field** (description or species name) has
  focus, so typing there is never hijacked.
- Rating/colour are only applied when the picture can carry them (e.g. a RAW
  with an XMP sidecar); otherwise the key is ignored.
- The colour order matches the on-screen swatches (Red, Yellow, Green, Blue,
  Purple).
