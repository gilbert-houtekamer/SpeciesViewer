# Album re-architecture → date-time master query

**Status:** planned, not started (2026-07-21).
**Goal:** an Album stops being a folder of per-day CSV snapshots and becomes a *saved
query* over the master DB — `fromdate ≤ DateTimeOriginal ≤ todate`, grouped by day —
exactly like the Master Species Viewer turns a species name into
`pictures_for_species`. One truth (the master), zero copies, the whole
stale-copy bug class gone.

Related memory: `project_album_rearch_datetime`, `project_master_db`,
`project_master_species_viewer`, `project_virttrip_editing`, `project_small_master`.

---

## Decisions locked in

- **Group by `pictures.DateTimeOriginal` (UPN / picture level).** That column holds
  the camera timestamp and is the ONE thing that matters for binning a picture into a
  day. We do NOT filter on the file-level `EXIF:DateTimeOriginal` the way svv2 did, so
  the whole "add XMP sidecars by UPN afterward" dance in svv2 disappears.
- **Publish pipeline: teach svv3/svv4 the query.** Clean end state — no extract step
  anywhere; `svv2ExtractDays` is retired.
- **Transition: parallel, prove, then cut over.** Build the query + a QA harness that
  compares its per-day counts against the current extract for a real album; keep the
  old path working until counts match; switch the live views one at a time.
- **Menus converge on the Master Species Viewer.** The album day viewer's card
  context menu today is crippled (it had to pretend it was "not quite a master"). We
  replace it with the Master Species Viewer's menu (`_master_context_menu` +
  the `col.master_*` collection methods) — full master ops: qualifiers, add files,
  JPG pin/delete, move species, delete picture, per-file delete, with the read-only
  fallback and JPG-protection blocking already built in.
- **Album *authoring* is a BigBoy-only capability.** SmallBoy holds a partial extract
  of the master (`master.readonly_locked = yes`), so it is not a place to design new
  albums — the date range would query an incomplete master. Creating / designing an
  album is blocked when `readonly_locked == yes`. (Viewing an already-published album
  is a separate question, handled at the end.)

---

## The one bridge that makes everything else small

Every consumer speaks one of two dialects:

- **GUI views** want a `PreviewCollection` / file-path list (built from `FilePicture`
  → `Files` / `Pictures`).
- **Publish pipeline** wants `project.files_dict` + `project.UniquePictureName_dict`
  plus a `(from_time, last_time)` range.

So the core deliverable is **`MasterDB.pictures_in_daterange(from, to)`** plus **two
thin adapters** that shape its rows into those two dialects. After that, each view is
a few-line swap.

---

## Steps (each independently shippable + verifiable)

### S0 — The query + a QA harness  *(prove it; ship nothing user-facing)* ✅ DONE
- [x] Add `MasterDB.pictures_in_daterange(from_date, to_date)` →
      `{ "YYYY-MM-DD": [picture_dict, …] }`, ordered, keyed off
      `pictures.DateTimeOriginal`. Picture dicts shaped like `pictures_for_species`.
- [x] Verified `DateTimeOriginal` is fixed-width `YYYY:MM:DD HH:MM:SS`, so
      `substr(...,1,10)` gives a lexicographically-sortable date; plain string
      `BETWEEN` is chronological. (Only 42/73955 pictures have an empty timestamp.)
- [x] **Ghost filter** = exclude `TargetPath = ""` (unfiled pictures, only 9 in the
      whole master). Cheaper than svv2's per-file stat; on-disk existence of survivors
      is a display concern the viewer already handles.
- [x] QA harness (`scratchpad/qa_daterange.py`) compares the query against a real
      album's extract and classifies each diff as STALE (gone from master) vs BUG.
      **helgoland result: clean** — exact match on 3 of 4 days; the 1 diff
      (`2014 helgoland dsc_4491`) is a picture removed from the master after
      extraction (0 rows in the current master CSVs) — i.e. the stale-copy bug this
      re-arch kills, correctly absent from the live query.

### S1 — Overview reads the query ✅ CODE DONE (awaiting live GUI verify)
- [x] `populate_virttrip_overview` now sources the day list + counts from
      `_day_counts_from_query` (the master date-range query) as PRIMARY; the legacy
      `review_root.iterdir()` + `_count_pictures_from_csv` remains as a FALLBACK only
      when no master is configured (the "parallel" safety net).
- [x] No-GPS collapsed to one field test: `pictures.GPSPosition` empty. The
      CSV-then-`upns_with_gps` reconciliation is gone from the query path.
