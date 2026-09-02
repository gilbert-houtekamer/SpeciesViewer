# SpeciesViewer — culling grid performance (1000+ picture days)

A stepped plan for making the Species Image Preview / culling grid fast on big
days, on a laptop, without new hardware. Written 2026-08-31 after live timings on
a Brazil day (1194 pictures).

---

## STATUS

| Step | What | State |
|------|------|-------|
| **1** | Open into a "Start with" subset + counts bar | **SHIPPED `fd8405c` (live-tested OK)** — `image_viewer.py`, no branch |
| **2A** | Grid width fills the window + `grid_columns` global + truncation fix + stable row height | **SHIPPED (live-tested)** — `image_viewer.py`, `rating_editor.py`, no branch |
| **2B** | In-RAM scaled-thumbnail cache, grid + filmstrip, with crop invalidation | **SHIPPED (live-tested)** — `gui/thumb_cache.py`, no branch |
| 2C | Persistent on-disk thumbnail cache at the stable width | **BUILT, pending test** — branch, `gui/thumb_cache.py` + providers, in the existing per-folder preview cache |
| 3 | Filter = show/hide, build cards once (lazy) | **BUILT, pending test** — branch `sv_culling_grid_showhide`, `image_viewer.py` only |
| — | Thumbnail aspect from the day's own images (not fixed 3:2) | **BUILT, pending test** — branch |
| — | Preview dialog: own taskbar button + reject too-small saved geometry | **BUILT, pending test** — branch |
| — | Look & Feel: "Culling grid" section (columns / fill % / Start-with default) | **BUILT, pending test** — branch |
| 4 | Viewport virtualization (recycle ~40 cards) | not started — **own branch**, the real scalability answer, only if 3 isn't enough |
| 5 | Cheaper card (one custom-painted widget) | not started — only if 3+4 fall short |
| 6 | Lazy controls (paint stars/swatches, real widgets on hover) | not started — scoped version of 5 |
| 7 | Hoist per-widget stylesheets into one shared sheet | not started — cheap, low risk, any time |

---

## The measured problem

Every filter keystroke — `g` (Red+Yellow+Green), `h` (clear), add/remove a
colour, the find box, the JPG toggle — calls `_rebuild_grid_from_keys`, which
**destroys every card and builds new ones** for the visible set. Two costs stack:

1. **Widget churn.** A framed `PictureCard` is ~30 `QWidget`s + ~10 layouts +
   ~15 individual `setStyleSheet` parses (`StarWidget` = 6 widgets,
   `ColorSelector` = 7 swatches, ~10 labels, badges, frames). `h` on a 1194-pic
   day ≈ **~35,000 widgets created** while the previous ~35,000 are torn down,
   all on the GUI thread.
2. **No in-RAM thumbnail cache.** `PreviewCollection.request_image` has no pixmap
   cache — every rebuild re-reads each picture's preview JPG from the AppData
   cache, `QImage`-decodes it, crops, and rescales. ~1194 disk decodes + ~1194
   GUI-thread pixmap hand-offs, every time.

Time scales with visible-card count, which matched the live timings:

| Action | Visible set | Time |
|--------|-------------|------|
| open (clean day → `_active_filter = "all"`) | all 1194 | ~40 s |
| `g` (keepers filter) | partial | ~10 s |
| add grey | bigger | ~20 s |
| `h` (widen back to all) | all 1194 | ~40 s |
| add grey again | smaller | ~8 s |

Narrowing clicks are the fast direction; the initial "all" build and `h`
(widening) are the slow ones.

---

## Step 1 — "Start with" subset + counts bar   (SHIPPED, pending test)

**Idea (user, 2026-08-31):** don't open into "all". A resumed cull only needs
the ~20 % still to decide. Show the day's colour tally and open straight into
the subset you're working on, so the single biggest rebuild never happens.

**What shipped** (`gui/image_viewer.py`, no branch — additive, low risk):

