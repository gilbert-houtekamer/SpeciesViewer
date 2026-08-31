# SpeciesViewer — culling grid performance (1000+ picture days)

A stepped plan for making the Species Image Preview / culling grid fast on big
days, on a laptop, without new hardware. Written 2026-08-31 after live timings on
a Brazil day (1194 pictures).

---

## STATUS

| Step | What | State |
|------|------|-------|
| **1** | Open into a "Start with" subset + counts bar | **SHIPPED (pending live test)** — `image_viewer.py`, no branch |
| 2 | In-RAM thumbnail (QPixmap) cache | not started — no branch, do on `main`/`SpeciesViewer03` |
| 3 | Filter = show/hide, build cards once | not started — **own branch** (risky rearchitecture) |
| 4 | Viewport virtualization (recycle ~40 cards) | not started — **own branch**, the real scalability answer |
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

## Step 2 — in-RAM thumbnail cache   (next, no branch)

Keep `{upn_key: QPixmap}` on the collection (or a bounded `QPixmapCache`), so a
rebuild re-attaches the existing pixmap instantly instead of re-decoding from
disk. Does **not** fix widget churn, so `h` still rebuilds ~35k widgets — but
`h` drops from ~40 s to ~20–25 s, and first scroll / large-view round-trips get
faster too. Independent, small, useful on its own. Invalidate an entry when the
picture's image signature changes (re-crop, new render, Create JPG).

---

## Step 3 — filter = show/hide, build cards once   (own branch)

Build all cards for the loaded set **once**; on every `g` / `h` / grey / find /
JPG-toggle after that, don't tear down — `card.setVisible(in_filter)` and
re-flow the visible ones in the grid. No widget creation/destruction, no
re-decode (pixmaps stay). `h` → ~1–2 s; other filter keystrokes near-instant.
This is `_drop_card_live`'s reflow trick generalised.

Cost: ~35k widgets + pixmaps resident for the session (rough guess 300–600 MB
for a 1200-pic day — measure). Fine on the laptop for the day sizes today;
pairs with step 1 (the "once" build is the subset, not 1194) and is superseded
by step 4 for very large days. Risky because it changes the shared grid
lifecycle — hence its own branch.

The QGridLayout with explicit (row, col) over ~240 rows is itself a cost;
consider a flow layout or manual positioning as part of this step.

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
