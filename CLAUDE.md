# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

SpeciesViewer: a PyQt6 desktop app (GPL-3, Python 3.12) for nature photographers. It takes
pictures from a memory card to a long-lived **master** collection organised by species: cull,
identify (GBIF taxonomy), geotag, write trip blogs, and optionally publish static websites
(local folder or SFTP). Data is plain folders + XMP sidecars + CSVs; SQLite is only a
per-machine read cache. Works with DxO PhotoLab sidecars (`.dop`) and ExifTool.

User docs: [README.md](README.md), [docs/README.md](docs/README.md) (index of all docs),
[docs/manual/](docs/manual/). **Read [docs/manual/appendix-c-data-architecture.md](docs/manual/appendix-c-data-architecture.md)
before touching anything that copies, moves or deletes pictures**, and
[appendix-e-data_structures.md](docs/manual/appendix-e-data_structures.md) for the four core CSVs.

## Commands

Run from the repository root.

```
pip install -r requirements.txt          # needs ExifTool installed separately (docs/install.md)
python speciesviewer/SpeciesViewer.py    # run the app (user usually runs it via VS Code F5)
python -m pytest                         # smoke + unit tests (tests/ only, see pytest.ini)
python -m pytest tests/test_countries.py::test_name   # single test
python tools/sv_card_empty_selftest.py   # hand-run self-test: emptying a card
python tools/svr_extension_audit.py      # invariant: RAW extensions live in ONE table
python tools/svr_manual_build.py --open  # build the reference manual into one HTML page
python tools/svr_build_zip.py            # PyInstaller one-folder build + pinned exiftool → build/
```

Running a single module ad hoc: put `speciesviewer/` on the path, e.g.
`PYTHONPATH=speciesviewer python -c "import engine.countries"`.

## Import convention

`speciesviewer/` is **not** imported as a package. `SpeciesViewer.py` (and `tests/conftest.py`)
insert `speciesviewer/` onto `sys.path`, so its subfolders are top-level packages:
`import engine.triplib`, `from utils.config import _config`, `from gui.batch import …`.
Never write `from speciesviewer.engine …`.

## Layout (`speciesviewer/`)

| Folder | Role |
|---|---|
| `SpeciesViewer.py` | Entry point: splash, warms the master SQLite cache on a thread, opens `gui.mainwindow`. |
| `gui/` | Qt windows, dialogs, viewers. `menuspec.py` wires menus to dialogs/batch modules; `batch.py` runs batch modules on worker threads (`BatchModuleRunner`, `ChainedBatchRunner`, `ColumnBatchRunner`); `mainwindow.py`; viewers (`rating_editor.py`, `large_image_view.py`, `biota_browser.py`, …); `theme.py` QSS. |
| `engine/` | Non-GUI logic: trips (`triplib`, `projectlib`), species/taxonomy (`specieslib`, `speciesclassification`, `taxon_order`, `subspecies`), sidecars/EXIF (`sidecarlib`, `exifcache`, `exif_writer`), web (`websupport`, `webmap`, `weburl`, `sftplib`, `localsite`), pipeline state (`statemachine`), deletes/trash (`force_delete`, `trip_trash`, `master_purge`). |
| `batch/` | Pipeline steps. Each module exposes `main(args, config)` and can also run as a script. Prefixes: `sv0–sv7` = per-day/trip pipeline, `svr*` = repair/admin, `svu*` = utilities, `svv*` = albums (virtual trips). |
| `db/` | `master_db.py`: SQLite **read cache** of the master CSVs, under app-data, rebuilt when CSV mtimes change. `master_mutate.py`, `master_health.py`. |
| `utils/` | Config and paths: `config.py` (`_config`), `ini_io.py`, `cache_paths.py`, `masters.py` (multiple masters), `raw_extensions.py`, `version.py` (single source of `__version__`). |
| `settings/`, `dialogs/`, `widgets/` | Settings window pages, About/settings dialogs, reusable panels. |
| `HTMLtemplates/`, `assets/` | Website templates and icons/images shipped with the app. |
| `dist/installation files/` | Shipped data, e.g. `initial_speciescollection.csv` (starter species list). |
| `one-off/`, `batch-future/` | Not part of the program; excluded from the smoke test. |

