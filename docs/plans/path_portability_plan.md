# Path separators & cross-platform paths (Windows + Apple) — plan

Companion to `docs/plans/PORTABILITY_AUDIT.md`. Written
2026-07-07 ahead of the M5 Mac. Goal: make `/` vs `\` handling **deliberate and
consistent** so the same data and code run on Windows and macOS.

> Note: `PORTABILITY_AUDIT.md` predates the restructure — its `qt/…` / `ghexif/…`
> paths are now `speciesviewer/…`. This plan supersedes its separator/path items.

---

## 1. Reality check — separate the cosmetic from the functional

Two different things get lumped under "the `/ \` mess":

- **Cosmetic (NOT a bug):** mixed separators in a built path, e.g.
  `…\metadata/review/2026 test1\data_pictures.csv`. This comes from
  `os.path.join(base, "metadata/review", day)` where `base` uses `\` and the literal
  uses `/`. Python's `os.path.join` / `open()` accept `/` on Windows and `\`… is just
  native on Windows, so **these paths work on both OSes today.** They only look ugly
  in logs. Pervasive (e.g. `sv1MakeReposity.py`, `engine/projectlib.py:140-162`).

- **Functional (real Mac breakers):** paths that are **persisted, parsed, compared, or
  sent over the wire**. A stored Windows path with `\` and/or a drive letter is fine
  on Windows but wrong on macOS:
  - `Path("D:\\CurrentPictures\\x\\y.jpg")` on macOS is **one filename** containing
    backslashes — not a path. `.drive == ""`, `.name == "D:\\CurrentPictures\\x\\y.jpg"`.
  - This is exactly what `engine/handling.py` gets wrong: `relativize_path` (:10) and
    `resolve_path` (:21) both parse the stored value with the **OS-native `Path`**. On
    macOS a drive-letter row falls through as "relative" and gets joined onto the root
    → garbage. Every `WindowsFileName` read builds `Path(stored)` the same way
    (`engine/filepicture.py:138,174,218,291`, `engine/filecsv.py`, `db/master_db.py:393`,
    `engine/websupport.py:1246,1412,1524`).

**Conclusion:** the priority is the *persisted-path* layer, not the cosmetic joins.

## 2. Policy (the rule to bake in)

1. **One canonical form for anything stored / compared / on the wire: forward slash
   `/`, relative to a known root.** Web/SFTP already uses `/`; POSIX-native; Windows
   accepts it. The master already moves this way (`relativize_path` uses `.as_posix()`;
   [[project_small_master]] made `WindowsFileName` relative).
2. **Never call `Path()` / `os.path.*` directly on a *stored* value.** Route every
   read through one normalizer that (a) accepts `\` or `/` and an optional drive letter,
   (b) parses it as a Windows path regardless of host OS, (c) returns an OS-native
   absolute `Path`. Convert to the OS separator **only at the filesystem boundary**
   (open/stat/copy).
3. **Never hardcode `\` or drive letters in source.** Machine roots live in the
   per-machine `.ini` (already the pattern). New literals use `pathlib` or `/`.
4. **Case:** never `.lower()` a full path (macOS is case-sensitive) — normalise only
   the leaf where intended.

## 3. Shared helper (bake the rule in one place — QA-as-guardrails)

New `speciesviewer/utils/paths.py`:

```python
from pathlib import Path, PureWindowsPath, PurePosixPath

def to_fs_path(stored: str, root: str | None = None) -> Path:
    """Interpret a STORED path (may use \\ or /, may carry a drive letter or be
    /-relative) and return an OS-native absolute Path. Parses with PureWindowsPath
    so a Windows-authored value resolves on macOS too."""
    ...

def to_stored(p) -> str:
    """Canonical stored form: forward slashes, no OS-native surprises (as_posix)."""
    return PurePosixPath(str(p).replace("\\", "/")).as_posix()

def display_path(p) -> str:   # optional: native separators for logs/UI
    return os.path.normpath(str(p))
