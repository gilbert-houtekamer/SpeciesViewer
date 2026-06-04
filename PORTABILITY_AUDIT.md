# Portability audit (Windows → Mac)

Read-only sweep done 2026-05-25 while on Windows, to head-start the MacBook
switch. Grouped by category, with file:line and the fix. The **qt/ app is
already largely portable** — the list is short.

Guidance for new code: see memory `cross-platform-avoid-windows-specific`.

---

## A. Windows-only "open file/folder" APIs — **DONE**

`qt/utils/open_path.py` is the shared helper (created commit f5c7154). All
three original blockers have been wired up:
- `qt/widgets/TripInfoPanel.py` — now calls `open_path(folder)` ✓
- `qt/widgets/VirtTripInfoPanel.py` — now calls `open_path(trip_dir)` ✓
- `bin/svu2avibase.py` — `os.startfile` guarded by `sys.platform.startswith("win")` ✓

## B. Hardcoded Windows paths in source

Low severity — mostly fallbacks and dev/test scaffolding, not shipped logic.
- **Live-app config fallbacks (Windows-style backslash):** `qt/gui/trip_process.py:40`
  and (legacy wx) `gui/dialogs.py:456` — `fallback="E:\\Dropbox\\naturedb"`.
  Only used when config is missing; switch to forward slashes / a neutral
  default. Real machine roots already live in the per-machine `.ini` (good).
- **FIXED:** `ghexif/triplib.py:158` — default `master_repository_disk` changed from
  `"c:/laptop/Zuid Afrika"` to `""` (blank).
- **Dev/test scaffolding** (hardcoded `C:/ D:/ E:/` in `if __name__` blocks; update
  when you want to run them on Mac): `qt/test_driver.py:9`,
  `qt/test_driver_all_csv.py:125`, `bin/run_*.py`, `bin/svu*.py`,
  `ghexif/sidecarlib.py:289`, `bin/svr_*.py`.

## C. External tool / binary dependencies — no code change, just install on Mac

- **exiftool** (called via `subprocess` in many places: `ghexif/exif_writer.py`,
  `ghexif/websupport.py`, `qt/engine/filepicture.py`, `qt/gui/image_viewer.py`,
  `qt/gui/rating_provider.py`, `qt/gui/preview_provider.py`, `bin/sv2ReadDirExif.py`).
  All invoke `exiftool` via PATH (no hardcoded `.exe`) — just `brew install exiftool`.
- **pillow-heif + Pillow** (HEIC decode): `qt/gui/image_viewer.py:723`,
  `qt/gui/preview_provider.py`, `qt/widgets/trip_widgets.py:8`. `pip install` on Mac.

## D. Already cross-platform-safe (good patterns — leave as-is)

- `qt/utils/config.py:7` — `platform.node().lower()` → per-machine `sv_config_<machine>.ini`;
  works on Mac and is exactly right for monitor-specific geometry.
- `qt/sharedlib/statemachine.py:240` — splits on `os.sep` (not hardcoded).
- `qt/gui/image_viewer.py:439-440` — probes both `.xmp` and `.XMP`; matters on
  Mac's optionally case-sensitive filesystem.
- **No `QSettings`/registry** in qt/ — dialog geometry & state live in the `.ini`
  (migrated earlier). Confirmed clean.
- `qt/gui/image_viewer.py:518` — `str(p).replace("\\", "/")` to match exiftool's
  forward-slash output: Windows-centric but a harmless no-op on Mac.

## E. New items found in 2026-06 sweep (code added since May 25)

### E1. DxO PhotoLab default path — medium priority (fix when Mac arrives)
- `qt/gui/large_image_view.py:47` — `_DEFAULT_DXO_PATH = r"C:\Program Files\DxO\..."`
- `qt/settings/globalsettings.py:28` — same string as `fallback=` in Settings dialog

  On Mac the DxO app bundle path will differ (e.g. `/Applications/DxO PhotoLab 9.app`).
  `launch_app_with_file()` already handles `.app` bundles via `open -a`. Fix: make the
  default `None`/`""` and let the user set it once in Global Settings on each machine.

### E2. Full-path `.lower()` in web-output code — low priority (fix before running website build on Mac)
- `ghexif/projectlib.py` lines 295, 334, 356, 382, 419, 445, 482, 508 — `os.path.join(...).lower()`
  used to build directory paths and HTML filenames.

  On case-sensitive macOS this lowercases the entire path including any uppercase in the
  master root. Harmless on case-insensitive Windows. Strip `.lower()` from path assembly;
  keep it only on the filename leaf where intentional normalisation is desired.

## F. Watch during the port (not enumerated — review on first Mac run)

- **Fonts / fixed pixel sizes**: many Qt stylesheets use fixed `px` font-sizes and
  widths. Qt's HiDPI handling usually scales them on Retina, but eyeball the
  dialogs once on the Mac.
- **Case-sensitivity**: extension matching uses `.lower()` widely (good); the
  `.xmp/.XMP` dual-probe is the main explicit spot. Watch any place that compares
  filenames for equality.
- **File encoding**: confirm `encoding="utf-8"` on CSV/XMP opens (present in the
  spots checked).

---
**Bottom line (updated 2026-06-04):** Section A is fully done. Remaining items:
install deps on Mac (section C), set DxO path in Global Settings (E1), strip
full-path `.lower()` in web build before running on Mac (E2), update test-script
paths when needed (B dev/test scaffolding).
