# 9. Help

Four items: the **Reference Manual** (this manual), the **Setup Checklist**
(what SpeciesViewer needs, and why a menu is grey), **Connection Status** (what
works offline and what does not), and **About**. None of them needs anything to
be set up first.

---

## Reference Manual — `F1`

Opens this manual as one page in your web browser: a sidebar with every chapter
and section, and a filter box to find a menu item by name. It comes with the
program, so it works without internet. To print it or save it as a PDF, use the
browser's *Print* — each chapter starts on a new page.

## Setup Checklist…

What SpeciesViewer needs, and what is optional — one row per setting, each with
a mark and a button that opens the dialog for it. The marks are read from the
real settings every time, so the window also answers *"why is this menu grey?"*
long after day one. It opens by itself at start when one of the three rows
everything depends on is missing — the master collection, where the website
goes, or exiftool. That happens the first time you start SpeciesViewer, and in
a new master.

| Row | Needed for | Mark when missing |
|---|---|---|
| Master collection | everything — create one with *Master ▸ Masters ▸ Create Master Collection…*, or **Choose…** an existing one | ✗ |
| Website | species pages and blogs — choose *on this computer* or *on your web server* | ✗ |
| Pictures folder | unfiled days, trips — and the GPS-log folder inside it | ✗ |
| Trips folder | trips | ✗ |
| Albums folder | albums | ✗ |
| exiftool | reading pictures — found on the PATH, or **Locate…** a copy (download from exiftool.org) | ✗ |
| Decode Quality | the 100% view; works on defaults | · |
| DxO PhotoLab | editing; optional | – |
| AI models | culling advice and species suggestions; optional — **Download…** opens [AI Models](01-file.md#ai-models) | – |

A row that is not set at all gets a **Use suggested** button (**Create
suggested** for the master): one click makes the folder and uses it. The
suggestions are `C:\SpeciesViewer\nature`, `…\trips`, `…\CurrentPictures` and
`…\albums` on Windows, and `~/SpeciesViewer/…` on the Mac. A row that is set
but whose folder is missing — an unplugged drive — gets no such button: that is
fixed by plugging the drive in, not by pointing somewhere else. In a master
other than the main one, the suggestions sit in that master's own folder
(`C:\SpeciesViewer\<master>\trips` and so on).

The folders for pictures, trips and albums are not needed to start; each only
unlocks its own menus. The website needs no server either: *on this computer*
is enough.

---

## Connection Status…

Shows whether the services SpeciesViewer depends on are reachable, one lamp
each: the **publish server** (where uploads go), **species lookup** (GBIF),
**place names** (OpenStreetMap) and **your website** (where the pages are
viewed). With the website on this computer, the server lamps do not matter.

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

The version, what SpeciesViewer is, and **Licences and credits…** — the
licence (GPL-3), the programs and libraries it uses, and where the pictures in
its dialogs come from.
