# SpeciesViewer — Project Backlog

Updated: 2026-07-11 · Branch: ClaudeSpecies · (machine-readable mirror of `SpeciesViewer_Backlog.html`)

Legend: **OPEN** to build · **NEW** newly raised · **BUG** defect · **NEEDS TEST** built/awaiting live run · **PARKED** deferred by design · **DONE** shipped.

Since the 16-June edition most of the backlog shipped (WP4 identity decouple, WP8 picture-root, cross-platform path P1, retire→virttrip arc, Working Days, Trip Species Viewer delete rework). Focus below = the short remaining list.

## Active shortlist
1. **NEW Maps for the Day (micro-trip) + Virtual-trip** — extend the Phase-5 trip/day map to those two collection contexts (ch.3).
2. **GPS / data-integrity umbrella** — tracklog geotag, GPS-from-addresses, location-from-GPS (ch.3; dead-link purge shipped as Refresh Master Database).
3. **NEW Compact the viewer toolbar + laptop-fit review** — wide filter/knob row + dialog sizing on small screens (ch.3).
4. **Virtual Trips WP7** — retroactive builder + amber/green blog bubbles (ch.2).
5. Smaller: GBIF lookup disk-cache (ch.11).

Recently shipped: **Refresh Master Database** (dead-link reconcile, ran live: cleared ~43k dead rows / ~15k phantom pictures) + **black-image fixes** (dead-link phantoms + Nikon D90 OtherImage preview) — bc4a060 / 3d0e490 / 4135c48; see ch.3. **EXIFtool parallel workers** (ch.11).

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
- OPEN/NEW **Map for the Day (micro-trip) viewer** — extend the Phase-5 trip/day map to the Days lane / micro-trip so an ad-hoc working day plots + geotags exactly like a trip day. [[project_working_days]] [[project_trip_map]]
- OPEN/NEW **Virtual-trip map** — bring the same map (bubbles→filmstrip→large view, travel path, drag-to-geotag) to a virtual trip's accumulated collection. [[project_trip_map]] [[project_virttrip_pending]]
- OPEN **GPS / data-integrity umbrella** — remaining: tracklog geotag (2a), GPS-from-addresses, location-from-GPS. Purge dangling data_files/pictures now shipped as Refresh Master Database. [[project_gps_data_integrity]]
- OPEN/NEW **Compact the filter/knob toolbar** — the Species Viewer top row (Species · lang · Camera · Sort · Map · Regenerate · JPG-only · colour swatches · Columns · Blue/Purple) is very wide; regroup / overflow-menu / second-row so it fits narrow screens.
- OPEN/NEW **Laptop GUI fit review** — walk every dialog on a laptop screen, confirm each fits (no clipped toolbars / off-screen buttons); ties the toolbar compaction.
- OPEN/NEW **Normalise cached preview size to the OM-1 long edge (~3200px)** (surveyed 2026-07-11) — embedded JPEG per body: **OM-1 II** ORF PreviewImage 3200×2400 ~0.9MB (current body, reference size); **Nikon D7100/D7500/D500** NEF: PreviewImage tiny ~600px, JpgFromRaw full-res 5568–6000px 1.5–2MB, OtherImage mid 1620×1080 ~0.75MB; **D90** only OtherImage 4288×2848 ~9.9MB; **Z8** (web research, not measured) 45MP/8256×5504, JpgFromRaw ~2.2MB (~same as D500, more compressed) + smaller preview/thumb, exact mid dims TBD. Plan: largest-first fix (needed so D90 shows at all) means some bodies cache multi-MB previews → rather than per-body tag rules, **downscale every cached preview to the OM-1 long edge (~3200px)** before caching (keep if smaller). One reference size for all: OM-1 unchanged, D90 9.9→~0.9MB, Nikon full-res 2→~0.9MB, Z8 likewise. Cache size is a secondary benefit; the point is one predictable preview size. Costs one decode+resize on first extract (Pillow). [[project_dead_link_prune]]
- OPEN/NEW **Preview-cache hygiene** — cache only grows (no TTL/LRU, no sweep on source delete) → holds stale previews for culled/committed/retired trip RAWs. (a) 90-day LRU prune like the EXIF cache; (b) sweep a trip's preview subfolder on retire/commit.
- OPEN/minor **"Upgrade best JPG" workflow** — sort-by-rating surfaces 800×533 best-picks; render `_DxO.jpg` → master card "Add new files for this picture" re-distils BestJPG. Make the loop smoother + confirm website re-publish picks it up. CORRECTION (2026-07-11 survey): the small best-JPG is an old small **DxO render**, NOT a RAW-preview limit — D500/D7100/D7500 NEFs embed a full-res JpgFromRaw, so where the RAW is on disk a bigger picture can be pulled straight from it. Still flag tiny best-JPGs (by best-JPG size) as "needs a bigger render". [[project_dxo_pipeline]]

