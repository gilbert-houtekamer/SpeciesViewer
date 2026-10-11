# Multiple masters on one machine — plan

*0.4, step 5 prerequisite (backlog ch.20, "SpeciesViewer 0.4 — proposed order").
Written 2026-10-11. Status: step 1 SHIPPED 89550ee; step 2 built, awaiting test; steps 3–4 to do.*

## Why

Gilbert wants to keep his real master ("Main") and, beside it, one or two **test
masters** to try new features — and the first of those test masters is the
zero-data simulation (what a brand-new user sees). Switching must be explicit in
the GUI, and **Main's settings and data must be untouchable while a test master
is active**.

Doing this before "create a new master" gives that feature somewhere to live,
and gives the GUI something better than path fields in *File ▸ Settings*.

## The one idea

A master is not just one path. Today the machine config holds a *set* of things
that only make sense together:

| Belongs to the master | Where it lives today |
|---|---|
| master database + web root + read-only flag | `sv_config_<host>.ini` `[master]` |
| trips root, active trip | `[trip]` |
| albums root, active album | `[virttrip]` |
| website + upload account | `[websettings]` / site fields |
| recent trips, trip → picture-folder map, species LRU | `state/sv_gui_cache.json` |
| Days lane | `state/days_<host>.json` |
| SQLite cache + pending-writes log | `db/master.db`, `db/master.wal.jsonl` |
| batch handoff (`svconfig.ini`, last published URL) | `runtime/` |

**Where things live.** The master's *data* — pictures, CSVs, `admin/` — stays
wherever the user put it (Dropbox for Gilbert's real master, any folder for a
test master). Never in app-data. What each master gets in app-data is its own
**settings, remembered state and caches**: `config/`, `state/`, `db/` and
`runtime/`. Everything else stays shared: preview/fullres/sidecar caches and the
master EXIF cache (all keyed by file path, harmless to share), map tiles, logs.

```
%LOCALAPPDATA%\SpeciesViewer\
    masters.json                 ← which masters exist, which is active, which is main
    config\ state\ db\           ← the master that existed before this feature
    masters\
        Test new user\
            config\ state\ db\   ← every master created afterwards
    preview\ fullres\ ...        ← shared caches
```

**All masters are equal; "main" is a flag that moves.**

```json
{ "active": "Test new user",
  "masters": [
    {"name": "Main",          "folder": ".",                     "main": true},
    {"name": "Test new user", "folder": "masters/Test new user", "main": false}
  ] }
```

- An existing install gets one entry, "Main", whose `folder` is `.` — today's
  folders. **Nothing moves**, no migration.
- Exactly one master is main. Any master can be made main; the flag moves.
- The main master cannot be removed — make another one main first.
- A new user's path: start with a test master, later create a real one and mark
  it main. Folder location never decides which one is main.
- A master never opens another master's folder, so a test master cannot touch
  the main master's settings or data.

## Why a separate world, not a "master" field you change

Two concrete hazards in the current code if we only swapped the master path:

1. **Pending writes would land in the wrong master.** There is one
   `master.db` + `master.wal.jsonl`. `is_stale()` rebuilds when `master_root`
   changes — dropping, or worse replaying, writes queued for the *other* master.
   A per-master `db/` folder removes the case entirely (`MasterDB` already has a
   `cache_name` seam for exactly this).
2. **Publishing to the real website from a test master.** A separate config
   means a test master starts with empty site fields, and since 80d6c50 empty
   means "not set up" — publish is simply unavailable.

## How the active master is chosen

- `masters.json` as above; each master's `folder` is relative to the app-data folder.
- Read **once**, at the top of `utils/config.py`, before `CONFIG_DIR`/`STATE_DIR`
  are computed. Environment variable `SV_MASTER` overrides it.
- The GUI sets `SV_MASTER` in its own environment at start-up, so every batch
  subprocess it launches inherits the same master — even if `masters.json` is
  changed meanwhile.