Other top-level: `tests/` (pytest), `tools/` (`svr_*` maintenance/audit scripts, many with
`--dry-run`; `SpeciesViewer.spec` for PyInstaller), `docs/` (manual, plans in `docs/plans/`,
backlog HTML), `build/` (git-ignored output).

## Core concepts

- **Four repositories**: local working pictures (`CurrentPictures/<year>/<trip>/<day>`) →
  **trip repo** (`trips/<trip>db`, transient, website files only) → **master** (`naturedb`,
  the one long-lived copy, canonical CSVs) → **album / virttrip** (`virttrips/<name>`, no
  pictures, a date-range query over the master). Deleting local originals is always the
  **last** step and only against size-matched master copies.
- **Pipeline + state machine**: `engine/statemachine.py` tracks per-day steps
  (MakeRepository → ReadDirExif → WritePictures → WriteBlog → Freeze → WriteCommit) in a
  `statemachine.ini`; re-running an earlier step invalidates later ones. sv5 BuildWebsite is
  folded into Freeze. A step that had nothing to do returns `nothing_to_do(reason)`.
- **Master CSVs are canonical** (`data_pictures.csv`, `data_files.csv`, species collection);
  SQLite (`db/master_db.py`) and all caches under `%LOCALAPPDATA%/SpeciesViewer/` are
  rebuildable and must never live in Dropbox.
- **Per-user settings** live in `%LOCALAPPDATA%/SpeciesViewer/config` (macOS/Linux
  equivalents via `utils/cache_paths.py`), not in the repo. See
  [docs/configuration_inventory.md](docs/configuration_inventory.md) for where a new setting belongs.

## Hard rules

- **Data safety first.** Never write a master CSV down to 0 rows; never overwrite a source
  picture or sidecar with a derived file; deletes go to a `.trash` folder first, and any
  feature that deletes must say exactly what goes. Failed writes must be reported (ExifTool
  can return 0 on a refused write — verify). Bugs historically cluster in remove/re-point
  code paths, not append paths.
- **`.ini` files**: always read with `utils.ini_io.read_ini` (UTF-8 with legacy fallback);
  write UTF-8. Never commit `.ini` files or other runtime artifacts.
- **Tests never touch real config.** `tests/conftest.py` redirects `LOCALAPPDATA`/`HOME` to a
  temp sandbox and sets `QT_QPA_PLATFORM=offscreen`. Headless GUI tests must also stub
  anything that persists config (`persist_config`, `set_setting`, …).
- **Cross-platform**: `pathlib`, no drive letters or registry; persisted paths parsed with
  the host's `Path`. Web hrefs are built as URLs, never with `os.path.join`.
- **CSV columns**: new columns are appended at the END (frozen CSVs are re-headed by position).
- **PhotoLab `.dop` interface is frozen**: never invent a `.dop`; change the interface only
  with a strong case.
- **pygbif is pinned to 0.6.6**, `paramiko<4` (pysftp) — don't bump casually.
- **Qt traps**: keep a Python reference to installed event filters; never `QTextEdit(text)`
  (use `setPlainText`); never `hide()` a dialog running `exec()`; long labels in dialogs go
  through `gui.widgets.wrapped()` (check `minimumSizeHint()` for width blow-ups); on a map
  scene use `_bubble_at()`, not `itemAt()`.
- **Accessibility**: no Ctrl/Shift + mouse interactions. Multi-select via context-menu toggle
  or an "Add to selection" item; prefer context menus with a keyboard shortcut.

## Style

- Match surrounding code. Modules typically open with a docstring stating their **ONE JOB**
  and the **INVARIANT** they keep; keep functions small and independent.
- Logging: `logger = logging.getLogger("speciesviewer")`.
- User-facing text: plain words, no internal names (no "virttrip", "sv6", file names), written
  *to* the user. In the UI a virtual trip is an **Album**.
- Prefer dry-run → review → apply for any bulk repair tool.
- Efficient at scale: the master holds ~85k pictures / ~178k files — batch and cache, avoid
  per-row ExifTool calls (use the shared stay-open ExifTool).

## Git workflow

- `main` = last release (0.5.0). Day-to-day work is on branch **`SpeciesViewer04`**
  (version `0.5.1.dev` in `utils/version.py`).
- Commit only after the user has tested and confirmed the change. Stage files by name (the
  tree has many untracked images/notes). Conventional-style messages:
  `fix(viewer): …`, `feat(translate): …`. Pass multi-line messages via heredoc to
  `git commit -F -`.