**Done (Refresh Master Database + black images)**
- DONE **Refresh Master Database** (bc4a060, ran live) — File ▸ Admin ▸ Review → Apply reconciles the master CSVs with disk (REMOVE/REPOINT/ADD/SPECIES); Review writes suggested CSVs for Excel + a `display_status.csv` black-image scan; Apply backs up both CSVs then rewrites (metadata only, never image files). Cleared ~43k dead file rows / ~15k phantom pictures. Doc: `docs/refresh_master_database.md` (4135c48). [[project_dead_link_prune]]
- DONE **Black-image fix — Nikon D90** (3d0e490) — old D90 NEFs have no PreviewImage/ThumbnailImage; embedded JPEG is under OtherImage/JpgFromRaw. `gui/preview_extract.py` now tries those largest-first → ~627 D90 pictures no longer render black. Verified live (herons/gulls/vultures). [[project_dead_link_prune]]

**Done (recent)**
- DONE **Maps Phase 1a — native OSM slippy-map** (b90ceda) — first slice of `maps_plan.md`: a dependency-free interactive map (NO QtWebEngine) showing a species' GPS distribution, launched from the Master Species Viewer's "Map…" button. `gui/map_view.py` `TileMapView` (QGraphicsView) renders OSM XYZ tiles in Web-Mercator world-pixel scene coords; async fetch via QNetworkAccessManager + per-machine disk cache (`%LOCALAPPDATA%/SpeciesViewer/map_tiles`), honest User-Agent, visible-viewport-only (OSM policy); hand-drag pan, wheel zoom-to-cursor, Fit, on-map attribution; same-spot pins merged w/ count. `engine/geo.py parse_gps_dms` decodes stored GPSPosition DMS→signed decimal; `MasterDB.gps_points_for_species(sci)`. Per-species point counts small (herons 2–35) → plain pins read fine; clustering deferred to 1c (whole-master/common species). **1b SHIPPED 617c752**: up-to-4-species overlays (red/blue/green/purple) + legend chips (colour · GPS count · × remove) + searchable "Add species…"; pin tooltip = "<count>× <NL>\n<EN>"; fixed a wheel-zoom `world_to_lonlat` OverflowError (clamp x/y + guard the zoom rebuild under `_syncing`). **Occlusion fixed** ccee1cd (per-colour rosette offset so co-located species fan out, not hide). **1c SHIPPED 77ae39a**: zoom-dependent per-species clustering (`_cluster`, greedy ~46 px, recomputed each zoom — split apart on zoom-in) + white count bubbles (grow with count) + click-a-bubble-to-zoom-into-it (click vs pan-drag via <4 px threshold); rosette widened 5→8 px so overlapping bubbles keep legible numbers. **1d/1e SHIPPED e7c6012**: map is now a PEER of the grid — click a bubble → bottom filmstrip of its pictures → click a thumb → large viewer (master editing enabled per policy, edits reflect on the strip); double-click a bubble zooms; per-species lazy full-master collection feeds strip+large-view; "Species Map…" standalone launcher (add 1–4 species). Look: **Terrain (OpenTopoMap relief) default with a 35% white wash** + Standard (OSM); pointer cursor (pointing-hand over bubbles); Master menu items renamed "Species Viewer…"/"Species Map…". **Phase 1 COMPLETE.** Design + GPS-coverage in `maps_plan.md` (uncommitted).
- DONE **Maps Phase 5 — TRIP / DAY map** — a second map mode over the LIVE viewer `PictureCollection` (all species, not per-species). **5a day map**: "🗺 Map" button in the day viewer (`ImagePreviewDialog`) plots the currently-shown pictures with spatial clustering, place+date tooltips, a travel path, and the same bubble→filmstrip→large-view drill-down (`gui/map_view.py TripMapView`, reuses `TileMapView`). **Whole-trip map**: "🗺 Map whole trip" button on the Trip Overview (`MainWindow.open_trip_map`) accumulates EVERY day's `metadata/review/<day>/data_files.csv`+`data_pictures.csv` into one in-memory `FilePicture` → a normal `PreviewCollection` → the same map — so bubbles/filmstrip/large-view span the whole trip (pure CSV reads, no exiftool; the engine loaders already accumulate across days). **Scalability**: new `PictureCollection.gps_cached(key)` seam reads GPS from already-loaded memory (Sidecar `_gps_by_stem`; Preview parses in-memory `Composite:GPSPosition`) so a day/trip plots with ZERO exiftool spawns (vs `gps()` which spawns per file). **Travel path**: time-ordered polyline, simplified location-to-location, with **direction arrows** per leg; on by default for the trip, off for a single day. **Outliers** (departure airport / home shots that stretch the frame): trip-only **Smart Fit** frames the photo-dense core via picture-count-weighted 2/98 percentile (species map keeps exact range framing), plus **right-click a bubble → Hide** (removes it + its route leg; reversible via "Show all"). Live-confirmed on "2026 North Greece" (1846 pics). **5b DRAG-TO-GEOTAG SHIPPED (user-confirmed on Kreta):** a collapsible top **"N without GPS" tray** (drag source, separate from the bottom bubble-filmstrip) — drag one or several (Ctrl-click, or `[`…`]` range-select) onto the map → confirm the reverse-geocoded place → writes GPS + location to the sidecar (reuses copy/paste-GPS path) and the picture hops from tray to a dot. **Suggested location w/ confidence preview** (SHIPPED, user-confirmed on Kreta): right-click a timestamped no-GPS picture → "📍 Suggest a position — confident/approximate/rough guess" TIME-INTERPOLATES the coordinate between the GPS'd photos just before/after it (the trip's own photos as a tracklog) AND shows a coloured on-map preview *before* the confirm dialog — green rings on the before/after neighbours, a confidence-coloured pin (green ≤30 m bracket = tight cluster "magnet" / amber ≤300 m / red = wide gap or one-sided extrapolation; softened one level for long time-gaps), dashed connectors, framed via focus_area. The confirm dialog explains the estimate ("Between two photos 18 m apart (12 s)") + "Not right? Cancel and drag it onto the exact spot"; on Cancel the neighbours linger as a drag target, cleared once a placement lands. "Zoom to around this time" still there for manual. **Sidecar-fallback fix**: `preview_provider.location_targets` no longer falsely bails "no writable sidecar" for a RAW whose indexed `.xmp`/`.jpg` path is dead/stale/mangled (the trip-side twin of [[project_dead_link_prune]]) — it falls back to the RAW's sibling `<stem>.xmp` (exiftool creates it; what PL/LR read) and logs a guardrail warning naming the dead index entry so we can measure how widespread stale trip sidecars are. **Right-click details** (name / file / taken / modified / file-types) identify render-less thumbs (e.g. PNG screenshots, `.dop`-only). **Delete (move to trash)** on the whole-trip map — JPG-protected, per-day `.trash` + raw-CSV-row-filter (preserves relative paths). Fixes shipped alongside: map opens over `all_keys()` not the active filter; **`item_for_key` made order-independent** in Preview/Sidecar collections (base `index_of` threw for keys outside a filtered `_order`, which silently blanked GPS'd points' date/place when the 📍 filter was on); `PreviewItem.date` falls back to the file row's `EXIF:DateTimeOriginal` (also fixes the day grid's shooting-order sort). **Drag-a-group-to-move SHIPPED (user-confirmed)**: press-and-drag ANY bubble on the trip/day map → a blue ghost pin (with the picture count) follows the cursor → release → reverse-geocode + confirm → OVERWRITES GPS/location for every picture in that group and the dot relocates (`TileMapView.group_move_requested` → `TripMapView._move_group`; place+move now share `_geocode`/`_write_gps_place`). A bubble IS a same-spot group, so they move together; when the bubble spans several distinct spots the confirm says "This group covers N different spots — zoom in first if you only meant one" (zoom is the isolation tool — greedy clusters split on zoom-in). Guardrail: "K of these already have GPS; moving overwrites it" when the group isn't one we placed this session. Empty-map drag still pans; click still selects. **Root-cause fix (was killing the drag): all bubble hit-testing moved from `itemAt()` to a new `_bubble_at()` that walks the z-ordered stack for the first item carrying a cluster** — the selection highlight ring (z=20, above the markers at z=10) has a shape() that covers its INTERIOR even with no fill, so `itemAt()` returned the ring (data=None) on any *selected* bubble. Same bug had silently broken click-reselect, right-click→Hide and double-click→zoom on a selected bubble; all fixed. **Phantom-tray filter**: `_build_points` now skips no-GPS UPNs with no real image file (`_has_image`) — orphan `.dop`/`.xmp`-only records (leftovers from early blue-deletes before we swept the .dop) were being minted as phantom "pictures" by Read & Summarize and cluttering the no-GPS tray with un-placeable ghosts (49 of them on Kreta 3/16); verified NO GPS was lost — the real pictures kept theirs. Orphan-sidecar REMOVAL still belongs to the admin heal pass. **Placement polish SHIPPED (user-confirmed):** inline confirm bar (non-modal, under the header — a QMessageBox landed on top of the very spot you were judging AND blocked nudging) + **draggable pin** on every pending place/move (release re-geocodes, bar updates) + **All/Species scope buttons** in the map header (All default; Species drops landscape/context shots — this is what surfaced backlog ch.12) + `pictures_geotagged` → the host viewer refreshes cards and re-applies its filter, so a picture placed from the map leaves the 📍 "missing GPS" grid live. Delete keeps its modal confirm (destructive). Honest wording: the move warning now says GPS may be "from the camera or an earlier session" — we only know it wasn't hand-placed this session.
**Day-coloured travel path SHIPPED (user-confirmed, "nice, maybe too subtle"):** the path is drawn per DAY on a blue ramp, light = trip start → dark = end, with a gradient legend + a per-leg tooltip (date + picture count). Each day's run is prefixed with the previous day's last point, so the overnight/relocation leg carries the ARRIVING day's colour. **Two findings from running the dataviz validator (ported to Python — no node) rather than eyeballing:** (1) days are ORDINAL, so one hue light→dark, NOT 13–20 categorical hues (the "generated hue" anti-pattern); and the ramp only resolves **~8 distinguishable steps** (OKLCH ΔL ≥ 0.06 over L 0.338–0.764) — so on a 13-day trip colour CANNOT carry day-identity, only progression, and identity comes from the tooltip (never colour-alone). Colours are interpolated, not snapped, so two days never land on the same hex. (2) **The sea was the binding constraint** — over water (`#aad3df`) the light ramp steps fail even the 2:1 ordinal floor, so every leg is a 2px coloured line on a **4px white casing** (the cartographic road trick), which decouples the palette from the tiles; on the casing all ten steps pass.
NOT built (user decided, after seeing it): **auto-detecting the "move"/hotel switch and dating the arrow.** The signal exists in principle (a move = where you SLEPT: the overnight gap + whether the next day RETURNS to the same base; a base = the cluster bookending consecutive days), and it would work on a linear bird trip. But on a hub-and-spoke family trip with two hotels and many day trips it is fragile, and it depends on having shot near the hotel at all — "there is a limit to what you can deduce from bird pictures if you don't have the hotels". Options if revisited: auto-detect w/ return-check (must stay SILENT rather than draw a wrong move), or let the user right-click → "Mark as base/hotel" and label base→base legs.
Then the GPS/data-integrity umbrella (`project_gps_data_integrity`): tracklog geotag (2a), GPS-from-addresses, location-from-GPS, purge dangling data_files/pictures. Original Phase 3 rectangle-select / 4 published Leaflet still follow. OPEN: black DxO-JPG render in tray; whether a placed `XMP:GPSLatitude` survives an sv2 re-Read (XMP-vs-EXIF); extend map-delete to the day map.
- DONE **EXIF-cache LRU retention** — the day/trip cache grew by distinct-path accumulation (the mutable bucket is keyed by PATH and updated in place, so NO edit-churn — an earlier note wrongly said path+mtime). Added a `seen` (epoch-day) stamp refreshed on every hit (`partition`) and write (`update`), and `exifcache.prune_expired(cache, ttl_days=90)` called before save on the day/trip scan only (the master cache is rebuilt wholesale by Rescan → left unpruned). LRU not FIFO: active paths never age out (only stopped-touching ones), and the usual "reads become writes" LRU cost is free here since the cache is fully re-serialized every scan anyway. Legacy entries (no `seen`) are migrated with a full-TTL grace, so the upgrade evicts nothing. A wrong eviction only ever costs one re-read (pure optimization, size/mtime/fingerprint-guarded). Measured before: day/trip 1.75 MB/1272 entries/15 ms load (master cache is the separate 205 MB one). [[project_exif_cache_design]]

