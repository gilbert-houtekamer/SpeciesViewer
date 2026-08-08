# SpeciesViewer — Export / Import a Trip

A design note. Status: **PLANNED — not built.**

Companion to `menu_reorg_plan.md`. Prior art that this deliberately copies:
`batch/svv7ExportVirtualTrip.py` + `ImportVirtualTripDialog`
(`gui/virttrip_process.py:1197`) for the ZIP/extract shape, and
`InstallSmallMasterDialog` (`gui/trip_process.py:4266`) for the
speciescollection merge-back.

---

## The idea in one line

**Move a trip's admin between machines as a unit** — trip.ini, its two images,
its species list, its publication status — so that preparing a trip on the big
server and taking it into the field on the laptop is a supported move rather
than a hand-copy.

## Why now

It closes the hole under the rule *"master unreachable is an error."* That rule
is only fair if there is a proper way to carry the work from one machine to the
other. Today there is one for the Master (SmallMaster) and one for an Album
(Export/Import Album); a Trip is the gap.

## Scope — what is and is not in the ZIP

**The rule that decides it.** Carry only what is (1) *hard to recreate* — the
trip's initial setup — or (2) *easy to forget* — the species list, the lifer
status, the publication status, the translations. Everything the GUI can
regenerate from the pictures in one click stays out, because re-running it is
good practice anyway.

**In (all small, all text or two images):**

| | Why |
|---|---|
| `trip.ini` | the whole trip identity: name, slug, dates, texts, banner/picture filenames, `picture_root`, `name_profile` — hard to recreate |
| `tripimage.jpg` / `tripbanner.jpg` | whatever `trip_picture` / `trip_banner` actually name — the trip root's images |
| `metadata/active/speciescollection.csv` | the trip's species list — easy to forget |
| `metadata/initialize/master_species_baseline.csv` | the frozen lifer baseline, if present (tiny; see D4) |
| `admin/TransCache.csv` | the trip's own translation cache (see D5) — hand-checked descriptions, expensive and annoying to redo |
| `export.ini` (new, written by the exporter) | manifest: `trip_id`, `web_slug`, source machine, timestamp, `published = yes/no`, `master_web_root`, SpeciesViewer version |

**Out, deliberately:**

- **Picture data** (`CurrentPictures/<year>/<trip>/<day>/…`) and the per-day
  `blog.ini` that lives beside it — an OS copy carries these, and they are the
  big thing.
- `biota/` and `web/` in the trip repo — derived output, rebuilt by Freeze /
  Build Website.
- `admin/FTPcache.json` — see D5. Carrying it is actively unsafe.
- `metadata/review/<day>/*.csv` — **decided: not carried** (D1). Read &
  Summarize rebuilds them from the sidecars, which is one easy GUI action and
  the healthier habit.

**Why excluding the picture data is safe:** the species identification lives in
`XMP:Subject` in the sidecar next to the picture
(`engine/projectlib.py:735`, `engine/filepicture.py:289`), not in the trip repo.
The day CSVs under `metadata/review/` are *derived* from those sidecars by
Read & Summarize. So an OS copy of the day folders carries every identification,
rating, colour label and GPS fix; the target machine re-derives the rest.

## Export — `Trips ▸ Export Trip…`

New batch module `batch/svr6ExportTrip.py`, `main(args, config)`, mirroring
`svv7ExportVirtualTrip` exactly (target dir in
`config["utilities"]["trip_export_target"]`, ZIP named `<trip folder>.zip`,
overwriting is safe). Dialog `ExportTripDialog` beside `SmallMasterDialog`.

1. Read the active trip root (`[trip] tripbiotadatabase`); refuse if there is
   no `trip.ini`.
2. Resolve identity via `utils.trip_identity.resolve()` and write `trip_id` +
   `web_slug` into the manifest — **explicitly**, so the import never has to
   re-derive them from a folder name.