```

Then rewrite `handling.relativize_path` / `resolve_path` to parse with
`PureWindowsPath` (not host `Path`) so drive-letter detection works on macOS, and have
every `WindowsFileName` read go through `to_fs_path`.

## 4. Work items (prioritised)

**P1 — Mac blockers (persisted paths).** Do before the first real Mac run against
existing data.
- `engine/handling.py:10-29` — parse stored paths with `PureWindowsPath`; drive
  detection must not depend on host OS. Add `utils/paths.to_fs_path` and use it here.
- Route all `WindowsFileName` reads through the helper: `engine/filepicture.py`
  (138/174/218/291), `engine/filecsv.py:73`, `engine/handling.py:73`,
  `db/master_db.py:393`, `db/master_mutate.py`, `engine/websupport.py` (1246/1412/1524),
  `engine/admin_*.py`.
- Confirm **writes** always canonicalise to `/` (relativize on the master side already;
  check the trip-side CSV writers and any absolute `WindowsFileName` write).

**P2 — case-sensitivity + defaults.**
- `engine/projectlib.py` — strip full-path `.lower()` at 295/334/356/382/419/445/482/508
  (audit E2); keep `.lower()` only on the filename leaf. Website build on macOS depends
  on this.
- Windows-only default strings → blank/config: DxO `_DEFAULT_DXO_PATH`
  (`gui/large_image_view.py`, `settings/globalsettings.py`, audit E1); backslash config
  fallbacks (`gui/trip_process.py` `fallback="E:\\Dropbox\\naturedb"`; the same in
  `generate_virttrip_ini`). WP8's new `picture_root` already stores `/` + relative — good.

**P3 — cosmetic (optional, low priority).**
- Normalise **display/log** paths via `display_path()` so logs stop showing mixed
  separators (this is what looked wrong in the micro-trip log).
- Optionally split literal `"a/b"` segments in `os.path.join` calls into separate
  segments, or wrap outputs in `os.path.normpath`. Functionally unnecessary; do only if
  we want clean logs. Touches many `sv*`/`svv*` batch files — not worth a big sweep.

## 5. Verification

- **On Windows now (simulate macOS parsing):** unit-test `to_fs_path` /
  `relativize_path` / `resolve_path` with `PureWindowsPath` and `PurePosixPath` inputs —
  assert a `D:\a\b.jpg` row and a `/a/b.jpg` row both resolve correctly, and that the
  drive-letter branch does not depend on `os.name`. Add a case for a `\`-separated
  relative value.
- **Data probe:** scan a real master/trip `data_files.csv` for any `WindowsFileName`
  still carrying a drive letter or `\`; those are the rows that would break on macOS.
  Report count (review-over-autofix), then a one-time normalise pass if wanted.
- **On Mac (first run):** open a trip whose CSVs were authored on Windows; confirm
  pictures resolve (filepicture), the culling grid loads, and a website build produces
  correctly-cased directories.

## 5b. Data probe result (2026-07-07)

Ran the probe against the live master `E:\Dropbox\naturedb\metadata\active\data_files.csv`:
- **202,661** rows, **100% absolute drive-letter** (`E:/Dropbox/naturedb/…`),
  **0** `/`-relative. 47,632 rows still contain a `\` (mixed within the file).
- **Implication:** the master's own canonical CSV is NOT Mac-portable — none of
  those `E:/…` paths resolve without an `E:` drive. P1 (done) makes the CODE
  classify them correctly (as absolute, not mis-joined onto root) and makes the
  primary `master_root / TargetPath / filename` reconstruction the reliable Mac
  path — but a **one-time data relativization** (rewrite WindowsFileName +
  File:Directory to `/`-relative against master_root, via `relativize_path`) is
  the real fix, and belongs in its own reviewed migration (review-over-autofix,
  dry-run report → confirmed apply w/ backup). NOT part of P1.
- Trip-side `test1db` active CSV had 0 rows (fresh, uncommitted) — re-probe a
  committed trip.

## 6. Sequencing

- **Now (safe, Windows-verifiable):** build `utils/paths.py`, fix `handling.py` to parse
  with `PureWindowsPath`, add the unit tests, add the data-probe report. Zero behaviour
  change on Windows; unlocks macOS.
- **When the Mac lands:** P2 (case `.lower()`, DxO/default paths), then the on-Mac
  data-open + website-build checks.
- **Anytime / skip:** P3 cosmetic log normalisation.

Related: [[feedback_cross_platform]], [[project_portability_audit_todo]],
[[project_small_master]], [[project_trip_picture_root]] (WP8 already stores `/`+relative).
