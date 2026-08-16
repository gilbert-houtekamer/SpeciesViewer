# Maps — design plan

Status: **design agreed, not yet coded** (2026-07-10, ClaudeSpecies).

A map is a new *view* over the existing picture-provider seam — the 4th consumer
of `PictureCollection` after the grid, the large viewer and the preview. It is
**not** a bolt-on: the collection already speaks geo.

## Why maps (the USP)

Lightroom has a map; our edge is **flexibility and a three-window workflow**:
grid + large viewer + **map** open at once, with a filmstrip you can drag *into*
the map, and a rectangle-select on the map that drives the selection in the
other two windows. Plus two things LR does poorly:

- **Distribution / range**: overlay up to 4 species and compare where they occur
  (blauwe reiger vs black heron — one is everywhere, one is local).
- **Approximate geo-backfill**: old / virtual trips shot before in-camera GPS
  (e.g. "Panama") — search the place, drag photos onto the map to assign
  approximate coordinates from memory / the photobook.

## What already exists (reuse, don't rebuild)

- **Geo data seam**: `PictureCollection.gps(key) -> (lat, lon)` (signed decimal),
  `item.has_gps`, `gps_text`, `place_fields`, `location_line`. The map reads
  these; the filmstrip is the collection's ordered items.
- **GPS write path**: `gui/geolocate.py` (Nominatim reverse-geocode, proper
  User-Agent, throttled ≤1/s), copy/paste-GPS, `gui/location_edit.py`, batched
  XMP writes. "Drop on map → set coords" = write GPS to the sidecar, then
  reverse-geocode the place name for free.
- **Selection sync**: grid ↔ large viewer already sync via `selection_changed`;
  the map becomes a third participant.
- **Nominatim**: already integrated; forward-geocode ("Panama" → recenter) is the
  same API.

## Rendering decision: NATIVE (QGraphicsView slippy-map)

Chosen over embedding a web map. The key clarification:

- The overhead we avoid is **QtWebEngine** (embedded Chromium: ~150 MB, packaging
  pain, Mac-port friction) — NOT Leaflet. The app has no QtWebEngine dep today
  and we keep it that way.
- **Leaflet** only appears in the eventual **published** trip map (Phase 4): a
  static HTML page rendered by *visitors'* browsers. Zero desktop-app overhead —
  it never touches QtWebEngine.

Native wins exactly where the USP lives: filmstrip→map drag is native
`QDrag`/`dropEvent`; rectangle-select is a native rubber-band; pan/zoom is fully
responsive and under our control. Cost = we build the tile + pan/zoom + lat/lon↔
pixel layer (a bounded, well-trodden one-time asset), OSM tiles as
`QGraphicsPixmapItem`s, markers as `QGraphicsItem`s.

- **Tiles**: plain OSM (Web-Mercator XYZ) v1; **terrain** basemap a v2 swap (the
  tile source is pluggable). Honest User-Agent + a local disk tile cache; respect
  the OSM tile usage policy (cache aggressively, no bulk pre-fetch).
- **Coordinates**: standard Web-Mercator (EPSG:3857) lat/lon↔pixel at zoom z.

## Phasing (gradual — clustering is its own art)

- **1a** — Map Species Viewer: tiles + pan/zoom + plain pins for ONE species,
  read-only. First light; de-risks tiles + the geo seam.
- **1b** — up to **4 species** overlaid, each its own colour + a legend. Layer
  model handles N; UI caps at 4.
- **1c** — **zoom-dependent clustering** (the art): group points within ~60 px at
  the current zoom into a count-bubble; re-cluster on zoom; click a bubble to
  zoom to its bounds. Clustered **per species** so colours stay separable where
  ranges overlap. Start with a simple grid/distance clusterer; refine later.
