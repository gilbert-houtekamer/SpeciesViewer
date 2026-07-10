# SpeciesViewer — Project Backlog

Updated: 2026-07-09 · Branch: ClaudeSpecies · (machine-readable mirror of `SpeciesViewer_Backlog.html`)

Legend: **OPEN** to build · **NEW** newly raised · **BUG** defect · **NEEDS TEST** built/awaiting live run · **PARKED** deferred by design · **DONE** shipped.

Since the 16-June edition most of the backlog shipped (WP4 identity decouple, WP8 picture-root, cross-platform path P1, retire→virttrip arc, Working Days, Trip Species Viewer delete rework). Focus below = the short remaining list.

## Active shortlist
1. **Virtual Trips WP7** — retroactive builder + amber/green blog bubbles (ch.2).
2. Smaller: EXIF-cache prune (ch.3), Trip→Master delete live test (ch.4).

Recently shipped: **EXIFtool parallel workers** (ch.11) — per-machine N-worker cold reads; see ch.11 below.

Parked by design: taxonomy/sequence (ch.6), rating-formula/HEIC (ch.7); master data relativization waits for the Mac (ch.10).

---

## 1. SD-card Ingest & Culling — CORE COMPLETE
Feature-complete: M0–M4, sidecar cache, GPS place-names, 100% view, day-picker, batched location writes, recent-species buttons, GPM copy/paste GPS, manual location editor + on-card line, live external-edit refresh. Cancel-during-copy, LR-in-place, tz validated. [[project_sdcard_ingest]] [[project_location_editor]]

**To do**
- NEEDS TEST **GPM southern/western trip** — hemisphere sign on S/W coords.

**Recently done**
- DONE **Municipality → website output** — wired read (sv2 `-Municipality`)→CSV (COLUMN_NAMES/DATA_FILE_FIELDS + PICTURE_FIELDS)→picture row (projectlib UpdatePicture)→website (`getFormattedCityStateCountry`: shown between City and State, collapsed when equal to City). master_db SCHEMA_VERSION→3. Verified live on Greece 2026-05-05 blog. Existing sites need a re-run to backfill (XMP already carries it via geolocate). [[project_location_editor]]

**Parked (by design)**
- PARKED curated sidecar EXIF field set (blanket `-xmp:all<all` works today).
- PARKED eBird regional most-likely-birds list; Nominatim zoom tuning; offline reverse-geocoder.
- PARKED GPM keyboard shortcut. IDEA: LR-style map module (not planned).

## 2. Virtual Trips — CORE + RETIRE COMPLETE
Full arc live incl. retire-to-virttrip (verified Braakman). svv6 make-clickable + per-day blog editor done. [[project_menu_reorg]] WP7 [[project_virttrip_pending]]

**To do**
- OPEN/LARGER **WP7 scenario-2 retroactive builder** — Create asks "from existing trip" vs "retroactive from master dates" + fold Extract into Create; build day-by-day from template. Step code works; the guided *experience* is missing.
- OPEN **amber/green blog-review bubbles** — auto/unreviewed=amber, reviewed=green.
- OPEN **scenario-1 Add-to-Master no-op greying** (retire already ran it).

**Parked**
- FUTURE single-day blog re-upload. PARKED trips/virttrips panel visual alignment.

## 3. Master Database, Editing & Viewer
Phases 1–3 shipped+live. Trip Species Viewer Trip+Master scope works (scope-aware colour-delete incl master, preview, colour filter). **Master Species Viewer SHIPPED** (standalone; Master → Master Species Viewer…): searchable ~2000-species field from the master's own list, full-master card grid, master read-only rules for editing. [[project_master_db]] [[project_species_viewer_delete]]

**Done**
- DONE **Regenerate ONE species' website page** (c1a359d + afda800) — Master Species Viewer "Regenerate page…" button: re-renders + SFTP-uploads just this species' page from CURRENT master data (no re-read, no mutation). Fast — pulls this species' pictures/files from the MasterDB SQLite index (never the 127 MB data_files.csv), renders NL+EN via makeSpeciesWebDirectory, uploads its subtree; done dialog has clickable page URLs. Also fixed: getBirdInfo now recognises bare juv/imm/ad, and regenerate re-derives sex/age/plumage from the subject at render so token-101 captions ("jonge aasgier") + grouping work despite empty master ageintext columns. Caveat: parent index thumbnails need a full build; master ageintext columns still empty until a re-derive/re-commit pass. [[project_species_page_rating]]

