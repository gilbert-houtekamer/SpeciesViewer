# Configuration out of the repository root — plan (0.4, step 3)

Status: **DRY RUN — nothing moved yet.** Written 2026-10-10 on `SpeciesViewer04`.
Backlog: ch.20 "The first-run problem", ch.19 "Structural loose ends".
Checklist source: `docs/configuration_inventory.md` → Known irregularities #1, #2.

## The one job

Every per-machine file SpeciesViewer writes lives in the per-OS application-data
folder, never in the source tree. The source tree is then read-only at runtime,
which is what an installed build needs.

**Invariant:** after this work, starting SpeciesViewer and running a day's work
creates or changes no file under `D:\code\SpeciesViewer` (check: `git status`
shows nothing new, and no untracked file's date changes).

## Where things go

`utils/cache_paths.app_data_dir()` already exists and the caches, the log and the
runtime handoff files already use it:

| OS | app-data folder |
|---|---|
| Windows | `%LOCALAPPDATA%\SpeciesViewer` → `C:\Users\gilbe\AppData\Local\SpeciesViewer` |
| macOS | `~/Library/Application Support/SpeciesViewer` |
| Linux | `~/.local/share/SpeciesViewer` |

New subfolder: **`config/`** for the machine-scope files.

## What is in the repository root on BigBoy today (10 Oct)

| File | Last written | Scope | Plan |
|---|---|---|---|
| `sv_config_bigboy.ini` | today | machine — **has the SFTP password** | **A** move to `config/` |
| `days_bigboy.json` | today | machine | **A** move to `config/` |
| `sv_gui_cache.json` | yesterday | machine | **A** move to `config/` |
| `photolab_denoise.ini` | 23 Sep | machine | **A** move to `config/` |
| `svconfig.ini` | today | ephemeral — **has the SFTP password** | **B** → `runtime/` |
| `sysprint-recreatetrip.txt` | today, 286 KB | log residue | **C** see bug below |
| `Species print.txt` | yesterday, 302 KB | debug dump from Freeze | **C** → `logs/` |
| `studio.log` | 19 Aug | stale — the log moved to `logs/` already | **D** delete |
| `speciesviewer.log`, `SpeciesViewerLog.txt` | 2025 | stale | **D** delete |
| `trip_admin.csv`, `trip_admin.pre-fix-backup.csv` | 21 Aug | one-off recovery copies | **D** your call |
| `sv_config_smallboy.ini` | 2025 | SmallBoy's file, not read on BigBoy | **D** your call |
| `speciesviewer/sv_gui_cache.json`, `speciesviewer/svconfig.ini` | Aug | orphans (inventory #2) | **D** delete |

## Step A — machine config to `config/` (one commit)

1. `utils/config.py`: `CONFIG_DIR = app_data_dir() / "config"` (created if missing).
   `days_registry`, `gui_config` and `photolab_denoise` already build their paths
   from `CONFIG_DIR`, so they follow without further change; `work_backup` too.
2. **One-time migration, at the top of `utils/config.py`, before the ini is read.**
   For each of the four files: if it is missing in `config/` and present in the
   old root, **copy** it across, then **rename** the old one to
   `<name>.moved-to-appdata`. Nothing is deleted. The rename is so a hand-edit of
   the old file (you do edit the ini by hand, app closed) cannot silently go to a
   file nobody reads any more. Logged once.
3. If `config/` cannot be created (permissions), fall back to the old root and
   log a warning — a program that will not start is worse than one with its
   config in an awkward place (same rule `logger_support.log_path` uses).
4. `tools/svr_backfill_species_baseline.py` builds the ini path itself → point it
   at `CONFIG_PATH`.
5. **Settings ▸ Open settings folder** menu item, so the ini stays one click away
   for hand-edits. (Optional; small.)

Docs to update in the same commit: `configuration_inventory.md` (irregularities
#1, #2 closed; table locations), and the `.ini rules` note in memory.

**Test (you, F5):** start the app → title bar, last trip and last day are as
before; `%LOCALAPPDATA%\SpeciesViewer\config\` holds the four files; the root
holds four `*.moved-to-appdata`. Read & Summarize one day. Repeat on SmallBoy.

**Undo:** delete `config\`, rename the four files back. Nothing is lost.

## Step B — `svconfig.ini` to `runtime/` (one commit)

The batch handoff is written by `utils/batch_config.generate_svconfig_ini()` and
read back by name in ~15 places (`gui/batch.py`, `mainwindow.py`, `menuspec.py`,
`trip_process.py` ×5, …) — all as the bare relative `"svconfig.ini"`, i.e. the
working directory. One constant `SVCONFIG_PATH = runtime_path("svconfig.ini")`,
used by the writer and every reader. Batch scripts run from a command line keep
their `--config` argument, defaulting to the same constant. It carries the SFTP
password, so getting it out of the source tree also gets it away from `git add .`.

## Step C — log residue (one commit) — includes a real bug

**BUG:** `batch/svr8RecreateTripRepository.py` adds a `FileHandler` to the shared
`speciesviewer` logger **at import time** (line 41). Whenever that module is
imported, every log line of the whole app is also appended to
`sysprint-recreatetrip.txt` in the working directory — which is why it was
written today and is 286 KB. Fix: attach the handler only when the script runs
as `__main__`, writing to `logs/`. `Species print.txt` (sv6Freeze, projectlib)
moves to `logs/` as well.

## Step D — clean the root (deletes — your call, item by item)

The stale files in the table above. Proposed: delete the three stale logs and the
two orphans; you decide on the two `trip_admin` copies and `sv_config_smallboy.ini`.

## Not in this plan

Trip-, day- and master-scope files stay where they are — they travel with the
data by design. The `data/configs/` files tracked in git are a ch.20 "private
data sweep" item, handled separately.
