# Viewer key bindings

These keys work the same in all three picture viewers — the **Species Image
Preview** grid, the **Rating Editor** grid, and the **Large Image View** — so
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
| `Ctrl+D` | Deselect all (grids; also Large Image View, same as `Esc`) |
| `Esc` | Clear the selection |

(`[` … `]` select an inclusive range in display order.)

In the **Large Image View**, right-clicking a filmstrip thumbnail is a second
way in: it opens **Set species…** / **Set rating…** for the current
selection (or just that one picture, if nothing's selected) — no need to
remember `[` / `]` first.

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
| `←` / `→` | Previous / next picture (Large Image View) |
| Mouse wheel | Over the image in the Large Image View: up → next, down → previous |
| `f` | Large Image View: toggle the **100% (full-resolution)** tab |
| Double-click image | Large Image View: open the 100% tab centred on the clicked spot |

## 100% (full-resolution) view — Large Image View

A **100%** tab shows the picture decoded from the RAW at full sensor resolution
(the embedded preview is only ~3200×2400). It's decoded lazily on first view and
cached, so later views are instant. **Blue/Purple** pictures are skipped (already
rejected). Drag with the left mouse button to pan; the 1:1 point **stays put as
you move through a group** (→ / Space), so a set of similar shots compares at the
same crop and the sharpest one stands out.

While the 100% tab is active, **navigation steps over Blue/Purple** pictures
(→ / ← / Space / wheel) — you flip only through the live candidates. The Fit tab
and the grids keep normal navigation, so a reject is still reachable to re-judge.

## Crop suggestion — Large Image View

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

## Dialogs (apply to the selection, else the focused picture)

| Key | Action |
|-----|--------|
| `s` | Open the **species** dialog |
| `c` | Open the **rating / colour** dialog |
| `f` | **Full resolution** — pre-decode to the 1:1 cache |
| `l` | **Get location** — reverse-geocode GPS → write `Location/City/State/Country` to the XMP |
| `z` | **Quick view** — the camera JPEG only, zoomable; `←`/`→` step, `Esc` closes (moves the cursor to where you stopped) |

`z` opens a stripped-down viewer — just the camera's embedded JPEG, no RAW
decode and no metadata panel, so it appears instantly. It exists for a fast
"zoom in to check the species" without the Large Image View's startup cost.
Scroll to zoom, double-click for 1:1, drag to pan. Nothing is written; on close
the only change is the grid cursor lands on the last picture you looked at.

`l` reverse-geocodes each picture's GPS (OpenStreetMap/Nominatim, English) and
writes the place name into the XMP. It's throttled and **deduplicated by
distance** — consecutive shots within ~100 m of the last fix reuse it — so
`Ctrl+A` then `l` does a whole day with only a handful of lookups. Pictures
with no GPS (or nowhere to write) are skipped.

Notes:
- Keys are ignored while a **text field** (description or species name) has
  focus, so typing there is never hijacked.
- Rating/colour are only applied when the picture can carry them (e.g. a RAW
  with an XMP sidecar); otherwise the key is ignored.
- The colour order matches the on-screen swatches (Red, Yellow, Green, Blue,
  Purple).
