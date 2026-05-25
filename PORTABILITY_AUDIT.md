# Portability audit (Windows → Mac)

Read-only sweep done 2026-05-25 while on Windows, to head-start the MacBook
switch. Grouped by category, with file:line and the fix. The **qt/ app is
already largely portable** — the list is short.

Guidance for new code: see memory `cross-platform-avoid-windows-specific`.

---

## A. Windows-only "open file/folder" APIs — MUST change (live app)

These are the only hard blockers in the running app. Recommend one shared
helper, e.g. `utils/open_path.py`:
```python
import sys, os, subprocess
def open_path(path):
    if sys.platform.startswith("win"): os.startfile(path)          # noqa
    elif sys.platform == "darwin":     subprocess.Popen(["open", str(path)])
    else:                              subprocess.Popen(["xdg-open", str(path)])
```
Then replace:
- `qt/widgets/TripInfoPanel.py:118` — `subprocess.Popen(f'explorer "{folder}"')`
- `qt/widgets/VirtTripInfoPanel.py:134` — `subprocess.Popen(f'explorer "{trip_dir}"')`
- `bin/svu2avibase.py:587` — `os.startfile(xlsx_path)` (batch script)

## B. Hardcoded Windows paths in source

Low severity — mostly fallbacks and dev/test scaffolding, not shipped logic.
- **Live-app config fallbacks (Windows-style backslash):** `qt/gui/trip_process.py:40`
  and (legacy wx) `gui/dialogs.py:456` — `fallback="E:\\Dropbox\\naturedb"`.
  Only used when config is missing; switch to forward slashes / a neutral
  default. Real machine roots already live in the per-machine `.ini` (good).
- **Verify:** `ghexif/triplib.py:158` sets `master_repository_disk = "c:/laptop/Zuid Afrika"`
  — confirm this is a test default, not a value written into live config.
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

## E. Watch during the port (not enumerated — review on first Mac run)

- **Fonts / fixed pixel sizes**: many Qt stylesheets use fixed `px` font-sizes and
  widths. Qt's HiDPI handling usually scales them on Retina, but eyeball the
  dialogs once on the Mac.
- **Case-sensitivity**: extension matching uses `.lower()` widely (good); the
  `.xmp/.XMP` dual-probe is the main explicit spot. Watch any place that compares
  filenames for equality.
- **File encoding**: confirm `encoding="utf-8"` on CSV/XMP opens (present in the
  spots checked).

---
**Bottom line:** the only must-fix-before-Mac items are the 3 open-file/folder
calls in section A (one small helper covers all). Everything else is install
dependencies (C) or low-priority cleanup (B).
