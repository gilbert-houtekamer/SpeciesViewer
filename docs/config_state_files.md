# SpeciesViewer — Configuration & State Files Reference

Every persistent file the application reads or writes, grouped by scope.
"Safe to delete" means the file is regenerated automatically; losing it has no
data-loss risk. "Never delete" means it is the canonical store — losing it
destroys data that cannot be reconstructed.

---

## Machine scope
*One copy per computer, lives in the repo root or in `%LOCALAPPDATA%`.*

| File | What it stores | Safe to delete? |
|------|---------------|-----------------|
| `sv_config_<hostname>.ini` | Master config for this machine: paths to trip/master databases, SFTP password, window geometry, RAW-decode quality knobs, recent-project list. E.g. `sv_config_bigboy.ini`. Created automatically if absent. | **No** — losing it means re-entering all paths and credentials. |
| `sv_config.ini` | Fallback / legacy machine config (same schema). Used when no hostname-specific file exists. | **No** |
| `sv_gui_cache.json` | Lightweight GUI state: recent-projects list (last 10), per-dialog convenience values (last folder, column widths, etc.). Repo root. | Yes — rebuilt from usage. |
| `studio.log` | Execution log for the current run. Opened in write mode on every launch (previous content discarded). Repo root. | Yes — a new one is written each run. |
| `svconfig.ini` | **Ephemeral** — generated at the start of each batch run from the active machine config and passed to the sv0–sv7 scripts. Never hand-edit. | Yes — always regenerated. |
| `%LOCALAPPDATA%\SpeciesViewer\db\master.db` | SQLite read-cache of the master collection (pictures, files, species). Rebuilt from the master CSVs whenever their mtime changes. WAL journal (`master.db-wal`, `master.db-shm`) lives alongside it. | With caution — forces a slow rebuild from CSVs on next open. The WAL files can be deleted if the app is closed. |
| `%LOCALAPPDATA%\SpeciesViewer\db\master.wal.jsonl` | Append-only intent log for pending master writes (rating changes, add/delete). Checkpointed into the master CSVs by a background worker. | **No** — an unprocessed entry here represents a real write that hasn't landed in the CSV yet. |
| `%LOCALAPPDATA%\SpeciesViewer\fullres_cache\` | Full-resolution decoded JPEG previews for the 100% culling view. Named `<stem>.fullres.jpg`. | Yes — recomputed on demand (~1 s per RAW). Clear via the cache manager in the GUI. |
| `%LOCALAPPDATA%\SpeciesViewer\preview_cache\` | Thumbnail / preview JPEGs extracted from RAW or HEIC files, one sub-folder per source folder (keyed by SHA-1 of path). | Yes — recomputed on first open. Clear via the cache manager. |

### Machine config sections (`sv_config_<hostname>.ini`)

| Section | Key examples | Purpose |
|---------|-------------|---------|
| `[Global]` | `config_version`, `dxo_path` | App-wide defaults |
| `[Master]` | `masterbiotadatabase`, `masterbiotawebroot`, `readonly` | Path to the master collection |
| `[Trip]` | `tripbiotadatabase`, `tripbiotawebroot` | Active trip database path |
| `[VirtTrip]` | `virttripbiotadatabase`, `virttripbiotawebroot` | Active virtual trip |
| `[websettings]` | `Sftppwd`, `base_url` | SFTP credentials and website URL |
| `[fullres]` | `algorithm`, `fbdd`, `bright`, `denoise`, `scale` | RAW decode quality for the 100% view |
| `[preview]` | `max_long_edge` (default 3200), `quality` (90) | Size of the previews we lift out of a RAW for the grid and the fitted view. Cameras embed wildly different ones (OM-1 3200 px, Nikons a full-size 6000 px, the D90 up to 9.9 MB), so they are normalised to one reference size. `0` = cache whatever the camera embedded. Set in the Decode Quality dialog, beside the RAW-decode knobs. |
| `[avibase]` | `source_format`, `phone_layout`, `first_column_lang` | Excel checklist export settings |
| `[masterdb]` | `pending_warn_seconds`, `checkpoint_debounce_seconds` | SQLite write-queue behaviour |
| `[performance]` | `exif_workers` | Parallel exiftool workers for reads. `auto` (default; ≈physical cores, capped 16, floored by file count) or an integer to pin this machine. Hardware-dependent, hence machine scope. |
| `[WindowState]` | `<dialog>_x/_y/_w/_h`, `<dialog>_vsplit/_hsplit` | Per-dialog geometry — one entry per window |
| `[PhotoSorter]` | `last_source_folder`, `last_dest_folder` | Last SD-card and destination paths |

---

## Master scope
*One copy shared across all trips, lives under `<master BiotaDatabase>/`.*

| File | What it stores | Safe to delete? |
|------|---------------|-----------------|
| `metadata/active/data_pictures.csv` | Canonical picture index: UniquePictureName, Rate, TargetPath, BestJPGPathName, … (~85 k rows). | **Never** |
| `metadata/active/data_files.csv` | Canonical file index: one row per physical file (ORF, JPG, XMP, MOV, …), ~178 k rows. | **Never** |
| `metadata/active/species.csv` | Per-species master record: ScientificName, picture/file counts, JPG map. | **Never** |
| `metadata/active/speciescollection.csv` | Species groupings and taxonomy annotations. Merged from trips by `svr5MergeSpeciesCollections`. | **Never** |
| `metadata/cache/exif_cache.json` | Global EXIF cache for trip re-reads. Two buckets: immutable (RAW keyed by name+size) and mutable (XMP/JPG keyed by path+mtime). Three rotating backups (`.bak1`–`.bak3`). | With caution — clearing forces a full-trip exiftool re-scan (~50–70 s per trip). |
| `metadata/cache/exif_cache_master.json` | Same structure, dedicated to the Master › Rescan operation. Kept separate so a master scan (159 k files) doesn't pollute the faster per-trip cache. | With caution — same cost as above for the master-wide scan. |
| `metadata/active/backup/<date>/*.csv.gz` | Daily gzipped snapshot of the three master CSVs (154 MB raw → ~7.3 MB, written in ~1 s), taken silently on startup and pruned to the newest 14. `manifest.json` records the row counts and the consistency verdict at the time. **A backup is REFUSED when the master fails its check** — a snapshot of a broken master would eventually push the last good one out of the rotation. Exists because the master has two kings (canonical CSVs, derived SQLite mirror that writes back to them) and on 2026-07-14 the mirror won an argument it should have lost. | **Never by hand** — it prunes itself. Database Settings ▸ Restore… puts one back. |
| `metadata/active/backup/pre-restore-*/` | The CSVs a Restore replaced, gzipped, so a restore is itself reversible. | Only when you are certain the restore was right. |
| `.trash/trash_manifest.csv` | Audit log of every delete operation: timestamp, UPN, original path, trash path, batch ID. | **Never** — it is the undo record. |
| `.trash/<date>/<upn>/` | Trashed picture files (RAW + XMP + sidecar; JPGs are never trashed). Grouped by delete date and UPN. | **Never** — only remove if you are certain the deletion was correct and you want to free space. |
| `.trash/snapshots/<batch>.json` | JSON snapshot of the DB rows removed in one batch delete; used by undo_delete(). | **Never** |

---

## Trip scope
*One copy per trip, lives under `<trip BiotaDatabase>/`.*

| File | What it stores | Safe to delete? |
|------|---------------|-----------------|
| `trip.ini` | Trip metadata: name, date range, banner image, NL/EN title and message. | **No** — hand-authored, not regenerated. |
| `virttrip.ini` | Same as `trip.ini` for virtual trips (`trip_type=virtual`). | **No** |
| `metadata/active/data_pictures.csv` | Trip picture index (subset of master format). Overwritten by WriteCommit. | **No** |
| `metadata/active/data_files.csv` | Trip file index. Overwritten by WriteCommit. | **No** |
| `metadata/active/species.csv` | Trip species list. | **No** |
| `metadata/active/speciescollection.csv` | Trip species collections. | **No** |
| `metadata/frozen/frozen_data_*.csv` | Snapshot of reviewed/finalised day data, written by the Freeze step. WriteCommit promotes these to `active/`. | **No** |
| `metadata/backup/<date>_data_*.csv` | Pre-commit backups of `active/` CSVs, one per WriteCommit run. | With caution — historical checkpoints; safe once the trip is fully committed. |
| `admin/blog_admin.csv` | Blog post metadata per day (title, species selection, NL/EN text). Used by WriteBlog and RedoBlog. | **No** — losing it makes blog regeneration incomplete. |
| `admin/FTPcache.json` | Remote SFTP directory listing cache. Checked before uploading to avoid redundant transfers. | Yes — rebuilt on next upload. |
| `admin/speclist.csv` / `speclist.txt` | List of species added during this trip (output of `svr1AddSpecies`). | Yes — informational output, not an input to any step. |

---

## Per-day scope
*Created per shooting day, lives under `<trip>/metadata/review/<YYYY-MM-DD>/` until frozen.*

| File | What it stores | Safe to delete? |
|------|---------------|-----------------|
| `statemachine.ini` | Progress tracker for the sv1–sv7 pipeline steps. Each step sets a boolean + a message when it succeeds. Determines which steps the GUI shows as done/pending. | **No** — deleting it resets all steps to "not run"; the next run re-executes everything. |
| `data_files.csv` | Per-day EXIF output from ReadDirectory (sv2). One row per physical file. | **No** — deleting requires re-running sv2. |
| `data_pictures.csv` | Per-day picture summary from WritePictures (sv3). One row per UPN. | **No** — deleting requires re-running sv3. |
| `species.csv` | Species assignments for this day. | **No** |

---

## Per-folder / per-card scope
*Written next to the picture files themselves.*

| File | What it stores | Safe to delete? |
|------|---------------|-----------------|
| `<picture folder>/.sv_sidecar_cache.json` | Metadata cache for XMP sidecars in that folder: rating, label, description, species, GPS, EXIF basics. Stale detection by mtime. | Yes — rebuilt from XMP on next culling-viewer open (one exiftool batch call per folder). |
| `<SD card root>/.sv_ingest_cache.json` | SHA-256 fingerprints of files already copied from this card. Enables incremental top-up ingest (skip already-copied files). | Yes (on the card) — forces a full re-hash on the next ingest run. |

---

## Deletion safety cheat sheet

| Colour | Meaning |
|--------|---------|
| 🟢 Safe | Rebuilt automatically; no data risk. |
| 🟡 Caution | Rebuilds, but costs time or loses convenience state (window positions, credentials). |
| 🔴 Never | Canonical data. Loss = permanent data loss or broken undo. |

| 🟢 Safe to delete | `studio.log`, `sv_gui_cache.json`, `svconfig.ini`, `fullres_cache/`, `preview_cache/`, `FTPcache.json`, `speclist.*`, `backup/*.csv`, `.sv_sidecar_cache.json`, `.sv_ingest_cache.json`, `exif_cache*.bak*`, `master.db-wal`, `master.db-shm` |
| 🟡 Caution | `sv_config_<machine>.ini` (path/credential loss), `master.db` (slow rebuild), `exif_cache*.json` (slow re-scan), `statemachine.ini` (re-runs all pipeline steps), `metadata/frozen/` (history loss) |
| 🔴 Never delete | `data_pictures.csv`, `data_files.csv`, `species.csv`, `speciescollection.csv`, `trip.ini`, `blog_admin.csv`, `master.wal.jsonl`, `.trash/**` |
