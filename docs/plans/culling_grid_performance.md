# SpeciesViewer — culling grid performance (1000+ picture days)

A stepped plan for making the Species Image Preview / culling grid fast on big
days, on a laptop, without new hardware. Written 2026-08-31 after live timings on
a Brazil day (1194 pictures).

---

## STATUS — DONE 2026-09-07 (step 4 shipped)

All of steps 1–3, 2C, 8, 8b, step 6 Tier 1, and **step 4 (viewport
virtualization)** are merged to `SpeciesViewer03` (latest `24b3000`). The
culling grid now builds a fixed ~50-card pool and recycles it via
`PictureCard.rebind()` — open / filter / scroll / delete / resize are O(pool),
not O(day). User, after hands-on testing: *"significant improvement"*.

Reverted along the way: step 6 Tier 2a (one rich-text `QLabel` for the meta
block) — a `QTextDocument` re-lays out on every `sizeHint()` and `QGridLayout`
asks constantly, so it made a full 2155-pic build *slower* (`card build`
6 → 31 ms/card). The plain multi-`QLabel` block is cheap; Tier 1's
`_ControlStrip` (10 buttons → 1 painted widget) was the real win there.

Nothing open. Possible future polish (not planned): the pool re-binds every
slot on a first-visible-row change — a ring that re-binds only `ncols` per row
is the optimisation if fast scroll ever feels heavy; `_META_H` = 80 px is
eyeball-tuned; step 6 Tier 2b (paint the corner badges) and the star
hover-preview were dropped as not worth the risk once step 4 landed.

### Original bottom line (2026-09-02, kept for context)

- **Filter-cycling within a session** (`g` / `h` / grey / find) — fast (step 3).
- **First open / reopen** of a ~900-pic day was **~14 s** — the raw cost of
  building **~30 Qt objects per card**, ~11 ms/card. Step 4 removes this
  entirely by never building more than the pool.
- 2C (on-disk thumbs) works (`0 decoded` warm); cold OS-file-cache can still
  spike disk-load to ~6 s on the first open of a day per boot.
- Didn't help (harmless, kept): build batch 20 → 100; hoisting
  `StarWidget` / `ColorSelector` stylesheets to one parse.

---

## RESUMING 2026-09-05 — agreed direction

Discussed the object budget (see "The measured problem" + step 6 below). Decision:

1. **Step 8 first** — viewport thumbnail loading (only request the thumbnails
   for cards in/near the viewport, ~100 always warm). Simplest, and it's the
   biggest win *for culling specifically* — a 500-pic day paints its structure
   at once instead of firing 500 decode requests. In-repo precedent already
   exists: `FilmstripWidget._load_visible` in `gui/large_image_view.py`.
2. **Then step 6, Tier 1 + Tier 2** — paint the stars/swatches + one shared
   floating editor that re-parents onto the focused card (Tier 1), then collapse
   the text labels into one rich-text `QLabel` and paint the corner badges
   (Tier 2). These help *every* `PictureCard` viewer, not just culling.

Not doing now: step 4 (full widget virtualization) — step 6 lowers the per-card
cost enough that building a whole day stays affordable at today's day sizes;
revisit step 4 only when a day genuinely runs into the thousands.

### Step 8 — SHIPPED `c6a30eb` (2026-09-05), live-tested OK

`defer_thumb=True` on `PictureCard` + `request_thumb_if_needed()` /
`release_thumb()`; `ImagePreviewDialog._load_visible_thumbs()` drives it from
the scrollbar (debounced 90 ms) and after every build / reflow / resize.
`_THUMB_WARM = 100` kept warm. **Plus progressive reveal:** the first batch
(100) builds behind the modal wait dialog, `_reveal_partial()` shows the grid
and hands back control, the rest build **hidden + out of the layout** (batches
of 30), and `_finalize_load` places the backlog in one frozen pass. First cut
(reveal by adding to a *live* `QGridLayout` per batch) was **quadratic** —
re-flow + badge reposition for every card every batch — and made a ~2200-pic
day *slower* (30 s → 45 s); the hidden-backlog version lands at **~15 s to a
usable grid, first screenful live in ~1.5 s**. `_grid_busy` stays True through
the background build so a filter keystroke is still deferred to `_finalize_load`.

### Step 6 — Tier 1 KEPT; Tier 2a REVERTED `40021e6` (2026-09-06)

Branch `sv_cheaper_card` (merged `d56569e`, deleted). Scoped to a **framed card
with `show_status`** (culling / preview grid only).