**To do**
- OPEN/minor **"Upgrade best JPG" workflow** — sort-by-rating surfaces 800×533 best-picks; render `_DxO.jpg` → master card "Add new files for this picture" re-distils BestJPG. Make the loop smoother + confirm website re-publish picks it up. [[project_dxo_pipeline]]

**Done (recent)**
- DONE **EXIF-cache LRU retention** — the day/trip cache grew by distinct-path accumulation (the mutable bucket is keyed by PATH and updated in place, so NO edit-churn — an earlier note wrongly said path+mtime). Added a `seen` (epoch-day) stamp refreshed on every hit (`partition`) and write (`update`), and `exifcache.prune_expired(cache, ttl_days=90)` called before save on the day/trip scan only (the master cache is rebuilt wholesale by Rescan → left unpruned). LRU not FIFO: active paths never age out (only stopped-touching ones), and the usual "reads become writes" LRU cost is free here since the cache is fully re-serialized every scan anyway. Legacy entries (no `seen`) are migrated with a full-TTL grace, so the upgrade evicts nothing. A wrong eviction only ever costs one re-read (pure optimization, size/mtime/fingerprint-guarded). Measured before: day/trip 1.75 MB/1272 entries/15 ms load (master cache is the separate 205 MB one). [[project_exif_cache_design]]

**Parked (by design)**
- PARKED **delete step 3 (JPG-bearing UPNs)** — blocked by never-trash-a-JPG; whether/how is open.
- PARKED **Undo → Restore-from-trash** — engine exists headless (undo_delete/apply_restore); decided against a lone "Undo" (implies action-log we lack); build trash-restore if discoverability bites. [[feedback_no_misleading_affordances]]
- DEFERRED trip-side SQLite.

## 4. Trip-to-Master Pipeline
"Add Trip Pictures to Master" 3-step + Run-all shipped; on-disk "Delete Local Files Backed Up in Master…" (trip_delete_scan) live. Older TripMasterDashboard+svr7 never run e2e. [[project_svr7_deletelist]] [[project_trip_delete_rethink]]

**Done**
- DONE **retire svr7 / deletelist.csv** (0fb1695) — deletelist.csv removed end-to-end (sv6 no longer writes it, sv7 gate dropped, svr7DeleteFiles deleted); trip_delete_scan is the sole delete authority. Verified live via a full freeze→commit→publish.
- DONE **converge two no-trip ingest paths** (9e2b233) — one context-aware AddToMasterDialog: Master mode editable + remembers last folder (leftover-rescue wording); Days mode path locked to the day, stateless. Menu "(no trip)" → "Add Pictures to Master…". [[project_master_add_dialog]] [[project_working_days]]

## 5. SmallMaster — COMPLETE ✓
Shipped+live on SmallBoy; auto-locks read-only. No open items. [[project_small_master]]

## 6. Taxonomy & Species — PARKED (by design)
- PARKED sequence redesign (eBird TAXON_ORDER float col). [[project_taxonomy_sequence]]
- PARKED auto-detect obscure flag (GBIF family). [[project_obscure_species]]
- PARKED placeholder-species cleanup (`? <name>`). [[project_placeholder_species]]
- PARKED regional most-likely-birds (eBird).

## 7. Rating & Website — PARKED (by design)
- DEFERRED rating-formula tuning (tune last, after viewer settles). [[project_rating_formula_tuning]]
- NEEDS DECISION HEIC/PNG sidecar capture — (a) .xmp beside HEIC / (b) patch .dop / (c) write HEIC. [[project_heic_sidecar_capture]]

## 8. Viewer Convergence — COMPLETE ✓
All closed. [[project_viewer_convergence]]

