# Trip naming — one name, everything else suggested

*Opened 2026-10-12 (Gilbert's proposal, reviewed together). Status: BUILT
2026-10-12 (uncommitted, awaiting Gilbert's test) — decisions at the end.*

## Why

"We need to think really hard about how things are called and created. Even as
a developer it is completely unclear how I need to call entities. A user will
run away." (Gilbert, 2026-10-12)

Today a new trip asks for a trip root folder, a separate "New trip folder"
(`e.g. kretadb`), a web root, and a pictures folder — four names the user must
invent and keep consistent, while trip_name, trip_id and web_slug are cut from
the web root behind the scenes. The test trips najaar/autumn showed that even
the developer gets them crossed, and "Create Folder" with an empty Trips folder
put a trip in the source tree.

**Principle: the recommendation is the default.** Seeing the suggested names
tells the user what to do (e.g. "add a space in the pictures folder name").

## 1. Three folders, asked once

Asked in the **Setup Checklist** (rows already exist for Master collection,
Pictures folder and Trips folder; the Albums folder follows the same pattern).

| Folder | Gilbert's machine | Default, Windows | Default, Mac | Web address |
|---|---|---|---|---|
| Master collection | `E:\Dropbox\nature` | `C:\SpeciesViewer\nature` | `~/SpeciesViewer/nature` | `/nature` |
| Trips folder (anchor) | `E:\Dropbox\trips` | `C:\SpeciesViewer\trips` | `~/SpeciesViewer/trips` | `/trips` |
| Pictures folder | `D:\CurrentPictures` | `C:\SpeciesViewer\CurrentPictures` | `~/SpeciesViewer/CurrentPictures` | — |

- The web addresses `/nature` and `/trips` are **fixed in code**, not asked:
  the site layout already assumes them, and a field with only one sensible
  answer makes people doubt themselves. An override, if ever needed, goes to
  an Advanced setting.
- Defaults are platform specific (pathlib; no drive letter on the Mac).
- Trips anchor: `C:\SpeciesViewer\trips`, not `C:\SpeciesViewer` itself
  (decided 2026-10-12).

## 2. One trip name drives everything

Example: name `madagascar`, start date 2026-11-02.

| What | Suggested | Label in the dialog |
|---|---|---|
| Trip data | `<Trips folder>\madagascardb` | "SpeciesViewer's files for this trip" |
| Raw pictures | `<Pictures folder>\2026\2026 madagascar` | "Your pictures (the day folders go inside)" |
| Web address | `/trips/madagascar` | shown as the full link (see §4) |
| Titles NL / EN | `Madagascar` | editable on the Trip Text tab |

Rules:

1. **Year from the trip's start date**, not today: a trip planned now for
   January 2027 goes in `2027`. So the dates move to the first tab.
2. **Web link cleaned from the name**: lowercase, accents dropped, spaces →
   `-` ("Costa Rica" → `costa-rica`, "Réunion" → `reunion`). The folder names
   keep what the user typed.
3. **Each field follows the name until you edit it**; then it stops
   following, and a small "↺ suggested" puts the suggestion back.
4. trip_name and web_slug are both the cleaned web name — one value, not two
   that can drift. trip_id stays the data folder's name (`madagascardb`): it is
   how SpeciesViewer finds the folder. **For NEW trips only:** existing trips do not
   always meet this (Gilbert, 2026-10-12) and keep their values — their web
   slug can never change, and their folder names are what they are.

## 3. Checks before Create, not after

| Situation | Shown as | Create allowed? |
|---|---|---|
| Folder does not exist yet | "will be created" | yes |
| Trip data folder exists | red: "already exists — choose another name" | no |
| Web address already in the trip index | red: "already used — a web address can never change later" | no |
| Pictures folder exists | **warning**: "exists, 12 day folders — this trip will use them" | yes |
| Pictures folder inside another trip's pictures folder | warning | yes |

The pictures folder often exists first (cards copied before the trip is made),
so that is a warning, never a block.

**One "Create Trip" button** makes every folder that is marked "will be
created". The separate "Create Folder" button goes.

## 4. The web address shown

- Website on a server: `https://houtekamer.com/trips/madagascar`.
- Website on this computer: `http://localhost:8765/trips/madagascar`,
  labelled "Your website (on this computer)". See "Local name" below.
- No website: the line reads "Web address — when you set up a website".

### Local name ("LocalSV")

Gilbert asked for a symbolic name instead of `127.0.0.1`. Options:

| Option | Looks like | Works | Cost |
|---|---|---|---|
| **`localhost` (recommended)** | `http://localhost:8765/` | every browser, every OS | none |
| `sv.localhost` | `http://sv.localhost:8765/` | Chrome, Edge, Firefox; not sure on Safari | needs a test on the Mac |
| Own name via the hosts file (`localsv`) | `http://localsv:8765/` | everywhere | admin rights to edit a system file — not for users |

The `:8765` stays: port 80 needs admin rights on the Mac and is often taken
on Windows.

## 5. Update mode

For an existing trip the same tab shows the names as facts, greyed: the web
address can never change (see Manage Trip Publication); the folders change
through Settings only.

## 6. Sketch — first tab, create mode

```
Trip name   [ madagascar          ]       Dates [2026-11-02] – [2026-11-20]

  SpeciesViewer's files   E:\Dropbox\trips\madagascardb             will be created
  Your pictures           D:\CurrentPictures\2026\[2026 madagascar]  ⚠ exists, 0 day folders
  Web address             https://houtekamer.com/trips/madagascar   ✓ free

                                                           [ Create Trip ]
```

## Not in this plan

- Albums: the same naming problem; copy this pattern once Trips is right.
- Renaming an existing trip.
- **Deleting a (test) trip is hard** (Gilbert, 2026-10-12): with its folder
  removed by hand, `madagascar` stayed on the website's trip list and kept
  its web name taken. The existing delete is for a trip AFTER it has been
  moved to the master; this one is a **force delete** of a trip that never
  got that far (a test, a mistake): the folder, the trip-list row and the
  published pages together. Its own plan. The clash
  message now at least says WHERE a name is used.

## Decided (2026-10-12)

1. Trips anchor default: `C:\SpeciesViewer	rips` / `~/SpeciesViewer/trips`.
2. Local name: `localhost` — the `:8765` is needed anyway, so a fancier name
   buys nothing.
3. Web link for multi-word names: `costa-rica`.

## Built (2026-10-12)

- `engine/trip_naming.py` — the rules (web name, suggestions, clash check
  over trips, albums and the trip index). No Qt.
- `utils/default_folders.py` — platform defaults.
- `gui/trip_name_panel.py` — Create Trip's first tab "Name & Folders"; the
  "Create Folder" button is gone. Update mode unchanged apart from texts.
- Setup Checklist: "Use suggested" (folders) and "Create suggested" (master),
  shown only when nothing is set.
- Local website address: `http://localhost:8765`.

### After Gilbert's first test (2026-10-12)

- Create Trip copies the default `tripimage.jpg` and `tripbanner.jpg` (terns)
  into the trip and fills both fields — the blog and the index use them, and
  they are the files to replace later. An empty banner elsewhere also falls
  back to the terns (the tropicbird is the master's).
- Check Range sits on the first tab when creating; a trip or album is no
  longer listed as overlapping itself (also in Update and in Albums).
- After a successful Create the button reads "Created ✓" and the name panel
  locks — a second press used to answer "already exists".
- Ctrl+K / right-click "Insert link…" in the trip and album descriptions.
- Second test: placeholder "e.g. Spain"; the clash message names where the
  web name is used (a trip folder, an album, or the website's trip list); a
  new master writes an EMPTY species baseline (every species is new) instead
  of refusing; the Master URL in the side rail is a link to the website.
- Side rail: the Trips/Albums counts no longer count the "(No … found)" row.