**Parked (by design)**
- PARKED **delete step 3 (JPG-bearing UPNs)** — blocked by never-trash-a-JPG; whether/how is open.
- REJECTED **Undo → Restore-from-trash** — decided against (not just deferred): a lone "Undo" implies an action-log we lack. Engine exists headless (undo_delete/apply_restore); data safe in `.trash`. If recovery ever needs to be user-facing it'd be a deliberate Restore-from-trash browser, never an Undo. [[feedback_no_misleading_affordances]]
- DEFERRED trip-side SQLite.

## 4. Trip-to-Master Pipeline — COMPLETE ✓
"Add Trip Pictures to Master" 3-step + Run-all shipped; on-disk "Delete Local Files Backed Up in Master…" (trip_delete_scan) live and sole delete authority (svr7/deletelist removed). Nothing open — whole trip→master flow in daily use. [[project_svr7_deletelist]] [[project_trip_delete_rethink]]

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

## 12. Landscapes in the Master — NEW, ARCHITECTURAL (raised 2026-07-12)
Today a landscape / context photo is a **trip-only, second-class citizen**: it never reaches the master. Raised while adding the map's All/Species scope — "Species" drops exactly these pictures, which made their status visible.

**How it works now (grounded):**
- A picture is a **"blog extra"** when its subject carries a marker keyword: `LANDSCAPE_KEYWORDS = {'landscape', 'landschap'}` → `SpeciesClassification.isBlogExtra(subject)` (`engine/speciesclassification.py:69-89`).
- It is deliberately **exempted from species resolution** so it doesn't warn as an unknown species (`engine/projectlib.py:799` clears `errormessage`).
- It renders in the **day blog** as a card with no taxon, and is excluded from species groups ("aves excludes landscapes, which belong to no group") — `engine/websupport.py:328/491/820/1502`.
- **It is SKIPPED when freezing to the master**, both its picture record and its file records: `batch/sv6Freeze.py:190` (`AppendChangedPictureData`) and `:147` (`AppendChangedFileData`), logging *"N blog-extra (landscape) pictures excluded from the master collection."*

