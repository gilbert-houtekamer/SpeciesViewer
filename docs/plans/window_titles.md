# One window-title scheme — proposal for review

Backlog ch. 23 ("Brazil trip — field feedback"). Agreed in principle 2026-09-25, deferred on
2026-10-05 because of one open question: the date part is a day, a range, or nothing,
depending on the window. This proposal answers that question and lists every title it changes.

## The problem

With half a dozen windows open, the titles don't help you find the one you want:

- They follow no pattern. The grid says `Species Image Preview (2026-09-15) — …`, the Editor
  says `Rufous hornero (3 of 22) — Editor`, the Trip Species Viewer says only
  `Trip Species Viewer`, whatever day or scope it shows.
- The Windows taskbar shows about 15–20 characters of a title. Two Trip Species Viewers, or
  two Image Viewers on different days, look identical there.
- The Editor always sits on top of the grid it was opened from (see "Not in this proposal").

## The rule

```
<Kind> — <Where> · <What>  (n of m)
```

- **Kind** is the window's name, always first and always the same words: *Image Viewer*,
  *Trip Species Viewer*, *Album Species Viewer*, *Master Species Viewer*, *Editor*,
  *Quick View*, *Biota Browser*, *Map*. On the taskbar, all windows of one kind sort together.
- **Where** is what the window is showing **right now**. This is the answer to the open
  question: it is not "a date", it is the window's **scope**, named the way you would say it.
  - Day scope: the day, `2026-09-15`.
  - Trip scope: the trip's name, `Brazil 2026`, not its date range. You know the trip
    by name; the range is longer and says less.
  - Trip+Master scope: `Brazil 2026 + master`.
  - Album: the album's name, and its day when a day is selected.
  - Master: `master`.
- **Where** follows the scope selector. Switching Day → Trip changes the title at once. Today
  the title is set once when the window opens, which is half of why two viewers look the same.
- **What** is the current filter or selection, when there is one: a species, `12 missing GPS`,
  `JPG only`. It is left out when nothing is filtered.
- **(n of m)** stays even when filtered to one species. It tells you where you are.

The separators are the ones the app already uses: ` — ` after the kind, ` · ` between parts.

## Every window it changes

| Window | Today | Proposed |
|---|---|---|
| Main window | `Species Viewer 0.2` | unchanged |
| Day grid (culling / day pictures) | `Species Image Preview (2026-09-15) — all 935 pictures` | `Image Viewer — 2026-09-15 · 935 pictures` |
| … with a filter | `Species Image Preview (2026-09-15) — 12 missing GPS · JPG only` | `Image Viewer — 2026-09-15 · 12 missing GPS · JPG only` |
| Trip Species Viewer, Day scope | `Trip Species Viewer` | `Trip Species Viewer — 2026-09-15 · Rufous hornero` |
| … Trip scope | `Trip Species Viewer` | `Trip Species Viewer — Brazil 2026 · Rufous hornero` |
| … Trip+Master scope | `Trip Species Viewer` | `Trip Species Viewer — Brazil 2026 + master · Rufous hornero` |
| Album Species Viewer | `Album Species Viewer · Tanzania 2023 · <source note>` | `Album Species Viewer — Tanzania 2023 · 2023-02-11 · <source note>` |
| Master Species Viewer | `Master Species Viewer` | `Master Species Viewer — Rufous hornero` |
| Editor, one species | `Rufous hornero (3 of 22) — Editor` | `Editor — Rufous hornero (3 of 22) · 2026-09-15` |
| Editor, no species filter | `2026 Brazilie - N9153705 (1 of 935) — Editor` | `Editor — 2026-09-15 (1 of 935) · N9153705` |
| Quick View | `Quick view — <file> — <names> (3 of 22)` | `Quick View — Rufous hornero (3 of 22) · N9153705` |
| Biota Browser | `Biota Browser — Master collection` | unchanged (already fits) |
| Trip map | `Map — Brazil 2026 (whole trip)` | `Map — Brazil 2026` |
| Day map | `Map — <name> (<scope>)` | `Map — 2026-09-15` |
| Species map | `Species distribution map` | `Map — Rufous hornero` |

The species in a title is the name in your display profile (the same name the card shows),
with no scientific name added. A title that holds three names truncates to nothing useful.

Short-lived dialogs (*Set species*, *Delete Blue pictures*, progress windows) are not
included. You never look for them on the taskbar.

## Decisions for you

1. **"Image Viewer" instead of "Species Image Preview".** You already agreed to this on
   25 September, knowing it gives three windows called *… Viewer*. Still yes?
2. **Trip scope shows the trip's name, not its date range.** I recommend the name. The range
   (`2026-08-28 … 2026-09-20`) uses most of the taskbar's 20 characters to say less.
3. **Editor title: picture first or kind first?** Today the picture is first
   (`Rufous hornero (3 of 22) — Editor`), which is the most distinctive part on the taskbar.
   The scheme puts the kind first (`Editor — Rufous hornero …`). I recommend kind first, for
   consistency: with several Editors open, the taskbar groups them and the hover preview
   shows the full title.

## How it would be built

- One small helper, `window_title(kind, where="", what="", pos=None)`, in `gui/widgets.py`,
  so the rule is written once.
- Each window calls it wherever its title is set today, and also on a scope change. In the
  Species Viewers that means connecting the Day/Trip/Trip+Master radios and the day combo to a
  title refresh. The Image Viewer's existing `_update_title_for_filter` keeps its filter
  logic and only changes its first part.
- Files touched: `image_viewer.py`, `rating_editor.py`, `large_image_view.py`,
  `quick_viewer.py`, `map_view.py`/`mainwindow.py` (map titles), `widgets.py`.
  This changes titles only. No data and no behaviour.
- Docs: the manual names windows by their titles in a few places (*Species Image Preview*);
  those mentions change with it.

## Not in this proposal: the Editor stays on top of its grid

The Editor is opened as a child of the grid it came from. Windows always keeps a child window
above its owner, so you can't bring the grid in front of its Editor. Changing that would mean
opening the Editor without an owner. That touches how the grid learns about edits made in the
Editor (it repaints its cards when the Editor closes), so it is a separate change with its own
risk. I suggest doing the titles first. If, with clear titles, the stacking still bothers you,
we take that on next.

## As built (2026-10-09)

Decisions: 1 yes, 2 the trip's name, 3 kind first; the main window becomes *Species Viewer 0.3*;
the Editor stays a child of its grid. Where the build differs from the table above:

- The trip's name is its short name from `trip.ini` (`brazil2026`), the same kind of name an
  album already shows (`tanzania`). Its long title is too long for a taskbar.
- Quick View keeps all three names in its title. It has no side panel, and it is where a bird
  gets identified against a field guide in either language.
- The species map can overlay several species, so its title lists the species shown and
  follows the legend (`Map — Rufous hornero · Firewood-gatherer`).
- The helper is `gui/window_title.py`, not `widgets.py` (that module holds widgets only).
- The Editor's title carries all three names, in the display profile's order
  (`Editor — Rufous hornero · Roodbruine ovenvogel · Furnarius rufus (3 of 22) · 2026-09-15`):
  inside the Editor the other language is sometimes the one you recognise. The Species Viewers
  keep one name. A landscape keeps its headline.
- The Image Viewer lists a colour filter in the colour bar's order (red, yellow, green, blue,
  purple, grey), not alphabetically.