- **2 — GPS backfill (REFRAMED by user 2026-07-10): a batch tool, NOT a map
  gesture.** Two sub-parts:
  - **2a (do first) — tracklog geotag → master.** Source GPS from a FILE: a GPX
    tracklog (phone/logger) covering the trip, or a reference photo that already
    has GPS. Correlate each photo by TIME (`DateTimeOriginal` ↔ track timestamps;
    exiftool has `-geotag track.gpx` built in). Test on a time range first
    (dry-run report), then a confirmed BATCH run that updates the MASTER: write
    GPS to the master files + DB `GPSPosition`, reverse-geocode place names.
    Existing pieces make the happy path small (exiftool `-geotag`, `geolocate.py`
    GPS→place, master stores GPSPosition); the real work is the master-mutation
    discipline (durable DB write, reverse-geocode, dry-run→confirmed-apply,
    reversible — like move/delete). Handles the bulk of the ~40% with no GPS.
  - **2b (later) — manual drag-onto-map** for the stragglers with no track:
    forward-geocode a place, drag filmstrip photos onto the map to assign
    approximate coords → write GPS + reverse-geocode. Native `QDrag`/`dropEvent`.
- **3** — rectangle-select on the map ↔ selection in grid + large viewer (the
  three-window USP fully wired, both directions).
- **4** — publishable trip map (long-term): emit GeoJSON + a static Leaflet page
  in the website build; same marker model as the editor.

## Where it plugs in

- New `MapView` window (own geometry, like the large viewer), opened from the
  Species Viewers (trip + master) and the trip viewer. Reads the host's live
  `PictureCollection`.
- **Distribution (Map Species Viewer)** reads master pictures per species from
  `MasterDB` (points where `GPSPosition` is present) — confirm master rows carry
  GPS at the density we want; backfill/skip the rest.
- Selection is shared via the existing `selection_changed` contract so all three
  windows stay in lock-step.

## GPS coverage (measured 2026-07-10, naturedb master)

- **88,953 pictures, 60% GPS'd (53,047), 11,941 distinct points.** The ~12k-point
  load is a WHOLE-MASTER problem → clustering matters there.
- **Per species the point count is tiny** — even wide-ranging birds top ~30–50
  distinct points. So Phase 1a/1b (per-species, ≤4 species) look good with PLAIN
  PINS; clustering (1c) only earns its keep for a whole-master view or a very
  common species. Confirms "pins first, clustering when it pays".
- The heron demo is real: blauwe reiger (ardea cinerea) = 35 points / 10
  countries vs zwarte reiger / black heron (egretta ardesiaca) = 2 points / 1
  country. Good 4-species overlays: huismus, aalscholver, koereiger, kleine
  zilverreiger (all 8–11 countries).
- GPS-rich (≈100%): Gambia, South Africa, Costa Rica, Tanzania, Oman, Morocco,
  Sri Lanka, Kenya (recent trips). GPS-POOR: Nederland 733/5076, USA 340/6073,
  Ecuador 5/3056 — old material; sparse until Phase-2 backfill. Pick a GPS-rich
  species for the first demo.

### Data-quality flags (bear on the clustering key)
1. Country strings are inconsistent: `USA` AND `United States`; `Nederland` AND
   `Netherlands`. Bad as an aggregation KEY (double-counts).
2. ~10,000 GPS'd pictures have NO country → any country-keyed grouping drops them.
3. **Decision: cluster on lat/lon (pixel space), NOT the country string.** At the
   lowest zoom a pixel-radius clusterer collapses each country to ~one bubble for
   free (the "country ≈ one dot" effect, done right + handling the no-country
   points). Country stays a LABEL ("The Gambia · 805 pts"), not the key.

## Open questions / later

- Clustering algorithm choice + tuning (radius, bubble sizing, singleton
  threshold) — iterate in 1c; lat/lon pixel-space clustering (see above).
- Terrain/satellite basemap provider (may need a key) — v2.
- Published-map interactivity level (static markers vs filterable) — Phase 4.

## 1d — bubble → filmstrip → large viewer (SPEC agreed, build next)

**Goal:** click a cluster bubble → its pictures appear in a bottom filmstrip →
click a thumbnail → read-only large viewer over *that bubble's* pictures. So a
dot becomes "these photos", and the filename (`2025 gambia-…`) tells the trip.