- A **"Start with:" bar** under the toolbar (only where the collection can
  filter by colour label — `PreviewCollection` fpr mode + `SidecarCollection`
  culling mode). Three buttons with live counts:
  - **Unrated (N)** — `color_filter = {Grey}` (no label yet)
  - **To review (N)** — `{Grey, Red, Yellow, Green}` — everything not marked for
    deletion
  - **All (N)** — no colour filter (today's full build)
  - plus a grey summary: `· 38 blue · 12 purple already set`
- On a **clean day** (the normal culling case — the branch that used to force
  `self._base_keys()`), the grid now opens into the **remembered** start subset
  (`[culling] start_subset`, default `unrated`) instead of all 1194.
- Clicking a button re-filters once and **persists** the choice, so after the
  first time it is zero-click.
- The colour tally is `len(collection.color_keys({name}))` per label — in-memory
  colour lookups, a few ms even on a 1000+ picture day. Refreshed on every
  `_apply_filters`.
- The bar is **hidden** for focused opens (warnings / missing-GPS /
  Add-to-Master / JPEG-Files cell) — those already open on a deliberate subset.

**Key seams:** `ColorFilterBar.reflect(colors)` (set the visual selection with
no rebuild callback); `_init_start_subset()` (open-cascade helper);
`_apply_start_subset(kind)` (button handler); `_refresh_start_bar()` (relabel +
mark the active button); `_colour_counts()`.

**Why it helps exactly when it should:**
- Fresh, un-culled day → everything is unrated → "Unrated" = all 1194 → same
  cost as today. No regression.
- Partially culled day (reopening to finish) → "Unrated" ≈ 20 % → the 40 s open
  becomes ~8 s, and you never build "all" unless you click **All**.

**Known limits (accepted for step 1):**
- Clicking **All** / pressing `h` still pays the full build once — opt-in, rare.
  Step 2 removes the decode half so even that drops to ~20 s.
- `h` clears the colour filter to "All" (unchanged). A later refinement could
  make `h` reset to the start subset instead.
- The bar, once hidden for a focused open, stays hidden for that session.

**Test:** open a partially-culled 1000+ day → lands on Unrated (~20 %), fast;
the counts read true; switching subsets re-filters in one rebuild; the choice
sticks across reopen; **All** still works (slow, as before); focused opens
(Trip Overview GPS cell, warnings) don't show the bar.

---

## Step 2 — stable sizing + in-RAM thumbnail cache   (SHIPPED, live-tested with fixes)

Caching and stable sizing are the same problem: a cached scaled pixmap is only
reusable if the same width is asked for every time.

### 2A — grid card width fills the window, stable across sessions

- **`grid_columns`** is a **global** `[culling]` setting now (default 5). The
  `Per row:` spinner writes it, so every day opens with the same column count.
- `_grid_card_width()` = `viewport_width ÷ columns − gaps`, using the scroll
  area's **viewport** (inside the scrollbar + frame). The grid fills the window
  edge to edge — no leftover right margin, no clipped last column. Stable across
  sessions because the window geometry is remembered and the column count is
  global. (A first attempt sized to a fixed % of the *screen* — it left a big
  right margin when the window wasn't exactly that wide. Viewport-relative +
  remembered geometry gets the stability without the gap.)
- **Fit fix:** `grid_layout.setContentsMargins(0,0,0,0)` (default ~9 px margins
  were eating the budgeted width); vertical scrollbar `ScrollBarAlwaysOn`;
  `_grid_card_width` subtracts a scrollbar width + slack defensively (the
  viewport is still reported one scrollbar too wide during the batched build);
  `_apply_grid_width` pins `grid_widget` to exactly `columns × card_w + gaps` so
  it can never exceed the viewport; and a debounced `resizeEvent`
  (`_on_resize_settled`) rebuilds the visible grid at the new width when the
  window is resized — or when the first-open viewport wasn't final yet. Net: the
  grid always fits with a small even right margin, no clipped last column, no
  horizontal scrollbar.
- **No visible reflow during the build:** `grid_widget.setUpdatesEnabled(False)`
  for the whole batched build, re-enabled at `_finalize_load`. On a warm cache
  every card + thumbnail is set synchronously, so the new grid appears in one
  paint. (A cold first load still trickles thumbnails in afterwards — fixed row
  height keeps that calm; fully eliminating it needs a fixed *card* height,
  which comes with step 3/4.)
- **Stable row height during load:** the thumbnail area is a fixed 3:2 height
  reserved up front; a portrait shot is scaled down to fit and letterboxed, not
  allowed to push the row taller. Rows no longer jitter as images stream in.
- First open with no remembered geometry defaults to ~92 % of the screen.
- Known, accepted: if you change `Per row:` while a *focused* view (missing-GPS,
  warnings) is showing, that view keeps the old width until reopened; the next
  full open applies the new column count everywhere.

### 2B — in-RAM scaled-pixmap cache

- `gui/thumb_cache.py`: `ThumbCache` keyed by `(key, target_w, image_sig)`,
  evicted by a **byte budget** (`_MAX_BYTES = 500 MB` — self-tunes to screen /
  column count; holds a full ~1200-pic day's grid + filmstrip). `image_sig` is
  the collection's existing "image changed" signature (`_img_sigs` /
  `_image_sigs` — source + `.dop` mtimes).
- **Explicit `invalidate(key)`** on top of the sig, called from `set_crop`,
  `create_blog_jpg`, `_bake_heic_crop`, and `refresh()` — because a crop on a
  picture with **no `.dop`** is written to the `.xmp`, which the signature does
  not stat, so the sig alone would keep serving the stale uncropped pixmap
  (found in testing: cropped image correct in the large viewer, stale in the
  grid + filmstrip). A colour/rating edit does NOT invalidate.
- One `ThumbCache` per collection (`PreviewCollection`, `SidecarCollection`,
  `FileListPreviewCollection`), so the grid AND the large-viewer filmstrip
  (same collection, fixed 144 px) share it for free.
- `AsyncImageLoader.request(..., on_cache=…)` — `_deliver` stores the finished
  pixmap **before** the target-widget-alive check, so a result still lands in
  the cache even when the card that asked for it was torn down by the rebuild
  in flight. `on_cache` captures the cache, never a widget.
- Effect (confirmed live): a colour set is immediate; the second and later
  filter rebuilds re-attach pixmaps with no decode. Widget churn is still there
  (steps 3–4), so `h` is not instant yet, but the decode half is gone.

**Not done:** persistent on-disk cache — step 2C.

---

## Step 2C — persistent on-disk thumbnail cache   (BUILT — branch, pending test)

Written as `<stem>@<width>.jpg` **inside the existing per-folder preview cache
dir** (`utils/preview_cache.cache_dir_for`, AppData, keyed by a hash of the
source folder). Inherits that cache's 90-day LRU + "folder gone → evicted"
hygiene for free, and shows under **Preview thumbnails** in Manage Caches. No
new infrastructure.

- `gui/thumb_cache.py`: `disk_thumb_path` / `load_disk_thumb` (touches the
  shared LRU stamp on a hit) / `save_disk_thumb` / `drop_disk_thumbs`.
  `cached_thumb_request(..., disk_dir=, stem=)` checks RAM → disk → decode; a
  fresh decode is written to **both** RAM and disk.
- The three culling collections pass `disk_dir` + `stem`. Invalidation is one
  method — `_drop_thumb_caches(key)` — that clears the RAM entry AND
  `drop_disk_thumbs` for both the display-source and the RAW stem; called from
  `set_crop` / `create_blog_jpg` / `_bake_heic_crop` / `refresh`.
- `utils/preview_cache.drop_for_files` extended to also unlink `<stem>@*.jpg`,
  so a blue/purple-delete takes the thumbnails with it.

Effect: a close + reopen of a day is now fast — the first pass loads small JPGs
from disk instead of re-decoding + smooth-scaling the big previews.

---

## Step 3 — filter = show/hide, build cards once (lazy)   (BUILT — branch `sv_culling_grid_showhide`)

Decisions (user, 2026-09-02): **Step 3 only**, defer Step 4 until we see if this
is enough. **Lazy / incremental** card build — never destroy — so Step 1's fast
"open into the ~20 % subset" survives. **Culling `ImagePreviewDialog` only** —
the Trip/Master/Album species grid (`rating_editor`) keeps the rebuild path
(it shows one species at a time, ~35 cards).

As built (`gui/image_viewer.py`, all in the dialog — `PictureCard` untouched):

- `_cards_by_key` is now the **persistent** store of every card built this
  session; `_cards` is the currently-**visible**, ordered subset that all the
  nav / selection / keyboard code already keys off.
- Two paths:
  - `_rebuild_grid_from_keys` — **HARD**: destroy every card, build from
    scratch. Used on open, on a column-count / card-width change, on resize
    settle, and on an album master change.
  - `_show_filtered` — **SOFT** (what `_apply_filters` now calls): sort the
    visible keys (`_collection.rebuild`), lazily build any **not-yet-seen**
    card (`_build_missing_batch`, batched + cancellable, hidden and out of the
    layout), then `_reflow_visible` — detach every card, re-add exactly the
    visible ones in order, `hide()` the rest. No widget destruction, no
    thumbnail re-decode (the RAM cache from 2B serves them).
- `_drop_card_live` (double-click-a-swatch → leave the filter) now **hides**
  the card instead of destroying it, so re-widening shows it again instantly.
- `_grid_busy` / `_pending_filter` guard against a filter change landing
  mid-build (coalesced to the final state).
- Deleted pictures: `_reflow_visible` prunes cards whose key left
  `_base_keys()` and `deleteLater()`s them.

Expected: the first widen to a bigger set in a session pays a one-time lazy
build of the delta (with the wait dialog); every filter cycle after that —
`g` / `h` / grey / find / JPG — is pure re-flow (target ≈ 1 s on the
1194-pic day, vs ~40 s).

Cost held for the session: up to a whole day of cards (~35 k widgets for 1194)
+ the capped pixmap cache. Accepted for current day sizes; Step 4 is the answer
for multi-thousand-picture days.

**Watch in testing:** the re-flow does ~N `removeWidget` + ~visible `addWidget`
on the `QGridLayout` per filter change — if that itself is slow on a big day,
that is the signal to move to Step 4 (drop `QGridLayout` for manual
positioning) rather than push this further.

---

## Step 4 — viewport virtualization   (own branch — the real fix)

Only ever build ~40 card widgets; recycle them from a pool as the user scrolls;
the filter just swaps the backing key list. Open, filter, and scroll all become
instant and memory stays flat whether the day is 300 or 30,000 pictures.

Needs: a manual-layout scroll area (drop the giant `QGridLayout`), a card
`rebind(item)` method (swap data without rebuilding sub-widgets), a small widget
pool. Several days of work + careful testing across every viewer that shares
`PictureCard` (Trip / Master / Album species viewers, culling preview, large
viewer filmstrip). This is the answer that truly scales; aligns with the
"efficient, scalable, big-master" direction.

---

## Step 5 — cheaper card: one custom-painted widget   (only if needed)

Replace the ~30-widget framed card with a single `QWidget` whose `paintEvent`
draws frame + thumb + text + stars + swatches, and `mousePressEvent` maps clicks
to regions. Removes ~30 widget constructions + ~15 stylesheet parses per card
(5–10× on build/destroy, stacks with 3/4). Big regression surface — every viewer
uses `PictureCard`. Do only if 3 + 4 are not enough.

---

## Step 6 — lazy controls   (scoped version of 5)

`StarWidget` (6 widgets) + `ColorSelector` (7 swatches) are ~40 % of the
per-card widget count and carry the heaviest stylesheets. Draw them as flat
glyphs in the card's `paintEvent`; instantiate the real interactive widgets only
when a card is hovered / is the cursor card. Keeps `c`/`s` keys and
double-click-swatch working. Lower risk than step 5.

---

## Step 7 — one shared stylesheet   (cheap, any time)

Every star button / swatch / label calls `setStyleSheet("…")` individually → Qt
parses that CSS per widget. Move to `setObjectName` + one dialog- or app-level
stylesheet with selectors, parsed once. ~15–25 % off build time, low risk,
helps every path. Good to fold into step 3 or 5.

---

## Not worth much on their own

- **Bigger build batches** — `_build_next_batch` yields to the event loop every
  20 cards (~60 round-trips for 1194). Raising to ~150 shaves a little; only
  worth bundling with a bigger change.
- **Parallelism** — decodes already run on a `QThreadPool`; the ceiling is
  GUI-thread widget work, which parallelism can't touch.