- **Tier 1 — KEPT** (`e14ded5`): `_ControlStrip(QWidget)` paints `★★★☆☆` +
  five colour dots and hit-tests clicks (star → rating, dot → toggle, dbl-click
  dot → `color_double_clicked`). Replaces `StarWidget` + `ColorSelector`
  (−13 widgets, −2 layouts, −17 connections/card). `_stars` / `_color_sel`
  alias the one strip so `refresh()` / `update_*_display` are unchanged.
  Missing: the star hover-preview.
- **Tier 2a — REVERTED** (`4779750` in, `40021e6` out). One rich-text `QLabel`
  for the whole meta block **made the full build much slower, not faster**:
  a `QLabel` in rich-text mode builds a `QTextDocument` and re-lays it out on
  `setText` *and* on every `sizeHint()` — and `QGridLayout` queries sizeHint
  a lot. Measured on a 2155-pic day: `card build` **~6 ms → ~31 ms/card**
  (84 s total, was ~30 s), and a pure re-flow of 2401 cards went **~1 s → ~10 s**.
  The plain multi-`QLabel` meta block (cheap sizeHint) was never the problem —
  it's restored.

**Lesson:** per-widget shaving on the *text* block is not the lever. The plain
labels are cheap; the expensive things were the 10 control buttons (Tier 1
fixed that) and — on a full build — the `QGridLayout` re-flow itself
(`other` = 17 s for 2155 cards). That re-flow is **step 4** (drop the giant
`QGridLayout`), which the plan always called the real fix.

**Tier 2b (paint the corner badges) — on hold.** Would shave ~4 more labels +
the `reposition()` cost, but leaves the `QGridLayout` re-flow untouched and
carries the rich-text-`QLabel`-shaped risk (custom paint + hit-test the
clickable attention badge). Only worth it if step 4 is off the table.

| Step | What | State |
|------|------|-------|
| **1** | Open into a "Start with" subset + counts bar | **SHIPPED** |
| **2A** | Grid width + `grid_columns` global + truncation fix + stable row height + width snapped to 16 px | **SHIPPED** |
| **2B** | In-RAM scaled-thumbnail cache, grid + filmstrip, crop invalidation | **SHIPPED** |
| **2C** | Persistent on-disk thumbnails in the per-folder preview cache | **SHIPPED**, verified hitting (`X disk, 0 decoded`) |
| **3** | Filter = show/hide, build cards once (lazy) | **SHIPPED** — filter-cycling fast; first open/widen still builds widgets |
| — | Thumbnail aspect sampled from the day's own images (not fixed 3:2) | **SHIPPED** |
| — | Preview dialog: taskbar button + `⤢` reset-size + reject a saved geometry small in both dims | **SHIPPED** |
| — | Look & Feel: "Culling grid" section (columns / fill % / Start-with default) | **SHIPPED** |
| — | Build-time instrumentation in the log (`built in N s … card build / layout / other`) | **SHIPPED** — leave it, it's cheap and useful |
| **4** | Viewport virtualization (fixed ~50-card pool, `rebind()`, drop `QGridLayout` for manual layout) | **SHIPPED `24b3000`** (branch `sv_grid_virtualize` merged, deleted). User hands-on: "significant improvement". Open / filter / delete / resize now O(pool), any day size. ~480 lines deleted incl. all of step 8/8b. |
| 5 | Cheaper card (one custom-painted widget) | not started — only if 4 + 6-T1 fall short |
| **6** | Cheaper card: T1 painted `_ControlStrip` (stars+dots) — **KEPT**; T2a rich-text meta label — **REVERTED** (slower); T2b paint corner badges — **on hold** | T1 shipped `e14ded5`; T2a in `4779750` / out `40021e6` |
| 7 | Hoist per-widget stylesheets into one shared sheet | **DONE for StarWidget/ColorSelector (`f638ea1`) — no measurable gain, don't bother with the rest** |
| **8** | Viewport thumbnail loading + progressive grid reveal | **SHIPPED `c6a30eb`** — ~2200-pic day ~30 s → ~15 s to a usable grid, first screenful ~1.5 s |
| **8b** | Progressive reveal for the `_build_missing_batch` filter-widen path too | **SHIPPED `3c7f702`** — 3-card no-GPS view widened to All: ~40 s blocked → ~2 s to a usable grid |
| — | Persistent process-level `ThumbCache` (module singleton, keep byte budget) | not started — cheap; immunises 2nd+ open in a session from cold OS-file-cache |
| — | Preview-cache size cap (% of free disk, LRU to fit) | requested 2026-09-02 — `utils/preview_cache` hygiene, separate follow-up |

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