**Architecture (the "brothers & cousins" model, user-confirmed):** grid and map
are peer **collection-views**; the large viewer is the *same* `LargeImageView`
class, a "cousin" spawnable from either by handing it `(collection, keys,
index)`. The map builds its OWN collection from `master_db`, seeded only by the
picked species names — independent, inherits context (the name), never a shared
object. This promotes the map from a read-only chart to a full brother of the
grid. (Trip-context map = same pattern over the trip collection, later.)

**Decisions locked:**
- Click behaviour: **single-click a bubble → populate the filmstrip + highlight
  it; double-click → zoom in** (split the cluster). "Show me" vs "drill in" are
  distinct gestures.
- Placement: **bottom horizontal filmstrip**, in a splitter so it collapses/
  resizes. Reuse `gui/large_image_view.py FilmstripWidget(collection, keys,
  orientation=Horizontal)` (lazy thumbs, `item_clicked(key)`).
- Large viewer navigates **just the clicked bubble's pictures** (the strip's set
  — usually one bubble), read-only (master rules), same as the Master Species
  Viewer's large view.

**Build steps:**
1. **Thread UPNs through aggregation + clustering.** `_aggregate` currently drops
   `upn`; make each aggregated spot carry its `upns` list, and each cluster carry
   the union → `cluster["upns"]`. (Tooltip/count unchanged.)
2. **Master-UPN thumbnail collection** — a small `PictureCollection` over
   `master_db` that, given a UPN, resolves its best-JPG (BestJPG + TargetPath, or
   a RAW preview) and implements `item_for_key(upn)` / `request_thumbnail` /
   `request_full_pixmap`. Spans species (a 4-species map). This single object is
   BOTH the filmstrip's collection AND the large viewer's collection — the
   enabler for "large view from map".
3. **Bottom filmstrip** in `MapView` (splitter). Single-click a bubble →
   `FilmstripWidget(collection, cluster["upns"])`; highlight the bubble.
4. **Double-click a bubble → `_zoom_to_bounds`** (move the existing single-click
   zoom to double-click).
5. **Filmstrip thumb click → `LargeImageView(self, collection, keys, index)`**,
   read-only, keys = the bubble's UPNs.
6. **Standalone launcher** — a "Species Map…" entry that opens an EMPTY map; the
   user adds 1–4 species ("Add species…" already exists; allow zero initial
   layers). Makes the map a first-class sibling, not only a child-launch.

Bridges: this collection + selection wiring is the groundwork for Phase 3
(rectangle-select → selection) and Phase 2 (drag a filmstrip photo onto the map
to geotag).

## First slice to build

**Phase 1a**: a `MapView` showing OSM tiles with pan/zoom and plain pins for one
selected species' GPS'd master pictures, launched from the Master Species Viewer.
Small, visual, low-risk — then 1b (4 species) and 1c (clustering) layer on.

---

## Phase 5 — TRIP / DAY map (NEW, inserted 2026-07-11; build BEFORE Phase 2)

The map so far is **master + per-species only**. But (virtual) trips and days are
just as valuable — a trip map shows *how you travelled*. So Phase 5 is a second
`MapView` mode fed by the **live viewer `PictureCollection`** instead of
`master_db`+species. It is the FIRST maps work to do next, and it unlocks the
natural home for the GPS-assignment work (drag no-GPS groups onto the map).

### How it differs from the master map
- **Population = all species** on the current day/trip selection (one spatial
  pool), not one species. Clustering runs over the whole pool, not per-colour.
- **Collection = the host's live collection** (culling / Read&Summarize grid /
  trip Species Viewer) — no `master_db` build, no lazy per-species collection.
  The bubble's UPNs feed a `FilmstripWidget(live_collection, keys)` directly.
- **Tooltip = date + location/city** (from `item.location_line` / place_fields),
  not "35× <species>".
- **Travel path** — a time-ordered polyline through the day's/trip's points.

