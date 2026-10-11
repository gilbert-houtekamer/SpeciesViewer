# 9. Help

Three items. Small, but the first two earn their place.

| Item | Needs |
|---|---|
| Setup Checklist… | |
| Connection status… | |
| About | |

---

## Setup Checklist…

What SpeciesViewer needs, and what is optional — one row per setting, each with
a mark and a button that opens the dialog for it. The marks are read from the
real settings every time, so the window also answers *"why is this menu grey?"*
long after day one. It opens by itself when the open master has no collection
yet — the first time you start SpeciesViewer, or in a new master without one.

| Row | Needed for | Mark when missing |
|---|---|---|
| Master collection | everything — create one with *Master ▸ Masters ▸ Create Master Collection…*, or **Choose…** an existing one | ✗ |
| Website publishing | publishing blogs and pages; optional | – |
| Pictures folder | unfiled days, trips — and the GPS-log folder inside it | ✗ |
| Trips folder | trips | ✗ |
| Albums folder | albums | ✗ |
| exiftool | reading pictures — found on the PATH, or **Locate…** a copy (download from exiftool.org) | ✗ |
| Decode Quality | the 100% view; works on defaults | · |
| DxO PhotoLab | editing; optional | – |

With only the master collection set up you can already copy days off a card,
add them to the master and browse it — no website needed.

---

## Connection status…

Shows whether the services SpeciesViewer depends on are reachable, as a set of
status lamps.

This matters more than it sounds, because the application is designed to work
**offline** and degrade in specific ways rather than fail:

- **Publishing** gates on SFTP. If the site is unreachable, the steps that upload
  cannot complete — and it is much better to know that before running a full
  website rebuild.
- **Species name resolution** (GBIF) keeps working offline for names already
  known locally. New names simply stay unidentified until you are back online, at
  which point re-running the step resolves them. The lookup times out rather than
  hanging.

So the useful reading of this dialog is: *what will silently not happen if I run
the pipeline right now?*

---

## About

Version and build information.