## Step 4 — viewport virtualization   (SHIPPED `24b3000`, 2026-09-07 — the real fix)

Branch `sv_grid_virtualize` merged to `SpeciesViewer03` and deleted after the
user's hands-on testing ("significant improvement"). Built in 2 slices +
1 fix: `009980d` rebind() infra, `d66f7e1` the layout swap, `397c4ad` the
resize aspect re-fit. As-built matches the plan below; the notes there stand as
the reference for how it works. Follow-ups if ever needed: the pool re-binds
*all* slots on a first-visible-row change (a ring that only re-binds `ncols`
per row is the optimisation if scroll ever feels heavy); `_META_H` = 80 px is
eyeball-tuned; species viewers still use `QGridLayout` (out of scope, fine).

Only ever build ~40–60 card widgets; recycle them from a pool as the user
scrolls; a filter just swaps the backing key list. Open, filter, scroll and
delete all become instant and RAM stays flat whether the day is 300 or 30,000
pictures.

### Scope

**Culling `ImagePreviewDialog` (`gui/image_viewer.py`) ONLY** — same call as
step 3. The Trip/Master/Album species grid in `rating_editor.py` keeps its
`QGridLayout` (one species, ~35 cards — no benefit, big regression surface).
`PictureCard` gains a `rebind(item)` method used only by the culling dialog;
`_FramedPhoto` / `_ControlStrip` unchanged.

### Model (replaces `_cards` / `_cards_by_key` / `_placement` / `grid_layout`)

- `self._order: list[str]` — the visible keys in display order. The **single
  source of truth**; nav / selection / keyboard key off it.
- `self._sel_keys: set[str]`, `self._cursor_key: str | None` — by key, not widget.
- `self._pool: list[PictureCard]` — a fixed set, `(visible_rows + ~3 buffer) ×
  columns`, all children of `grid_widget`, built once. `grid_widget` is a plain
  `QWidget` with **no layout**, `setFixedSize(cols·cw + gaps, rows·ch + gaps)`
  where `rows = ceil(len(order)/cols)` — its size drives the scrollbar.
- `_relayout()` — the heart. From the scrollbar value compute the first visible
  row → first index; for each pool card `i`, target `idx = first_idx + i`; if in
  range → `card.rebind(order[idx])`, `card.move(col·(cw+gap), row·(ch+gap))`,
  `card.show()`, `card.set_selected(key in _sel_keys)`,
  `card.set_cursor_focus(key == _cursor_key)`; else `card.hide()`. Called on
  scroll (debounced ~30 ms), filter change, resize-settle, delete, column change.
- `PictureCard.rebind(item)` — swap `_item` / `upn_key`, clear `_selected` /
  `_cursor`, run the existing refresh helpers (`_populate_name_lines`,
  `_update_thumb_style`, `_ControlStrip.set_rating/set_color`,
  `set_disabled_colors`, corner badges, `update_rate_display`,
  `_refresh_framed_status`), `release_thumb()` then request via the viewport
  path. No widget creation.
- Keyboard nav / `_cull_set_cursor(key)` → set `_cursor_key`, scroll so its row
  is visible (`scrollbar.setValue`), `_relayout`. `ensureWidgetVisible` → "scroll
  to index" math. Delete-live → drop key from `_order`, `_relayout`.

### What goes away

`_rebuild_grid_from_keys`, `_build_next_batch`, `_finalize_load`,
`_show_filtered`, `_build_missing_batch`, `_reflow_visible`, `_drop_card_live`'s
cell-shuffle, `_grid_placement`, `_grid_busy` / `_pending_filter`,
`_deferred_cards`, **and the whole step 8 / 8b progressive-reveal machinery**
(`_reveal_partial`, `_revealed`, `_REVEAL_N`) — virtualization makes open
instant, so there is nothing to reveal progressively. `_apply_filters` just sets
`_order` and calls `_relayout`. `_load_visible_thumbs` collapses to "request the
bound cards' thumbs" (they're all near the viewport by construction).

### Two decisions (need a call before building)

1. **Uniform card height.** Manual positioning needs every card the same
   height. The only variable part is the 1–3 species-name lines. Options:
   (a) **reserve a fixed 3-line meta area** — tidy aligned rows, a little extra
   whitespace on single-name cards; (b) measure the tallest of the first ~50
   cards and clip anything taller. → lean (a).
