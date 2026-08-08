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
