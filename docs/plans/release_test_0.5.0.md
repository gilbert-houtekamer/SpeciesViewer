# 0.5.0 — release test on a clean machine

*For 2026-10-22. Goal: install the ZIP the way a new user would, on a Windows account
that has never seen SpeciesViewer, and walk one small trip from card to master. Every
oddity goes on the list at the bottom; small fixes go in, then one rebuild.*

## 0. Before you start (BigBoy, ~5 min)

1. Tell Claude "build the test package". It rebuilds `build/SpeciesViewer-0.4.2.dev-windows.zip`
   from the latest commit (~90 s; exiftool 13.59 included).
   *The version stays 0.4.2.dev during the test; it becomes 0.5.0 only when the test passes.*
2. Copy to a USB stick or `\\SMALLBOY\transfer`:
   - `build/SpeciesViewer-0.4.2.dev-windows.zip`
   - `build/SpeciesViewer-sample-pictures.zip` (10 Pantanal pictures with species, colours, GPS)
   - optional: one small SD card (a few dozen pictures) for the card-reading part.

## 1. The clean machine

**Laptop, new Windows user (chosen).** Settings ▸ Accounts ▸ Other users ▸ *Add account* ▸
"I don't have this person's sign-in information" ▸ *Add a user without a Microsoft account*,
e.g. `svtest`. Sign out, sign in as `svtest`.

- Why a new user: SpeciesViewer keeps its settings in `%LOCALAPPDATA%\SpeciesViewer` — per
  Windows user. Your own account would open your SmallMaster, not a first run.
- The laptop is 1440×900 at 200 %: also the test for dialog widths.

*Alternative: Windows Sandbox (`build/SpeciesViewer-sandbox.wsb` on BigBoy) — empty each
time, gone when closed.*

## 2. Install

1. Unzip `SpeciesViewer-0.4.2.dev-windows.zip` to e.g. `C:\Users\svtest\SpeciesViewer` —
   **not** under `D:\code`.
2. Start `SpeciesViewer.exe`. Expect *"Windows protected your PC"* → *More info* → *Run anyway*.
3. Check: splash fits; main window opens; title says 0.4.2.dev.

## 3. First run — Setup Checklist (owl)

| Step | Check |
|---|---|
| Setup Checklist opens | owl on the left; every line readable; no internal words |
| Create Master in an EMPTY folder, e.g. `C:\Users\svtest\SVtest` | the folder dialog does NOT open in the program folder |
| exiftool | found (bundled) |
| Website | choose **this computer** (local website); no server |
| Pictures folder (CurrentPictures) | a new folder, e.g. `C:\Users\svtest\Pictures\SV` |

## 4. One small trip

1. **Create Trip** (Trips ▸ Create Trip) — e.g. "Pantanal test".
2. **Read SD-card or Folder** — the unzipped sample pictures (or the SD card). Weaver on the
   left; Scan days; Read.
3. **Cull** in the day viewer: colours, a species name or two, one crop
   (try the second click on ✂ Cropping…).
4. **Read & Summarize** — log stays short; the GPS line is one line.
5. **Configure Blog Post** — type a text, press **Close** (it must be kept); set
   *Pictures per species* = 3; **Create Blog**.
6. **Publish Day** — macaws + the small turning wheel; then look at the local website.
7. **To Master ▸ Add to Master** — then open the Master Species Viewer.

## 5. Look around

- File ▸ Settings… (albatross, 7 tabs — do they fit on one row at 1440?)
- Help ▸ Connection status (woodpecker); Trip overview: Metadata backup / RAW backup (jays).
- Utilities ▸ Rebuild all Albums and the Master website (sandgrouse) — dry run only.
- The log file: `C:\Users\svtest\AppData\Local\SpeciesViewer\logs\studio.log`.

## 6. Afterwards

- Copy the svtest `studio.log` to `\\SMALLBOY\transfer` for Claude.
- Remove the test account (Settings ▸ Accounts ▸ Other users ▸ Remove) — that removes its
  SpeciesViewer settings too; or keep it for the next release.

## 7. Then (Claude)

Fix what the list says → rebuild → set version **0.5.0** in `speciesviewer/utils/version.py`
→ build the final ZIP → release notes (from the commits since 0.4.1) → tag `v0.5`.

## Found during the test

| # | Where | What | Fix now / later |
|---|---|---|---|
| 1 | | | |