2. **Delete the step 8 / 8b code** (recommended — ~80 lines of now-pointless
   complexity) vs. leave it dormant behind a flag.

### Build order

1. `PictureCard.rebind(item)` + an isolated test (rebind a card through several
   items, assert every field updates, no stale state). No behaviour change.
2. The swap: pool + `_relayout` + scroll/resize/keyboard/selection/delete wiring,
   replacing the methods listed above. One big commit on the branch, heavy
   manual testing (F5): open, scroll fast, filter cycle, `g`/`h`, arrow-nav to
   off-screen rows, range-select `[`…`]`, double-click-swatch drop, delete,
   column change, resize, large-view open/close repaint, "Start with" subsets.
3. Delete the dead step 8/8b code; update this doc.

### Risks

Cursor/scroll off-by-one (cards misplaced or cursor lost on nav); `rebind`
missing a field → stale data on a recycled card; selection across filter
changes; the large-view "occluded card repaint" contract (`_notify_large_view`
must `_relayout`, not poke one widget); the uniform-height visual change.

---

## Step 5 — cheaper card: one custom-painted widget   (only if needed)

Replace the ~30-widget framed card with a single `QWidget` whose `paintEvent`
draws frame + thumb + text + stars + swatches, and `mousePressEvent` maps clicks
to regions. Removes ~30 widget constructions + ~15 stylesheet parses per card
(5–10× on build/destroy, stacks with 3/4). Big regression surface — every viewer
uses `PictureCard`. Do only if 3 + 4 are not enough.

---

## Step 6 — cheaper card, tiered   (scoped version of 5)   (AGREED — do after step 8)

### Why it's safe: keyboard editing never touches the control widgets

`handle_cull_key` (`gui/viewer_keys.py`) writes rating/colour **through the
`PictureItem`** and then calls `refresh_key(key)` so the card repaints. It never
reads or pokes a `StarWidget`/`ColorSelector`. The star + swatch *widgets* exist
almost entirely for **mouse** editing — and you only mouse-edit the card you are
pointing at, one at a time. So ~99 % of on-screen cards carry ~14 interactive
widgets they never use.

### Per-card object budget (framed culling card, `show_status=True`)

| Group | Objects |
|---|---|
| card frame + `_pframe` + `meta` + `controls` wrappers | 4 widgets |
| `_thumb` (`_FramedPhoto`) | 1 widget |
| corner badges (rate / attn / format / colour-dot…) | ~4–5 QLabels, hand-positioned on every resize |
| name lines + location + filename + crop mark | ~5–6 QLabels |
| `StarWidget` | 1 widget + 1 layout + **5 buttons** + 5 event-filters + 5 connections |
| `ColorSelector` | 1 widget + 1 layout + **5 swatches** + 10 connections |
| layouts | ~8 |
| signal connections | ~25 |

≈ **25 widgets + 8 layouts + ~25 connections**, all torn down + rebuilt on a
hard build. `StarWidget` + `ColorSelector` = **~14 widgets (10 buttons)** — the
biggest single chunk and the heaviest to set up.

### Tier 1 — paint the controls, hydrate the focused card

- Draw `★★★☆☆` and the five colour dots as flat glyphs in the card's
  `paintEvent` (read straight off the `PictureItem`; repaint on `refresh_key`).
- Keep **one** shared real `StarWidget` + `ColorSelector` for the whole grid,
  re-parented / repositioned onto whichever card is hovered or is the keyboard
  cursor — not one instance per card, and not created-per-hover.
- Double-click-swatch ("set + drop from filter") becomes a hit-tested region on
  the painted dots, or is only live on the hydrated focused card.
- Keyboard paths (`q w e r t`, `1`–`5`, `Ctrl+1`–`5`, `0`, `s`, `c`) unchanged.
- **Effect:** card ≈ 33 → **~19 objects**; ~40 % off first-open build; bounded to
  `PictureCard`; near-zero behaviour change.

### Tier 2 — collapse the text + paint the badges

- Name lines + location + filename + crop mark → **one rich-text `QLabel`**
  (`<span style=…>` runs + `<br>`). The 📝 and ⌗ links stay as `<a href>`
  (`QLabel.linkActivated` already carries them).
- Paint the corner badges in the thumbnail's `paintEvent` instead of positioned
  child `QLabel`s → drops ~5 widgets + the reposition-on-resize work.