### Grid ↔ map relationship (DECIDED 2026-07-11): BROTHERS
Grid and map are **peer collection-views over ONE shared live collection**, not
parent/child. The map is opened *from* the trip/day viewer and reads that same
`PictureCollection`. GPS'd pics = dots; **no-GPS pics live in a "No GPS (N)" tray
the MAP window owns at the bottom** — you drag from that tray onto the map to
geotag, all inside one window for the first cut. Cross-window grid→map drag +
two-way rubber-band selection sync come later (ties into Phase 3).

### 5a — read-only trip/day map (DO FIRST)
Ship this before any geotag write-back. Reuses the tile/pan/zoom canvas,
`_aggregate`/`_cluster`, and bubble→filmstrip→large-view wholesale.
1. **Alternate MapView construction from a collection.** New entry path
   `MapView.from_collection(parent, collection, title, scope)` (or a mode flag):
   builds ONE all-species layer from the collection instead of `master_db`. Points
   = iterate items, for each `item.has_gps` take `collection.gps(key)` →
   `{lat, lon, upn:key, place, date, sci}`.
2. **Generalise the marker tooltip + data.** Keep `data(0)` carrying
   `{upns, count, bounds}`; make the tooltip builder pluggable — trip mode shows
   **date + place**, species mode keeps "<n>× <NL>". Cluster spans mixed species.
3. **Filmstrip uses the live collection directly** — `bubble_activated` →
   `FilmstripWidget(live_collection, cluster["upns"])`; thumb → `LargeImageView`
   over the live collection. (No `_collection_for_species` needed — simpler than
   master.) Editing follows the trip collection's own read/write rules.
4. **Travel path toggle** — `QGraphicsPathItem` polyline through time-ordered
   world-pixel coords; "Path" button. Default on for day scope. Needs an item
   timestamp (reuse the card's date-sort field).
5. **Entry points** — a "Map…" action wherever a trip/day `PictureCollection`
   grid is shown (culling viewer, Read&Summarize/preview grid, trip Species
   Viewer). Opens `MapView.from_collection(...)` over the current view's
   collection, so map scope = whatever the viewer shows (day, or trip-wide if the
   viewer produces a trip-wide collection — else start day-scoped and add a
   trip-wide aggregating builder).

### 5b — drag-to-geotag (the payoff; after 5a)
This is the **trip-scoped sibling of Phase 2b** (master 2a/2b stay separate — they
carry the master-mutation discipline). Trip sidecars are the source of truth, so a
geotag here is a normal sidecar write like copy/paste-GPS — lighter ceremony.
1. **No-GPS tray** — `FilmstripWidget` over the subset where `not item.has_gps`,
   labelled "No GPS (N)".
2. **Drag** — tray items are `QDrag` sources (mime = UPN list); map viewport
   accepts drops. On drop: map pixel → lon/lat via `world_to_lonlat`; write GPS to
   those UPNs' sidecars (reuse the copy/paste-GPS write path / `location_edit`
   batched XMP write), then reverse-geocode place (`geolocate.py`) → write
   City/Municipality/State/Country.
