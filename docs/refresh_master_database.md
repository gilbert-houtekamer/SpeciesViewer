# Refresh Master Database (admin reconcile)

Over time the master metadata (`metadata/active/data_files.csv` and
`data_pictures.csv`) drifts out of sync with what is actually on disk under
`biota/`: an import records a file that never lands, a picture is moved to
another species folder, an old path string gets mangled. **Refresh Master
Database** compares the two and offers to reconcile them. It lives under
**File ▸ Admin** and is backed by
[`engine/admin_rebuild.py`](../speciesviewer/engine/admin_rebuild.py); the menu
wiring is in [`gui/menuspec.py`](../speciesviewer/gui/menuspec.py).

It is metadata-only — it edits the two CSVs, and **never deletes or moves an
image file**. (Moving stray sidecars to trash is a separate action, *Clean
Orphan Sidecars*.)

## The two menu items

| Item | Destructive? | What it does |
|---|---|---|
| **Refresh Master Database — Review…** | No | Scans the `biota/` tree once and reports what a refresh *would* change. Always shows a fast summary; then optionally writes the full suggested CSVs + a black-image scan for inspection. Writes only into `admin/status/review_<timestamp>/`. |
| **Refresh Master Database — Apply…** | **Yes** | Backs up both CSVs to `admin/status/backup/`, then rewrites them with the reconcile. The only destructive Admin action. |

Non-debug builds show just these two. *Audit Species Counters* and *Clean Orphan
Sidecars* are separate maintenance items, shown only when debug is enabled.

## What the reconcile computes

`plan_rebuild` classifies every difference between the CSV admin and the tree:

- **REMOVE** — an admin row whose file exists nowhere on disk (a dead/ghost
  line). Removing it also drops any picture whose files are then all gone.
- **REPOINT** — an admin row whose file *does* exist, but at a different path
  than recorded (usually an old tree layout or a mangled separator string).
  The path is repaired, not deleted.
- **ADD** — a file on disk the admin doesn't list. **Reported only, never
  applied** — mostly DxO renders of existing pictures (`attach`) or stray
  exports (`junk?`).
- **SPECIES** — a picture whose folder says a different species than the admin;
  the folder is treated as ground truth.

`execute_heal` applies REMOVE + REPOINT + SPECIES. In **preview mode**
(`out_dir=`) it writes the healed CSVs to a folder instead of over the master,
so nothing is touched until you run Apply.

## Files written to `admin/status/review_<timestamp>/`

| File | Contents |
|---|---|
| `rebuild_remove.csv` | rows a refresh would delete |
| `rebuild_repoint.csv` | rows whose path would be repaired |
| `rebuild_add.csv` | files on disk missing from the admin (informational) |
| `rebuild_species_change.csv` | pictures whose folder ≠ admin species |
| `data_files.csv`, `data_pictures.csv` | the full **suggested** CSVs — diff these against `metadata/active/*` in Excel |
| `display_status.csv` | per-picture display resolution (the "black image" scan) |

## Black images — two causes

The `display_status.csv` scan resolves each picture the way the viewer does
(`master_root / (TargetPath or BestJPGPathName) / BestJPG`, else a RAW-thumb
fallback) and buckets it `jpg_ok` / `raw_fallback` / `black`. Investigating the
`black` set surfaced two independent causes:

1. **Dead-link phantoms** — the picture's files are genuinely gone (mostly old
   mangled-path imports). These are exactly the REMOVE set, so **Apply** clears
   them.
2. **Preview-tag mismatch (Nikon D90)** — the file is present but the extractor
   looked at the wrong embedded-JPEG tag. Old D90 NEFs carry no
   `PreviewImage`/`ThumbnailImage`; their JPEG sits under `OtherImage` /
   `JpgFromRaw`. Fixed in
   [`gui/preview_extract.py`](../speciesviewer/gui/preview_extract.py) by trying
   those tags largest-first. This is a code fix, independent of any data change.

## Safety and recovery

- **Review** changes nothing.
- **Apply** copies `data_files.csv` and `data_pictures.csv` into
  `admin/status/backup/<timestamp>_pre-prune_*` before writing. To undo, copy
  those two files back over `metadata/active/`.
- After Apply, run **Count Species** to refresh the per-species counters (the
  dropped phantoms leave them high until then); the master DB cache
  (`master.db`) rebuilds itself on next use.

See also [`config_state_files.md`](config_state_files.md) for the file layout
and [`data_structures.md`](data_structures.md) for the CSV schemas.