- [x] Blog-state (from ini) and web-built detection unchanged.
- [x] Unit-verified vs helgoland: query counts `(92,14,0)/(126,26,0)/(175,20,0)/(123,14,0)`
      match the CSV path on every day except the stale-ghost day (query correctly
      shows 92 pics / 0 no-GPS vs the extract's stale 93 / 1).
- [ ] **Live GUI verify** (user, F5): open the Active Album tab, confirm the columns
      read sensibly and 2014-06-23 now shows Pictures 92 + GPS ✅ (was 93 + 📍1).

### S2a — Day viewer reads the query ✅ CODE DONE (awaiting live GUI verify)
- [x] `_open_day_viewer` now builds `file_list` / `crop_hints` / `name_hints` from a
      one-day master query (`_day_data_from_master`) as PRIMARY; the CSV builders
      remain as a FALLBACK when no master is configured.
- [x] The hint builders were factored into row-based cores
      (`_file_list_from_files` / `_crop_hints_from_files` / `_name_hints_from`) shared
      by both the query and CSV paths — the CSV `_build_*` now just read rows and
      delegate. Master-DB open consolidated into `_open_master_db()` (reused by the
      overview too).
- [x] Unit-verified vs helgoland 2014-06-25: all-files (370), jpg-files (20), crop
      hints, and name hints are IDENTICAL between the query and CSV paths.
- [ ] **Live GUI verify** (user, F5): open a day from the Active Album, confirm the
      cards, JPEG filtering, "#" crop markers and captions render as before.

### S2b — Converge the card context menu onto the Master Species Viewer (NEXT)
Bigger, provider-level refactor (kept separate on purpose). The `master_*` methods +
`_master_context_menu` live on the **rating_provider** collection (Master Species
Viewer + Rating Editor); the album day viewer uses a **sidecar_provider** collection
with only a partial master surface. Convergence means giving the day viewer the same
master ops (qualifiers / add / pin / delete / move) rather than its current crippled
menu — a deliberate step of its own.
- [ ] Decide: extend the sidecar_provider collection with the full `master_*` surface,
      OR host the album day viewer on the rating_provider collection.
- [ ] Wire `_master_context_menu` (or a shared copy) into the day viewer's cards.
- [ ] Verify each master op works from an album.

### S3 — Map reads the query
- [ ] Replace `_collection_from_review_days` (`gui/mainwindow.py:1093`) CSV reads with
      the query → in-memory `FilePicture`.
- [ ] `_overlay_sidecar_gps` likely becomes dead code (it reconciled stale snapshots
      against sidecars; the DB mirror already holds placed GPS) — verify, then delete.
- [ ] Verify whole-trip + single-day map against the live map.

### S4 — Publish pipeline consumes the query  *(retires svv2)*
- [ ] Add `load_day_from_master(project, db, day)` — fills `project.files_dict` +
      `UniquePictureName_dict` and returns `(from_time, last_time)` — a drop-in for the
      `read_file` / `read_pictures` pair at `svv3WriteBlog.py:513-514`.
- [ ] svv3 day discovery moves from `review_root.iterdir()` to the date range; the
      per-day loop is otherwise untouched.
- [ ] Repeat the identical swap in `svv4BuildWebsite`.
- [ ] Retire `svv2ExtractDays` (menu + flow).
- [ ] Verify a full blog write + website build for a real album, diffed against the
      last published output.

### S5 — Album authoring gated to BigBoy
- [ ] Album Create / design UI (date range picker, "Find Trips → Create") requires the
      full master: block when `master.readonly_locked == yes`, with a plain-language
      note ("This machine holds a partial master copy — design albums on the main
      machine").
- [ ] Confirm the gate does NOT break simply *viewing* an already-created album on
      SmallBoy if that path is kept (open question below).

### S6 — Retire the extract + migrate gracefully; sweep stragglers
- [ ] Remove the "Re-extract this day" context action (moot).
- [ ] **Leave existing `review/` folders on disk** — the new code never reads them;
      don't delete user data. (Optional later: a cleanup that trashes them.)
- [ ] Grep the remaining `review/` readers (`virttrip_process.py`, blog editor,
      cover-from-grid, Delete / Manage-publication) — confirm each is query-backed or
      identity-only before declaring the class dead.

---

## Open questions to resolve as we go

- **SmallBoy viewing.** Authoring is BigBoy-only (locked in). Should an
  already-published album be *viewable* on SmallBoy at all, given its master is a
  partial extract? If yes, the day query must tolerate a subset master (missing
  pictures simply absent). If no, hide albums entirely when `readonly_locked == yes`.
- **`DateTimeOriginal` completeness.** Any master picture with an empty
  `DateTimeOriginal` would fall out of every date range. S0's compare script flags how
  many, if any; decide whether to backfill or accept.
- **Identity metadata untouched.** `virttrip.ini` (name, dates, slug, title, cover,
  name-profile) stays exactly as-is — only the picture LIST becomes a query.
