# SpeciesViewer — Data Repositories & Data Flow

DRAFT 2026-07-24 — please correct; I've misread this flow before. Once the
content is right I'll render an HTML version (like the backlog).

## Repositories

| Repository | Path | Dropbox? | Holds | Written by | Read by |
|---|---|---|---|---|---|
| **Local working pictures** | `D:\CurrentPictures\<year>\<trip>\<day>` | no | RAW/XMP/JPG/DOP originals + DxO renders (the live files you cull and edit) | SD-card ingest, PhotoLab/Lightroom | culling, trip pipeline, Merge-to-Master, delete step |
| **Trip repo** | `E:\Dropbox\trips\<trip>db` (e.g. `kretadb`) | yes | `metadata/` (per-day review CSVs, blog admin), `biota/` = **the website (developed) species files only**, `topics/`, `web/`. **Transient** — retired once the album exists | the trip pipeline sv1→sv7 (`getRepositoryPath()` = trip repo, `copy_gate="jpg"`) | the trip website `/trips/<trip>` |
| **Master** | `E:\Dropbox\naturedb` | yes | `biota/` (every species across all trips), `metadata/active/*.csv`, `topics/` | **Publish Trip to Master → Merge Pictures into Master** (master pipeline), and **Add Pictures to Master** | master website `/nature/`, Albums, Species/Master viewers |
| **Album (virttrip)** | `E:\Dropbox\virttrips\<trip>` | yes | `virttrip.ini`, blogs, banner/cover — **no pictures**; the album is a live date-range *query* over the master | **Retire Trip to Album** | album website `/trips/<trip>` (served from master data) |
| **Caches (per-machine)** | `%LOCALAPPDATA%\SpeciesViewer\` | no (local SSD) | `exif_cache/`, `preview_cache/`, `db\master.db` (SQLite) | scans / viewers | viewers, album/master overviews; rebuilt on demand |

## Lifecycle of a trip

1. **Ingest** — SD card → `D:\CurrentPictures` (cull, GPS place-names, sidecars).
2. **Develop** — PhotoLab / Lightroom → JPG renders, `.dop`, `.xmp`.
3. **Trip pipeline** (Trip menu / Active-Trip overview, per day):
   Read & Summarize (sv2+sv3) → Write Blog (sv4) → Build Website (sv5) →
   **Publish Day** (Freeze sv6 + Commit sv7).
   sv6/sv7 copy the **website (developed) species files** into the trip repo's
   `biota/` (`copy_gate="jpg"` in trip mode). Undeveloped species are NOT copied
   here — the master gets them later from `CurrentPictures` (step 4.3). The trip
   website `/trips/<trip>` points at the trip repo.
4. **Publish Trip to Master** (Master menu), three steps:
   1. *Publish Species Names*
   2. *List Trip on the Site* (adds the trip to the master trip index)
   3. ***Merge Pictures into Master*** — the master pipeline (sv2/sv3/sv5/sv6/sv7
      in `master_mode`, so `getRepositoryPath()` = `naturedb`). **Two sources feed
      the master**, which is why the mental model and the code both looked right:
      - the **selected / website pictures + their frozen metadata** come from the
        trip repo's frozen dir, `E:\Dropbox\trips\<trip>db\metadata\review\<day>\frozen`
        — this is the sense in which *"kretadb is merged into the master."*
      - the **not-selected images** (RAW/undeveloped, never on the website) still
        come from `D:\CurrentPictures`.

      Together the master ends up holding **every identified species file** — this
      copy is the backup that makes the local delete safe.
5. **Delete locals** — the species-named files in `D:\CurrentPictures` can now be
   deleted (the master holds them). The **delete gate verifies each file is
   physically present in the master first** (size-matched) before removing it, so
   a missing master copy means the local is kept. **Non-species files stay local.**
   The trip repo's metadata is no longer needed after this point.
6. **Retire Trip to Album** (Trip menu) — repoints the trip as an **album**: a live
   date-range query over the master (no picture copies). The website `/trips/<trip>`
   is now served from master data instead of trip storage.
7. **Remove the trip repo** — the steps must run in order, but **once the album is
   set up from the trip, the trip repo is no longer needed**. Remove it via
   **right-click on the trip in the Trips list**. `CurrentPictures` and the master
   are untouched.

*(Alternative direct route used in the Kreta ghost-row test: **Add Pictures to
Master** ingests a folder straight from `D:\CurrentPictures` into the master,
bypassing the trip repo. Same copy rules apply.)*

## `getRepositoryPath()` — the thing that confuses everyone (me included)

`getRepositoryPath()` returns `[repository]BiotaDatabase` — **the repo the current
run writes into**, which depends on mode:
- **Trip mode** (normal config): trip repo, e.g. `kretadb`.
- **master_mode** (`generate_svconfig_ini(master_mode=True)` sets
  `[master]MasterBiotaDatabase=""` → `isMasterRepository()` = True): the master.

The trip **Publish** and the **Merge** run the *same* sv6/sv7 code; only the target
repo differs. That is why a fix to the copy logic must be correct in **both** modes.

## The load-bearing invariant (why the ghost-row loss happened)

**The repo that becomes the backup must physically hold EVERY named file of every
identified picture it lists a row for.** The **master** is that backup — the local
originals are deleted after it's populated — so the **Merge** copies *all* species
(`copy_gate="pictures"`). A master CSV row with no file = a *ghost row*; once the
master carried ghost rows, the old delete step removed the only copy → ~14k lost.
(The trip repo is **not** the backup — it's transient and website-only — so its
Publish uses `copy_gate="jpg"`; any undeveloped rows there are harmless because the
master is built fresh from `CurrentPictures`, not from the trip repo.)

Guardrails now in place: `copy_gate="pictures"` in **master mode** (all files);
copy verification that aborts the freeze if any file didn't land; and a delete gate
that refuses to remove a local file without a size-matched master copy.

## A note on complexity

The author's own words: *"when I made the process it seemed logical, but it is
terribly complex."* Two sources feeding the master, a transient website-only trip
repo, `getRepositoryPath()` meaning different things in
different modes — this is a lot of moving parts for "get the pictures into the
master and clean up." This document is step one (making it legible); a later pass
could look at **simplifying** the flow (e.g. a single Merge source, or folding
Publish + Merge). Not urgent, but worth a backlog line.