3. Determine publication: is `web_slug` a row in the master's
   `admin/trip_admin.csv` (`triplib.Trip.getTripAdminFile()`)? Record
   `published = yes/no` plus the master web root the check was made against.
4. Zip the files listed above, resolving the image names *from trip.ini*
   (never hard-code `tripbanner.jpg` — a trip may name its own).
5. Report: N files, size, path — same one-line summary style as SmallMaster.

## Import — `Trips ▸ Import Trip…`

`ImportTripDialog`, modelled on `ImportVirtualTripDialog`. Destination is
`<tripsrootdirectory>/<ZIP stem>`.

1. **Inspect before touching anything.** Read `export.ini` from the ZIP and show
   trip name, dates, source machine, export date, published state, species
   count, and the destination path.
2. **Existing directory** → require an explicit confirmation, and state what
   will happen to each file (D3). Refuse outright if the existing folder's
   `trip_id` differs from the ZIP's — that is the wrong trip, not an update.
3. **Restore trip.ini + the images FIRST**, into the destination folder. This
   mirrors how the GUI already creates a trip: `TripCreationDialog` writes
   trip.ini and only then runs the batch (`create_trip_ini()` → `on_create_clicked`,
   [trip_process.py:389](../../speciesviewer/gui/trip_process.py#L389)), and
   `sv1MakeReposity` reads `trip.ini` for the banner/picture upload. Building
   the tree before the ini exists would repeat a bug the GUI already avoids.
4. **Create the repository structure** with `sv1MakeReposity`, so the tree,
   empty CSVs and web skeleton come from one place. It must run in a mode that
   does **not** re-seed `speciescollection.csv` from the local master and does
   **not** re-write the baseline — both would be wrong on the laptop. Skip sv1
   entirely when updating an existing trip.
5. **Restore** the species collection (merged, never replaced — D2), the
   baseline, and the translation cache (merged — D5).
6. **Publication.** If `published = yes`: pull `trip_admin.csv` from the web
   (`triplib.read_TripAdmin_from_web`) into this machine's master `admin/`, so
   the local view of what is advertised matches reality. Never push (D6).
7. **Wire up the config**: `[trip] tripbiotadatabase`, `tripbiotawebroot`,
   `[project] picturedirectory`, and the `trip_project_map` GUI cache — exactly
   what `TripCreationDialog.on_select` does. `tripsrootdirectory` is *read*, and
   only ever set to the parent of the imported folder — never cleared.
8. **Locate the pictures.** Resolve `picture_root` against this machine's
   CurrentPictures root (`trip_identity.resolve_trip_picture_root`). If the
   folder is not there yet, say so plainly — *"Copy the day folders to
   `D:\CurrentPictures\2026\2026 Kreta` and re-open the trip"* — and import
   anyway. This is the normal order of events; it is not an error.
9. Refresh the trip overview + sidebar panel (`populate_trip_overview`,
   `trip_panel.rebuild()`).
10. Report what happened, including the merge count and the re-run advice.

## The round trip

| | Home → laptop | Laptop → home |
|---|---|---|
| Trip exists on target | no | **yes** |
| trip.ini | created | updated, old copy to `.trash` |
| Images | restored | replaced if different |
| Species collection | seeded from ZIP, merged with local master's | **merged** — this is the leg that grew |
| Translation cache | restored | **merged**, `verified` flags preserved |
| Publication | pulled from web if published | already known locally |
| Day pictures | OS copy, either order | OS copy, either order |
| After import | ingest cards as usual | Read & Summarize the new days |

---

## Decisions — all settled

**D1 — `metadata/review/<day>/` is NOT carried. DECIDED.**
The day CSVs hold absolute `WindowsFileName` paths
(`engine/trip_registry.py:196` relies on that), so they are machine-specific
anyway. Read & Summarize on the target rebuilds them from the sidecars — one
easy GUI action, and re-loading the days is good practice in its own right. No
path-rewriting mode, now or later.

**D2 — Species collection: merge, ZIP wins. DECIDED.**
Both legs merge, never replace: the home master has the full list, the laptop
adds field species. Use `SpeciesClassification.mergeSpeciesCollections` as
`InstallSmallMasterDialog` does. On the *first* import to a machine the result
is `merge(ZIP, local master's collection)`, so the laptop still gets everything
the master knows. Where the same species differs on both sides the ZIP wins —
it is the side you were just working on — and the count of overridden entries
goes in the report so a surprise is never silent.

**D3 — Overwrite policy on an existing trip. DECIDED.**
Back up `trip.ini` and `speciescollection.csv` into the trip's `.trash/` before
writing (same convention as Delete Album), replace trip.ini, merge the
collection, replace images only when the bytes differ, touch nothing else.
Nothing is ever deleted.

**D4 — The lifer baseline travels, and is never regenerated on import. DECIDED.**
`svr2CountSpecies` prefers the *date rule* (species the master has a picture of
from before `fromdate`) and only falls back to the frozen
`master_species_baseline.csv`. The date rule needs the master's
`data_pictures.csv`, which a SmallMaster does carry — so lifer counts work on
the laptop. Carry the baseline file anyway (a few kB). Regenerating it from the
laptop's partial master would silently mark known species as new.

**D5 — Two caches, opposite verdicts. DECIDED.**

- `admin/FTPcache.json` — **excluded.** `cachedUpload` skips anything the cache
  claims is already on the server ([sftplib.py:74](../../speciesviewer/engine/sftplib.py#L74)),
  so a foreign cache makes the laptop skip uploads it genuinely needs.
  Excluding it costs one slow re-upload; including it costs 404s that never heal.
- `admin/TransCache.csv` — **included.** Same folder, opposite call, and the
  distinction matters: this is the trip's *own* per-repository translation cache
  ([projectlib.py:80](../../speciesviewer/engine/projectlib.py#L80) builds it from
  the trip repo, separate from the GUI's master-level one at
  [mainwindow.py:303](../../speciesviewer/gui/mainwindow.py#L303)). It holds the
  descriptions written for this trip's pictures and blogs — expensive to redo,
  and some entries are hand-`verified`. **Merge, never replace**, keyed by CRC:
  a `verified` entry must never be downgraded by an unverified one from the
  other side, whichever direction the ZIP is going. Only the *trip's* cache
  travels; the master's is untouched.

**D6 — `trip_admin.csv` is pulled, never pushed. DECIDED.**
It lists *every* published trip, and whichever machine writes it last wins. The
import may pull it from the web; it must never push a version derived from a
laptop that only knows about some trips. If publishing from the laptop is ever
wanted, that is its own piece of work: re-read from the web, add one row, write
back.

**D7 — Two machines editing the same trip: last-import-wins. DECIDED.**
Union on species, last-import-wins on trip.ini. The manifest's source machine +
timestamp is shown before the user confirms. This rests on decent use — one
machine at a time — and is a stated limitation, not a handled case. No merge UI.

**D8 — Export writes the OS-copy instruction. DECIDED.**
The export knows the resolved `picture_root`, so the manifest and the on-screen
report both name exactly which folder to copy and where it should land on the
target. Cheap, and it removes the one manual step that can be got wrong.

---

## Phasing

1. **Export** — `svr6ExportTrip.py` + `ExportTripDialog` + manifest (incl. the
   D8 copy instruction). Standalone and verifiable by opening the ZIP.
2. **Import, new trip** — the home → laptop leg, the one the feature exists for:
   trip.ini + images → sv1 → collection + baseline + TransCache → config wiring.
3. **Import, existing trip** — the return leg: `.trash` backup, species merge,
   TransCache merge, image replace.
4. **Publication** — pull `trip_admin.csv` when `published = yes`.
5. **Polish** — picture-root guidance, Capabilities doc entry.

## Non-goals

- Moving picture files. The OS copy is better at it and always will be.
- Any kind of sync, watch, or conflict resolution.
- Publishing from the laptop.
- Exporting an Album — that already exists.
