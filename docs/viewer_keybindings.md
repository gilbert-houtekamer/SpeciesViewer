# Viewer key bindings

These keys work the same in all three picture viewers — the **Species Image
Preview** grid, the **Rating Editor** grid, and the **Large Image View** — so
culling feels identical whichever one you're in.

Implementation: [`qt/gui/viewer_keys.py`](../qt/gui/viewer_keys.py) (one
definition, called from each viewer's `keyPressEvent`). Keep this file in sync
when bindings change.

## Selection

| Key | Action |
|-----|--------|
| `[` | Set the start of a selection range at the focused picture |
| `]` | Set the end of a selection range at the focused picture |
| `Ctrl+A` | Select all pictures (grids) |
| `Esc` | Clear the selection |

(`[` … `]` select an inclusive range in display order.)

## Moving the cursor (grids)

| Key | Action |
|-----|--------|
| `←` / `→` | Move the cursor one picture left / right |
| `↑` / `↓` | Move the cursor one **row** up / down |

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

## Dialogs (apply to the selection, else the focused picture)

| Key | Action |
|-----|--------|
| `s` | Open the **species** dialog |
| `c` | Open the **rating / colour** dialog |
| `f` | **Full resolution** — pre-decode to the 1:1 cache |
| `l` | **Get location** — reverse-geocode GPS → write `Location/City/State/Country` to the XMP |

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
