# Scope — Editor parity (and what to call it)

*What each picture surface offers today, where they disagree, and what to decide
before any of it is built. Written 2026-09-25 from the code; nothing built yet.*

---

## Why now

Right-clicking in the large viewer offered **one** action (*Set rating…*) where
the grid it was opened from offered **eight**. The window has quietly become the
place where a picture is edited — species, rating, colour, crop, exposure,
headline, description, Make JPG, hand-off to PhotoLab — while its action set
stayed where it was when it was a viewer.

## What exists today

Four menus and one button bar, across three files. **A** and **B** are grids;
**C**, **D** and **E** all live in the large viewer.

| Action | A. Culling grid | B. Trip Species grid | C. Large: panel menu | D. Large: filmstrip | E. Large: bottom bar |
|---|:--:|:--:|:--:|:--:|:--:|
| Set species… | ✅ | ✅ | — | ✅ | ✅ |
| Move to existing species… | — | ✅ | — | — | — |
| Sex / age / plumage… | ✅ | — | ✅ | — | — |
| Set rating… | ✅ | ✅ | — | ✅ | ✅ |
| **Set exposure…** | ✅ | — | — | — | — |
| Full resolution | ✅ | ✅ | — | — | — |
| Create JPG (embedded + crop) | ✅ | ✅ | — | — | — |
| Get location | ✅ | ✅ | ✅ | — | — |
| Get GPS from location | ✅ | — | ✅ | — | — |
| Edit location… | ✅ | — | ✅ | — | — |
| Edit headline / description… | ✅ | ✅ | ✅ | — | — |
| Copy GPS + place | ✅ | — | — | — | — |
| Paste GPS + place | ✅ | — | — | — | — |
| Use as trip/album picture | ✅ | — | — | — | — |
| Add new files (scan folder)… | — | ✅ | ✅ | — | — |
| 🗑 Delete | ✅ | — | — | ✅ | — |
| Clear selection | — | — | — | — | ✅ |

Source: `image_viewer.py` ~1794‑1862 (A), `rating_editor.py` ~3613‑3645 (B),
`large_image_view.py` ~2056‑2087 (C), ~2867‑2873 (D), ~1274‑1291 (E).

## What the table shows

**1. Exposure is split in half, and that is the reported bug.** The culling grid
has a batch dialog (`_open_batch_exposure_dialog`, `image_viewer.py:2217`) but no
slider. The large viewer has a live slider — for **one** picture, in the top bar
— but no way to apply a value to a selection. So each surface has the half the
other is missing. That is why the group dialog was remembered correctly and could
not be found: it exists, in the other grid.

**2. The Trip Species grid is missing exposure entirely.** Both halves are absent
there, which is the surface the report came from.

**3. The large viewer's filmstrip menu is gated down to almost nothing.** It shows
*Set species* only if `can_edit_species`, *Set rating* only if `can_edit_rating`
or `can_edit_color`, *Delete* only if `_allow_delete`. Opened from the Trip
Species Viewer, that left one item. The gates are probably right; the emptiness
is the symptom.

**4. Three ways to do the same two things, inside one window.** *Set species* and
*Set rating* are reachable from the filmstrip menu (D), the bottom bar (E), and
the right panel's own inline stars and colour dots. This is the duplication the
report suspected on the thumbs — features arriving without the older path being
retired.

**5. The bottom selection bar is unique in the app.** Nothing else uses a
persistent action bar for a selection; every other surface uses a context menu.
*Correction (2026-09-30): not true — both grids (A and B) also raise a selection
bar with Set species… / Clear selection (B adds Move to species…). The Editor's
was the only one on a non-grid window.*
It is also where the duplication in (4) lives.

**6. Some gaps are arguably correct.** *Full resolution* pre-decodes so the 100%
view is instant — the large viewer already has an 80% tab, so it may not need the
action. *Copy/Paste GPS + place* is inherently a two-picture gesture and may
belong to a grid. These should be decided, not copied by default.

## Decisions (taken 2026-09-25)

**D1 — The target: one agreed action set; each surface shows the subset it can
honour.** The more expensive option, chosen deliberately — *"more cost means more
useful"*. So there is one canonical list of things you can do to a picture, and a
surface hides an entry only when it genuinely cannot honour it (read-only master
card, frozen day, no RAW), never because nobody got round to adding it.

**D5 — The Editor inherits from the grid it was opened from.** It is not a
fixed menu: opened from the culling grid it offers the culling grid's set, opened
from the Trip Species Viewer it offers that one. The two grids are NOT being
reconciled with each other in this work — A vs B stays as it is.

**D2 — Exposure: both halves, everywhere.** The Editor keeps its live slider
for the focused picture and gains an *Apply to N* beside it, mirroring the rating
row's existing *Apply to selected*. The batch dialog
(`_open_batch_exposure_dialog`) is added wherever the action set includes
exposure, so a value can be typed for a group. Slider for one, dialog for many.

**D3 — The bottom selection bar is retired into the context menu.** Selection
actions move to right-click, matching every other surface; the selection *count*
may stay as a plain label. This removes two of the three duplicate paths in
finding (4) as a side effect.

**D4 — Rename in the UI only.** Window title, menu entries and the manual say
**Editor**; the class stays `LargeImageView`. Same split Album/virttrip
already uses between display name and code name.

## Order of work

1. **Exposure** (D2) — the reported bug, and the one costing time now.
2. **The rename** (D4) — cheap, and it makes the parity requirement self-evident
   to anyone who opens the window afterwards.
3. ~~**Retire the bottom bar** (D3)~~ ✅ 2026-09-30 — removed outright, count label
   included; right-click, `s`/`c`, Esc/Ctrl+D cover everything it did. The two
   grids' selection bars went too (his call, same day): no surface in the app
   has a persistent selection bar any more — a selection is acted on by
   right-click, and cleared with Esc.
4. **The rest of the parity table** (D1 + D5) — mechanical once the action set is
   named and the Editor inherits it from its host.
   *Built as delegation, not copying (2026-09-30):* the Editor's right-click —
   image, panel or filmstrip — opens the host grid's own card menu when the host
   offers `editor_context_menu(key, extras)`; hosts without one (Biota Browser,
   maps) keep the Editor's own menus. The Editor adds only *Sex / age /
   plumage…* for a master picture, and only when the grid's menu lacks it. The
   host must push its edits back (`refresh_after_host_action`, `drop_keys`,
   `clear_selection`). Dialogs are NOT re-parented to the Editor; none opened
   behind it in testing. Stage 1 culling grid ✅; stage 2 Trip Species Viewer.

Each step is independently testable and independently useful, so they can land as
separate commits and be abandoned at any point without leaving a half-state.

## Not in scope

The four surfaces' *filters* and layout (columns, scope selector, colour
filters). This is about actions on a picture, nothing else.