## 9. Usability & Polish
**Done**
- DONE **Master Species Viewer move + camera/sort** (e24eb77) — mirrors the trip-viewer additions to the Master Species Viewer and adds a **master move operator**. (1) **Camera filter + sort** (Rate / Capture time ↑↓ / Camera), reusing `RatingEditorCollection`; `available_cameras` now also scans the loaded master rows (the master fpr has no trip pictures), scoped to the species in view. (2) **Move to existing species** (right-click): searchable picker over the master's own `species.csv` (any species ever seen — NOT the taxonomy backbone); **blocked for JPG-bearing pictures** (greyed menu line) and gated behind a **confirmation dialog** since it relocates real files. `db.master_mutate.move_picture_to_species` relocates the no-JPG files into the target species folder, rewrites the XMP sidecar keyword, reassigns the DB picture + file rows, and snapshots to `.trash` for undo (`undo_move`). **DB-FIRST**: reassignment applied before any bytes move, so a DB error leaves files untouched. (3) **Counts maintained inline** — `MasterDB._species_move_updates` diffs both taxonomy chains against one pre-move snapshot (−1 source / +1 target, shared ancestors net to zero), handling species emptying/un-emptying → **no recount step needed** (removed the bogus "Master → Recount species" menu ref from the move AND the two delete messages; delete already maintained its counts). (4) **Robustness** — `apply_move_species` self-drops its jsonl intent on a SQLite failure (`_drop_jsonl_seq`) so a bad op can't block every checkpoint; the generic `_replay_sections` handles the move on crash recovery (no new replay op). GOTCHA fixed mid-build: the files table has no `TargetPath` column (it's picture/species-level) — an early version crashed mid-write and stranded a picture (recovered live via the snapshot/manifest). [[project_master_species_viewer]] [[project_master_db]]
- DONE **Species Viewer viewer batch** (c992e6c) — four related Trip Species Viewer / species-panel improvements: (1) `SpeciesInfoPanel` gains as-you-type contains-match suggestions (popup completer over the loaded collection), replacing the click-"Suggest" flow while keeping Validate/GBIF for genuinely new names; (2) **camera filter** in the top bar (by `CameraModel`, scoped to the selected species so only bodies actually present are listed); (3) **sort order** — Rate↓ (default), Capture time ↑/↓ (groups burst duplicates for blue-delete culling), or Camera body; (4) **Move to existing species** (selection-bar button + right-click), a constrained picker limited to species **already recorded on this trip** (new species stays "Set species…"). Move reuses the batched `updateSubjects`, so `XMP:Subject` on every file + the NL/EN/scientific columns all update to the target's canonical names. FIX: `updateSubjects` crashed in Trip scope (merged path-less `Files` → `open('')`); `write_project_files` now skips when it has no filename (symmetric with `write_project_summary`), and both Move/Set-species route persistence through the provider's per-day CSVs. Trip-side move of a JPG-bearing picture is allowed (fix a mislabel everywhere); master side will keep never-move-a-JPG. NEXT: mirror camera+sort to the Master Species Viewer, whose Move targets = species from `species.csv` (seen-everywhere) and blocks JPG-bearing UPNs. [[project_master_species_viewer]] [[project_species_editor]]
- DONE **connectivity/publish reliability** (99547b5) — publish (blog/website/commit) now gates on the SFTP server (`engine.connectivity.publish_reachable`), not a website HTTP ping; specific message replaces the misleading "no internet"; Help → Connection status lamps (publish / GBIF / place names / website, latency + amber-if-slow).
- DONE **species qualifier picker + abbreviations** (9efab26 / e69cbf8) — sex/age/plumage toggles in both species editors; `fuut ♂♀ br.` shown on cards + large viewer.

**To do**
- OPEN/minor **preview cards don't resize live** — fixed at build; add debounced resizeEvent if it grates.

**Parked / watch**
- WATCH translation edge cases (orphan CRC cache; competing HEIC Headline).
- DEFERRED user-configurable cache locations.
- PARKED align Virtual Trips menu's remaining "Rating Editor" entry with the rename.

## 10. Cross-platform (Mac) Prep
Path layer P1 DONE (utils/paths.py host-independent). [[project_portability_audit_todo]] `path_portability_plan.md`

**To do (when Mac arrives)**
- OPEN/until-Mac **master data relativization** — data_files.csv = 202,661 rows, 100% absolute drive-letter (E:/…); one-time reviewed migration (dry-run→apply w/ backup) via relativize_path.
- OPEN/until-Mac **case-sensitivity + defaults** — strip full-path `.lower()` in engine/projectlib.py; DxO/default paths → blank/config.

## 11. Performance & EXIFtool
**Recently done**
- DONE **Set-species multi-write batched** (7b09eba) — committing a species to N selected pictures was ~0.6s/file: **2 exiftool spawns (read+write) + a full data_files.csv + a full data_pictures.csv rewrite PER file** → 23s for 37 (measured), even with no pixel change. New `FilePicture.updateSubjects` writes the whole selection in **2 exiftool calls** (one guard read, one write) + **one** CSV/summary rewrite; identical semantics (species resolve, write-target filter, on-disk guard). ~11.7× on the exiftool half; unit-tested (happy/guard/clear) + user-confirmed live. NOTE the **Edit/Get-location paths were already batched** (51dbf38 `write_location_batch` = one exiftool call per distinct place; reverse-geocode dedups to one Nominatim query per place, throttled ≤1/s — external-API bound like GBIF, not spawns).
- DONE **EXIFtool parallel workers** — cold day reads (~20s Greece) now split the uncached `misses` list across N independent exiftool processes. `engine.exifcache.read_metadata_parallel` (generator, memory-bounded, reuses the caller's primary helper for chunk 0) + `auto_worker_count(n, configured, cap=16, per_worker=24)` = `min(cpu_count()//2, 16, n//24)`. Wired into sv2ReadDirExif (day/trip Read Directory), `exifcache.rescan` (master Rescan, super-chunked for memory), and SD-card XMP sidecar creation (retired the hardcoded `_XMP_WORKERS=4`). Config `[performance] exif_workers = auto` (per-machine; default `auto`, or pin an int). Benchmarked 2026-07-09: speedup peak tracks **physical** cores (BigBoy 32-logical → 16 workers ≈7.4×; SmallBoy 16-logical → 6–8 ≈2.5×); over-splitting small folders regresses (hence the file-count floor). Verified parallel output byte-identical to serial + real sv2 e2e. [[project_exif_parallelism]]

**To do**
- OPEN **GBIF lookups now the Read-Directory bottleneck** (raised 2026-07-09, after the parallel EXIF read: 704 files read in 2.0s, so species classification dominates). GBIF results (`pygbif species.name_backbone` in `engine/speciesclassification.py:getKnownSpecies`) are cached only **in memory for the run** — `sc_dict`/`dutch_dict`/`english_dict` + `names_not_found_list` short-circuit repeats within a session, but a fresh session or a new trip re-hits the network API for every name. Options: (a) **persist a GBIF response cache to disk** (subject-string → resolved backbone, incl. negative/not-found entries), like the EXIF cache, so lookups survive across sessions/machines; and/or (b) **write GBIF-resolved species back into the persisted speciescollection at end of session** so they resolve locally next time. Caching beats parallelizing here (external API, rate limits). Owner idea: "GBIF calls cached, or speciescollection updated at end of a session."
- OPEN **Shared persistent server** — one app-wide `-stay_open` exiftool. NOTE: per-operation stay_open already exists (gui/preview_extract.py; sv2 reads). Idea = widen to one server + worker pool vs per-run launch. [[project_preview_extraction]]
- Ties [[project_perf_optimizations]] (real value = write-contract + big reads, not micro-perf).

**Parked**
- PAUSED general micro-perf sweep (processEvents throttle shipped; rest non-winning/risky). Revisit with a measured bottleneck.

---

## Recently Completed (key commits)
- Master Species Viewer — standalone master-only viewer (searchable species field + full-master card grid, reuses RatingEditorCollection full mode + MasterDB).
- WP4 naming decouple — b4871ae. WP8 picture-root — 8ddd7e1. Cross-platform paths P1 — 0af2d87.
- Trip Species Viewer delete rework — ee5333e, 3aaa390, 454832d.
- Menu reorg WP1–WP6 + retire arc — 6dde8ed, a0f6906, 6ad9893, 6042ae6. Recreate virttrip blogs — db27090.
- Working Days — beee137, 3c679bf. Trip retire (delete) — 2942aad. Manage publication/rename — 3ad4bf9. Days viewer colour — 8431799.
- Location editor + card line — 3b896d4, 178529f, 877ce6a, e127b5d. Crop tool — 2cce37f, e7676ef.
- SD-card grid/picker/GPM — 51dbf38, 4a1ad2f, 25b8ac1, 286965a, e94b16f. Master no-display scan — e6dc5fd. File policy .dop/.dng — ce2e589.
- Earlier (May–June): restructure, decode quality, geolocation, batch-delete, caches, blog extras, translation validation, global EXIF cache, Master SQLite P2/P3, SmallMaster, Mac first-pass.
