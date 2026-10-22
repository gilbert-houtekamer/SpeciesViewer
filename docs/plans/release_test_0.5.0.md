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
- The log file: `C:\Users\svtest\AppData\Local\SpeciesViewer\logs\speciesviewer.log`.

## 6. Afterwards

- Copy the svtest `speciesviewer.log` to `\\SMALLBOY\transfer` for Claude.
- Remove the test account (Settings ▸ Accounts ▸ Other users ▸ Remove) — that removes its
  SpeciesViewer settings too; or keep it for the next release.

## 7. Then (Claude)

Fix what the list says → rebuild → set version **0.5.0** in `speciesviewer/utils/version.py`
→ build the final ZIP → release notes (from the commits since 0.4.1) → tag `v0.5`.

## Found during the test

| # | Where | What | Fix now / later |
|---|---|---|---|
| 1 | Day viewer + Trip overview count after Reset Trip (also right after reading a card) | The movie is missing: 9 instead of 10. Culling mode never got video support (only the trip-data viewer did, 4cde8a3) | FIXED in the 09:09 ZIP, uncommitted — check: 10 cards, frame + play symbol, `v` plays, overview says 10 |
| 2 | Log after Reset | "SidecarCollection built: …" and "load_species_classification: …" reach the user log | FIXED (moved to debug), same ZIP |
| 3 | Editing in the viewer (packaged .exe only) | Black console windows flash; editing feels sluggish. Every exiftool run without CREATE_NO_WINDOW opens its own console window (invisible under F5) | FIXED: utils/no_window.run at 12 call sites — check: no flashes; OK on the laptop (responsive again) |
| 4 | To Master ▸ Publish Species Names | Feature: a picture, like the other dialogs | DONE: coati (assets/wait_coati.jpg), not yet in a ZIP |
| 5 | File ▸ Settings ▸ Global | Google Translate key only editable in the .ini; Preferred names label and list far apart | DONE: "Species names" box (preferred names + Google Translate key, hidden like the password); stretch above Save — layout OK on BigBoy. + eye in both hidden fields (gui.widgets.password_eye) — check F5 |
| 6 | Settings ▸ Global | "Species names" box read as if Google translates birds | DONE: "Species names and text translation" |
| 7 | Colour on a movie after Read & Summarize | "No HEIC/PNG to write rating/colour into" — a movie counted as HEIC-only; without an .xmp the colour was lost | DONE: movie never HEIC-only; its .xmp is created when missing (preview_provider) |
| 8 | Settings ▸ Species tab | Scope + name type at their defaults; name display duplicates Global | DONE: tab removed (speciessettings.py); readers keep their defaults; manual points to Global |
| 9 | Log after Reset with the day viewer open | 3× WARNING "could not persist summary: … data_files.csv" | DONE: Reset Trip first closes every picture window (Quick view, large view, grids, Rating Editor), without the re-run question |
| 10 | Log on Publish Day | "5 species with a picture" vs "8 species (8 new)" + "4 species have no JPG" — which number is the truth? | DONE: high = species named (RAW too), low = with a JPG. Result line now says both: "8 species on this trip (8 new this day); 5 with a picture on the trip website" |
| 11 | Log wording | WriteBlog:/Freeze: … on local copy on Windows/WriteCommit: … on repository/Keep old files…/rename active files…/BuildWebsite:/Sidecar cache:/(current-schema header)/Rated 44 … Species path biota/… | DONE (his review: e reworded "<name> (<sci>) with rating N is selected for the blog", f kept + reworded, q kept with time "Species pages updated in N s"; rest debug) |
| 12 | Trips ▸ Reset Trip dialog | Feature: a picture | DONE: toucan |
| 13 | Logs: Create Master + Read SD-card | developer text (".txt … needs adding to RAW in utils/raw_extensions.py", MakeReposity:, "0 sidecar(s)" next to "10 carried", flags line, TransCache, trip.ini written ×2, Species baseline froze 0 …) + Species print.txt written on every Publish headed "Debug enabled" | DONE (his OK): one "Not copied" warning in plain words; MakeReposity/flags/TransCache/trip index/skeleton/baseline → debug; "✅ Read complete: … N sidecar(s) came along … Time: 2 s for 1 day(s)."; "✅ Trip settings saved."; Species print.txt only with debug |
| 14 | Publish Day (trip) | The "jpg" gate picks species with a JPG, then copies ALL their files (ORF too) into the trip repo — the code comment says RAWs there are "pure bloat" | later (after 0.5.0); nothing lost |
| 15 | Every message | Should start with a capital ("northern nutcracker … is selected") | DONE: CapitalFirst log filter (INFO+), not for iPhone / c:\ |
| 16 | Blog log | "N species have no JPG yet" hides the minimum rating | DONE: both lines name the species; "no JPG yet, or its JPG is rated 0 (the minimum is N)" |
| 17 | Taskbar pin (F5 on BigBoy) | Pinned SV shows the Python icon | dev only (pins python.exe) — check: pin SpeciesViewer.exe on the laptop |
| 18 | Step dialogs | After a step, Run Step stays selected (Reset Trip, Merge Trip Species) | DONE: Close becomes the default on success (BatchModuleRunner) |
| 19 | Log | Indented "not validated:" lines not capitalised; no intro before the selected species | DONE: CapitalFirst skips indent; "Pictures selected for the blog (the minimum rating is N):" |
| 20 | Merge Pictures into Master, 2nd time (also after Reset + Publish) | 3 steps ran, 23 pairs of duplicate warnings + 10 "Level.ORDER … no identified JPGs" lines | DONE: Run first checks the master (engine/merge_check.py) → "Nothing to add", nothing runs; one duplicate summary; one "N species … no JPG" line; base_url warning quiet for a local site — laptop test |
| 21 | Log file name | studio.log (first-Qt-version name) | DONE: speciesviewer.log; old studio*.log archived + swept after 14 days |
| 22 | Merge again (laptop) | "Nothing to add" did not show: steps 1–3 were still green, so Run went to "Continue at step 4?" and skipped the check | DONE: check runs whenever the trip was merged before — retest |
| 23 | Start-up | 4 copies started by repeated double-clicks | DONE: utils/single_instance.py — a 2nd start brings the running one to the front; a master switch restart waits for the old one |
| 24 | NOTICE | Firefly illustrations based on his photographs — credit? | DONE: NOTICE.md "Illustrations": made with Adobe Firefly from his photographs, © him, GPL-3 (window pictures only; splash + icon are not Firefly) |

**Result (2026-10-22 evening):** all 24 findings fixed and checked on the laptop. Released as 0.5.0, tag `v0.5.0` (three parts, like v0.4.0 / v0.4.1). Left for later: #14.