- HTML/CSS helps **here only** — it removes label objects + layouts + style
  parses. It does nothing for the stars/swatches (interaction targets, not
  text), and a rich-text `QLabel` still builds an internal `QTextDocument`, so
  treat it as "fewer objects, same per-object cost" — which is the point, since
  object *count* is the bottleneck.
- **Effect:** card ≈ 19 → **~7 objects**.

Rough first-open, ~900-pic day: ~14 s today → ~7–9 s (Tier 1) → ~4–5 s (Tier 2).
Full custom paint (step 5) would reach ~1–2 objects/card but reimplements wrap /
hover / tooltips / a11y by hand across every viewer — only if 6 falls short.

### Regression surface

`PictureCard` is shared by the Trip / Master / Album species viewers, the
culling preview, and (indirectly) the large-viewer filmstrip. Gate the painted
path on `framed` (+ maybe `show_status`) so the legacy stacked layout is
untouched, and smoke-test each viewer: hover-to-edit, keyboard edit, selection
ring, cursor ring, tooltips, the 📝 / ⌗ links, frozen-day read-only.

---

## Step 7 — one shared stylesheet   (cheap, any time)

Every star button / swatch / label calls `setStyleSheet("…")` individually → Qt
parses that CSS per widget. Move to `setObjectName` + one dialog- or app-level
stylesheet with selectors, parsed once. ~15–25 % off build time, low risk,
helps every path. Good to fold into step 3 or 5.

---

## Step 8 — viewport thumbnail loading   (AGREED — do first)

**Idea (user, 2026-09-05):** building the cards is one cost; firing a thumbnail
request from every card is another. On a 500-pic day the second one is what
makes "open" feel slow. Decouple them: a card that isn't near the viewport gets
a placeholder (grey box + filename), and asks for its thumbnail only when it
scrolls into range.

**Precedent already in the repo:** `FilmstripWidget._load_visible`
(`gui/large_image_view.py`) — from the vertical/horizontal scrollbar `value()` +
`viewport()` size + a fixed item stride, compute the visible index range, pad by
`_PREFETCH`, and call `request_load()` on those cells only. Cells carry a
`_requested` flag so repeated calls are idempotent. The grid is the 2-D version:
`row_stride = card_h + gap`, `first_row = bar.value() // row_stride`,
`first_idx = first_row * columns`, pad by a row or two each side.

**What it needs in `ImagePreviewDialog` / `PictureCard`:**
- The grid already has a **fixed row height** (step 2A) and a **global column
  count** (`grid_columns`), so the row-stride math is exact — no per-card
  measuring.
- `PictureCard`: split "build the widget" from "request the thumbnail". A new
  `request_thumb_if_needed()` (idempotent) and, optionally, `release_thumb()`
  that drops the pixmap back to the `ThumbCache` and restores the placeholder.
- Dialog: connect `scrollArea.verticalScrollBar().valueChanged` (debounced, like
  `_on_resize_settled`) → `_load_visible_thumbs()`; also call it once after a
  hard build, a soft re-flow (`_show_filtered`), and a resize settle.
- **~100 always warm:** never release the first N cards of the current visible
  key list (covers the top of the grid + a resumed cull's whole "Unrated"
  subset when it's small), and keep a generous prefetch band (± ~2 rows) so
  normal-speed scrolling never shows a placeholder. Fast flings may flash grey
  briefly — the on-disk cache (2C) makes the catch-up quick.
- Interaction with step 3's lazy card build: unchanged. Cards are still built
  once and re-flowed; this only gates the *thumbnail request* per card.
- Interaction with the RAM/disk caches (2B/2C): unchanged — this rides on top,
  it just changes *when* `request_thumbnail` is called and lets far cards return
  their pixmap to the byte-budgeted cache so a huge day's grid stays flat in RAM.

**Effect:** first open paints the grid structure immediately; the visible ~40
thumbnails stream in; the rest load on scroll. Biggest *felt* speed-up for
culling, and it caps grid RAM regardless of day size. Independently shippable
without step 4's widget recycling.

**Watch in testing:** placeholder → image swap must not shift row height (fixed
thumb height from step 2A already handles this); the filmstrip in the large
viewer already does its own lazy load, so no conflict; check the "Start with:
Unrated" open still shows its (small) subset fully loaded with no grey.

---

## Not worth much on their own

- **Bigger build batches** — `_build_next_batch` yields to the event loop every
  20 cards (~60 round-trips for 1194). Raising to ~150 shaves a little; only
  worth bundling with a bigger change.
- **Parallelism** — decodes already run on a `QThreadPool`; the ceiling is
  GUI-thread widget work, which parallelism can't touch.