- **Switching = restart.** The GUI caches Project state everywhere; a live
  switch would be a long tail of stale-state bugs. Switch writes `masters.json`
  and relaunches SpeciesViewer.

## GUI

A top-level **Master** menu (or *File ▸ Master* — see decision 1):

```
Master
  ● Main                         ← radio list, active one checked
  ○ Test new user
  ─────────────
  New test master…               ← name; "empty" (new-user simulation)
                                    or "copy machine settings from the main master"
  Make this the main master
  Rename / remove master…        ← the main one cannot be removed; remove = zip to .trash
  ─────────────
  Repository settings…           ← the existing dialog, moved here: it is about
                                    THIS master
```

Always visible which master you are in:
- **window title** gets the master name for every master that is not main
  (`SpeciesViewer 0.4 — TEST: Test new user`);
- an orange **strip** under the menu bar for a master that is not main AND
  whose publishing is not set up yet — an uncustomised test master. Once its
  upload server/account/folder are filled in, the strip goes (Gilbert,
  2026-10-11); the title still says TEST. **×** hides it for the session. The
  main master shows nothing — no change to today's look.

## "New test master…" — two kinds

| Kind | Starts with | Use |
|---|---|---|
| **Empty** | a `[Global]` stub only — exactly what a first run makes | the zero-data / new-user simulation (step 5) |
| **Copy machine settings** | the main master's `sv_config` *minus* `[master] [trip] [virttrip] [websettings]` and the site fields | trying a feature against a small test collection without re-entering DxO path, exiftool workers, window sizes |

Neither copies the main master's `state/` or `db/`.

## Steps — each one stands on its own

1. **Resolver only.** `masters.json` + `SV_MASTER` + per-master
   `CONFIG_DIR`/`STATE_DIR`/`db/`/`runtime/`. No GUI. Until the menu exists:
   `python -m utils.masters list | add NAME | use NAME`. An unknown name or an
   unreadable masters.json stops the start — never a silent fall-back to Main. Test: set
   `SV_MASTER=Test` from the command line, start, confirm the main master's files'
   mtimes are unchanged after a session. *Invariant logged at start-up:
   "Master: <name> — config <path>".*
2. **Visible:** window title + coloured strip.
3. **Master menu:** switch (restart), New test master (both kinds).
4. **Move Repository Settings** under it. Rename/remove last.

Then step 5 of the 0.4 order — the zero-data simulation — is: *Master ▸ New
test master ▸ Empty*, restart, and write down everything that breaks.

## Not in scope

- Creating a *real* new master collection on disk (the `admin/`, CSV skeleton) —
  that is the feature this plan makes room for; it comes after the simulation
  tells us what it needs.
- Live switching without restart.
- SmallMaster: unchanged; it is a property of a master's config (`readonly_locked`)
  and simply travels with it.

## Nothing here deletes

Remove-master zips its app-data folder to `.trash` first, in line with Delete
Album, and never touches the master's data folder. The main master cannot be
removed. Renaming changes only the display name; the folder keeps its name.

## Zero-data findings so far (input for 0.4 step 5)

Collected while making an empty test master start at all (2026-10-11):

1. **Crash: `KeyError: 'trip'`** in `MainWindow.__init__` — 41 places read
   `_config["section"]["key"]` directly. FIXED once, in `utils/config.py`: the
   core sections/keys always exist, empty meaning "not set up".
2. **Crash: `NoSectionError: 'repository'`** in `build_menu_spec` — with no
   master, `generate_svconfig_ini` writes no file and the menu read whatever
   `svconfig.ini` was on disk. Before per-master `runtime/` that was silently
   *another master's* file. FIXED with an empty fallback (same as "no trip").
3. Start-up warning *"Trip mode is selected but there is no 'Active Trip'
   panel to show…"* — meaningless to a new user. Open.
4. Start-up warning *"Publishing is not set up…"* — correct, and points at the
   right dialog. Keep.