3. **Refresh** — dragged pics move tray→dots (map refresh); grid cards refresh via
   the existing external-edit watcher / `picture_changed`. Batched write per the
   established pattern; reversible (it's a sidecar edit).

### Sequencing note
Phase 5 (trip map) is FIRST. Then the broader **GPS & data-integrity umbrella**
(memory `project_gps_data_integrity`): (2) GPS-from-addresses, (3) location-from-
GPS backfill, (4) 2a tracklog geotag, and finally (1) purge dangling
`data_files`/`data_pictures` entries. Original Phase 3 (rect-select) / Phase 4
(published Leaflet) still follow.

---

## 2a — SHIPPED 2026-08-16 (live-tested, both entry points)

Built as a **unified** tool rather than the two separate slices this addendum
was originally written as: one dialog covers both altitude-fill (photo
already has GPS) and full lat/lon+altitude-fill (no camera GPS at all),
reading the same tracklog once. Two entry points share the one dialog/engine:
**Master ▸ Maintain Master Database ▸ Geotag from Tracklog…** (whole master,
date-range query) and a **Geotag from tracklog…** button on the Read-a-card
dialog (`gui/trip_process.py:ReadSDCardDialog`, shared by Days/Trips/Unfiled),
scoped to just the pictures a card read just produced — chosen over a
yellow-panel button because it's one integration point covering all three
lanes at the moment the files are freshest.

Key files: `engine/geo.py` (parsers + `clean_altitude_series` cleaning),
`engine/tracklog_geotag.py` (`plan`/`apply` for the master, `plan_entries`/
`apply_entries` for a card-read's files, `MasterSource`/`FilesSource`
adapters), `gui/tracklog_geotag_dialog.py` (the shared review-table dialog),
`gui/geolocate.py` (`write_altitude`, `batch_datetime_gps`).

**Two known follow-ups, not yet built:**
1. The Read-Card geotag button becomes clickable as soon as the copy phase
   finishes, but "Add location names" (if ticked) keeps running behind its
   own modal progress dialog for a few more seconds — a click during that
   window looks swallowed, reads as "needs a double click." Not a crash, just
   bad enablement timing; fix is to enable the button only once the whole
   read (geocode included) is truly done.
2. `clean_altitude_series`/`nearest_track_point` were hardened against a
   large/dense tracklog (two-pointer window instead of an O(n²) rescan, sum()
   instead of the slow stdlib `statistics` module, binary search instead of a
   linear scan) — but the deeper fix Gilbert asked for (2026-08-16) is still
   open: **thin the cleaned points to about one every 5 minutes before
   correlation**. Altitude barely moves faster than that — it's already what
   the rolling average smooths toward — so a long/dense log doesn't need
   every raw sample kept around for the search.

### Original addendum below (research + build plan, kept for the reasoning trail)

## 2a — altitude-source addendum (2026-08-15, research complete, ready to build)

**Why this exists as its own slice of 2a:** the OM-1 already tags `GPSLatitude`/
`GPSLongitude` live into every RAW via Bluetooth (confirmed via exiftool on
2026-08-15 shots) — the ONE gap is `GPSAltitude`, which the camera never writes.
A full field-test day (OM-1's own NMEA log vs a second phone tracker, both
running alongside real shooting) proved the OM-1's own background log is
trustworthy for altitude once cleaned — no second app, no lat/lon dependency.
Full research + decision trail: memory `project_gps_data_integrity`
(2026-08-14/15 entries). This slice is **narrower and buildable sooner** than
full 2a (which still separately covers writing lat/lon from a track on days with
NO camera GPS at all — unaffected, layers on the same machinery, see bottom).

### Decided
- **Altitude only, lat/lon untouched.** Never overwrite `GPSLatitude/Longitude`
  from a track — the camera-tagged values are already trusted. The tracklog is
  consulted purely for its altitude column, matched to each photo by time.
- **Source priority: OM-1's own per-day `.log` (NMEA `$GPGGA`) is PRIMARY** — a
  free byproduct of the same Bluetooth session that already tags lat/lon, zero
  extra step. A companion phone-app `.gpx` (NomadTracks-class) is a **secondary/
  fallback** source for a day the OM-1 log is missing/gappy — NOT because it's
  more accurate (field test: matched OM-1 to a median 0.00 m / std 0.47 m once
  both are cleaned) but because it's a second independent capture.
- **Clean before use, always**: local-window outlier rejection (±5 min, reject
  points where `|value − local_mean| > 2×local_stdev`) removes the shared GPS
  altitude dropouts (both sources hit the SAME bad readings at the SAME moments
  — a satellite-geometry event, not independent per-device noise), THEN a
  rolling average (5–10 min window, tune at build time) over the cleaned points
  is what actually gets assigned to a photo — never a raw nearest-single-sample
  match, which could land inside a dropout. Validated on real data: flags ~4.3–
  4.5% of points, leaves the rolling average sitting near the true value through
  a −48 m dropout instead of being dragged into it.
- **No unit/datum conversion needed.** `$GPGGA` field 9 is MSL-corrected
  altitude by spec already (not raw WGS84 ellipsoidal height); NAP (the Dutch
  vertical datum) matches a decent geoid model to within centimeters, well under
  our own ~0.4–1 m residual noise floor. Write the cleaned value straight
  through as meters.
- **Silent-optional.** No tracklog file for a day → nothing changes, no error,
  no GPSAltitude written. Same principle as [[project_unfiled_inbox]].
- **Discovery convention**: a per-day tracklog file lives **inside that trip's
  `YYYY-MM-DD` day folder** (`utils/trip_identity.py resolve_trip_picture_root`
  — the same folder already holding `blog.ini` and the day's pictures/sidecars)
  — sibling to the content it applies to, auto-picked-up by date, "Browse…" as
  manual fallback if it's filed elsewhere.

### What's already there to reuse (confirmed by code survey, 2026-08-15)
- **`GPSAltitude` is ALREADY a first-class ingest field** — read via
  `EXIF:GPSAltitude`/`findAnySourceItem` (`engine/projectlib.py`), listed in
  `engine/csv_columns.py` + `engine/selected_fields.py GPS_FIELDS`, and already
  round-trips into `data_pictures.csv`/the master index. **No new DB/CSV column
  needed** — today it's just always empty because nothing ever writes it.
- **exiftool batching convention**: `engine/exif_writer.py run_exiftool_files
  (option_args, files, pre_args=None)` — one call per shared set of tag values,
  via a `-@` argfile (dodges the Windows ~32KB cmdline limit), always
  `-charset utf8`. No `-stay_open` session exists; every call is a fresh
  `subprocess.run`. This is the wrapper `write_gps`/`write_location_batch`
  already use — reuse it, don't invent a second one.
- **Master mutation shape** (the "write BOTH halves" discipline, already proven
  live on 2,415 pictures for place-names): sidecar/XMP write via the exiftool
  wrapper, THEN mirror into `MasterDB` — `apply_gps()` (`db/master_db.py:1685`,
  stores `GPSPosition` + provenance, one journal entry) is the closest existing
  analog; `apply_location_fields()` (`:1506`) the other. Dry-run→apply UI
  template: `gui/master_geocode.py MasterGeocodeDialog` — worker-thread lookup →
  checkable review table → confirmed batched write + `backup_pictures_csv()`.
  Reuse this shape wholesale for a new tracklog-geotag dialog, don't redesign it.

### What's genuinely new to build
1. **Track parsers** (`engine/geo.py`, PyQt-free like the rest of that module):
   - `parse_gpx_track(path) -> list[{t_utc, lat, lon, alt}]` — GPX `<trkpt>`.
   - `parse_om1_nmea_log(path) -> list[{t_utc, lat, lon, alt}]` — `$GPGGA` lines;
     needs a new `nmea_coord_to_deg(value, hemisphere)` helper (ddmm.mmmm format,
     different from `parse_gps_dms`'s exiftool-DMS-string format — don't conflate
     the two). Date comes from the `YYYYMMDD.log` filename (one file = one day),
     combined with the GGA time-of-day field.
   - Both return the same shape so cleaning/correlation code is source-agnostic.
2. **Cleaning function**: `clean_altitude_series(points, window_s=300, sd_mult=2.0)`
   — local-window outlier flag, then rolling-average — pure function, unit-
   testable standalone against the real 2026-08-15 logs as fixtures.
3. **Per-photo correlation**: for a photo's `DateTimeOriginal` (⚠ **local camera
   time** — the log's `@OM Digital Solutions/+0200/+0200` header / GPX's UTC
   `<time>` are NOT the same clock; convert consistently, this bit tripped us up
   by hand this session) find the cleaned rolling-average value nearest in time;
   skip (no write) if nothing falls within a max-gap cutoff (~10–15 min, TBD) —
   this is what makes coverage gaps silently degrade instead of writing garbage.
   **`DateTimeOriginal` is already sitting in `data_pictures.csv`** for any
   master-batch run — correlation is a pure in-memory pass over the index, ZERO
   EXIF file I/O. The only file touch in the whole flow is the final write
   (step 4) — don't build a "read every RAW's EXIF first" step, it isn't needed.
4. **Write path — NEW, nothing today writes altitude**: `write_altitude(files,
   altitude_m)` in `gui/geolocate.py`, sibling to `write_gps`, setting only
   `XMP-exif:GPSAltitude` + `XMP-exif:GPSAltitudeRef` via `run_exiftool_files`.
   ⚠ **GPSAltitudeRef encodes sign separately** (0 = above sea level, 1 = below) —
   `GPSAltitude` itself is a magnitude. Polder shots will legitimately be
   *below* NAP zero (our own 2026-08-15 data had −1 to −4 m stretches) — get the
   ref bit right or a negative elevation silently becomes a positive one.
5. **Master mirror**: extend `apply_gps()` (or add a thin sibling
   `apply_altitude()` next to it) to also set the already-existing `GPSAltitude`
   column, same one-journal-entry discipline as the rest of `MasterDB`.
6. **Batch write grouping**: `run_exiftool_files` applies ONE shared set of tag
   values to a whole batch of files (confirmed — that's how `write_gps`/
   `write_location_batch` get their "534 calls not 2,415" economy: group files
   by identical target value). Altitude varies more than a place-name would, but
   the rolling-average step already makes it PIECEWISE CONSTANT across a window
   — photos inside the same window naturally share one rounded value, so the
   same group-by-value trick applies for free. Confirm at build time whether
   `run_exiftool_files` can do per-file distinct values in one call (multiple
   `-execute` blocks) as an alternative if finer granularity is wanted later.
7. **New dialog** (e.g. `gui/master_tracklog_altitude.py`, or fold into a future
   full 2a dialog): pick a date range / trip, auto-discover a day-folder
   tracklog file (OM-1 `.log` preferred, `.gpx` fallback, manual Browse… override
   if multiple/misfiled) → parse → clean → per-photo review table (proposed
   altitude, flagged "no coverage" rows) → confirmed batch apply. Template:
   `MasterGeocodeDialog` end to end.

### Validation source: iPhone HEIC altitude (QA cross-check, not a track format)
Confirmed live in the real database (2026-08-15, `C:\laptop\trips\safaridb\…\panthera
leo\*.HEIC`, 2024 South Africa trip): Apple/iPhone writes `GPSAltitude` +
`GPSAltitudeRef="Above Sea Level"` into HEIC EXIF, same MSL convention as the
OM-1's `$GPGGA`. Sample (iPhone 12 Pro, three shots 8–31s apart, ~stationary):
`1200.5 m / 1200.4 m / 1200.6 m` — stable to ~0.2 m, and 1200 m ASL is exactly
right for the Highveld near Johannesburg/Pretoria. Good sanity check, but
**anecdotal as evidence** (one device, one trip, not concurrent with the
Aug-14/15 OM-1 test days) — noted here, not treated as proof.
**Use, don't build for:** wherever a day's master already has HEIC shots
alongside OM-1/tracklog coverage, diff `GPSAltitude` (HEIC) against the
tracklog's cleaned value at the matching time/place as a free extra QA signal
in the review table — no new parser needed, `GPSAltitude` is already an ingest
field on both sides. **2026-08-16 (planned): user shooting iPhone photos
alongside tomorrow's OM-1/tracklog session** — first genuinely concurrent
same-day comparison across all three sources (OM-1 log, phone tracklog app,
iPhone HEIC EXIF). Pick this up from that data before treating iPhone-HEIC
cross-check as validated rather than anecdotal.

### Relationship to full 2a (lat/lon-from-track, for days with no camera GPS)
Unaffected and unblocked by any of the above — it's the same track-parser
output (`lat, lon` are already in the parsed points, just unused here), reusing
the ALREADY-BUILT `write_gps`/`apply_gps` for position instead of the new
altitude writer. Can be built as a second pass over the same dialog once the
altitude slice is live, or built first if a no-GPS day becomes the more urgent
need — either order works, nothing above needs redoing.