**Why this is architectural, not a small feature.** The master's whole organising principle is the species: the on-disk tree is species folders (`TargetPath` from the taxonomy chain), and species is the key for the MasterDB index, the species counters, "move to species", the website's per-species pages, the best-JPG/rating pick, and the audit/heal tooling. **A landscape has no taxon, so there is no answer today to "where does it live and how is it found."**

**Open design questions (the `topics/` direction above answers #1; the rest are open):**
1. ~~Where do the files go?~~ → **`topics/<topic>/<leaf-or-year>/`, a sibling of `biota/`.** (Alternative considered: key landscapes by **location**, the one thing they all do have — could still be the *leaf* inside `topics/landscape/`, e.g. year then place.)
2. **How are they indexed?** MasterDB and the CSV schema assume a species per picture. The generalisation is *subject = species OR topic leaf*; species-less records otherwise ripple into every species-keyed query, counter, audit and the "move to species" tool.
3. **What does the website do with them?** Stay blog-only (stored in master purely for safekeeping), or earn a home — topic pages / a per-year landscape gallery? Storage and publication are separable. [[project_direction_beyond_website]]
4. **What is "best" without a species?** The rating/best-JPG pick groups by species. Group by topic leaf (year/place), or not at all?
5. **Migration.** Every trip committed so far has had its landscapes excluded. A backfill would re-ingest them from trips that still exist on disk; landscapes from already-retired trips are only recoverable from the picture root (they were never deleted — see above).
6. **How is a topic declared?** Today the marker is a hardcoded keyword set (`LANDSCAPE_KEYWORDS = {'landscape','landschap'}`). A topics branch needs a small registry (topic → leaf rule) so `planes/F16` doesn't mean touching code per topic.

**VERIFIED 2026-07-12 — NO immediate data loss (an earlier note in this chapter claimed there might be; it was wrong).** Both delete paths leave landscape originals alone:
- `trip_retire_scan.execute_retire` does `shutil.rmtree(trip_db)` — it deletes the trip **metadata repo**, not the picture files in the picture root. (Its blockers are only B1 virttrip exists / B2 virttrip has blogs / B3 not the active trip; none mention blog extras, but none need to.)
- `trip_delete_scan` deletes a file **iff its UPN is in the master** ("A file is deletable iff its UPN is in the master"). A landscape is never in the master → never confirmed → **correctly kept**.

**The REAL problem (and the reason to do this chapter): landscapes have no path into the master at all, so they are never backed up.** After a retire, a landscape survives only as a loose file in the picture root — no master copy, no trip index, and permanently un-cleanable (the cleanup tool will report it as "keep — not in master" forever). They are not lost today; they **accumulate as un-backed-up orphans**, one disk failure or one manual cleanup of the picture root away from being gone.

**DIRECTION (user, 2026-07-12): a `topics/` branch beside `biota/`.** The master tree is rooted at the taxonomy (`biota/…` chain → species leaf). Add a **sibling top-level branch for non-biota subjects** — e.g. `topics/landscape/<year>/` — organised by year. This generalises: a topic can carry its own "species"-like leaf, so `topics/planes/F16/` works the same way (the user's example; not an actual interest, it shows the shape). The organising key stops being *species* and becomes **subject**, which resolves either down the **biota** chain (taxonomy → species) or down a **topics** chain (topic → leaf/year). That gives landscapes a real home, a backup path into the master, and a website home, without special-casing them everywhere. Fits the existing machinery: `TargetPath` is already built from a chain — a topics chain just yields a different root.

**RECOVERY IS POSSIBLE — the data is all still on disk (user, 2026-07-12).** Nothing was deleted (see VERIFIED above), so every landscape ever shot is still in the picture root. Better: **the whole master record can be rebuilt from the files beside the RAW, without the trip repo** — which matters because a retired trip's metadata is gone:

| What the master record needs | Where it survives on disk |
|---|---|
| subject (the `landscape` keyword) | the **`.xmp`** sidecar — keywords always go to XMP |
| GPS + location | the **`.xmp`** |
| rating + colour label | the **`.dop`** — NOT the XMP (PhotoLab mistreats colour/rating there) [[project_photolab_xmp_disappear]] |
| date / camera / EXIF | the **RAW** itself |

All three survive (the file policy already says *keep the `.dop`* [[project_file_policy_dop_dng]]), so a retired trip's landscapes are fully reconstructible from `RAW + .xmp + .dop`. This is an unplanned but real validation of sidecar-as-source-of-truth.
**⚠ THE TRAP:** a scan that reads only the `.xmp` finds the landscapes but **silently loses every rating and colour label** (they live in the `.dop`). Get this right up front.

**Cost is fine.** Identifying candidates only needs `XMP:Subject` / `IPTC:Keywords` — the same two fields `projectlib` already reads — i.e. a narrow-tag read over the tree, and the EXIF cache makes every pass after the first near-free. Scale reference: the master rescan did **159,973 files cold in ~5m25s at 16 workers** [[project_exif_parallelism]].

**ORDER OF WORK (the scan cannot go first — there is no `TargetPath` until a topic has a rule):**
1. **Topic registry** — declarative topic → leaf rule, so adding `planes/F16` is data, not a code change (today it's the hardcoded `LANDSCAPE_KEYWORDS`).
2. **Scan → dry-run report** — walk the picture root with the parallel EXIF reader; list every topic picture found, with what was reconstructed from RAW + `.xmp` + `.dop`.
3. **Reviewed ingest** into `topics/…`, with backup — dry-run → review → explicit apply, never an auto-fix. [[feedback_review_over_autofix]]

[[project_blog_extras]] [[project_master_db]] [[project_trip_retire]]

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
