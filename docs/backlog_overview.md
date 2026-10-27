# Backlog overview — what is still open

*Made 27 Oct 2026 from `SpeciesViewer_Backlog.html` (ch. 1–24), checked against the code
on SpeciesViewer04. The HTML stays the full record; this is the short list to choose from.*

Size: **S** = an hour or two · **M** = a day · **L** = several days or a design decision first.

Madagascar starts 28 Oct: everything here is for **after the trip**.

---

## 1. Bugs and data that is wrong

| # | What | Size | Notes |
|---|------|------|-------|
| 1.1 | **Two wrong coordinates found by the coarse-GPS scan** (ch. 2) — a South Africa photo tagged Country = Spain; Galapagos 2015 reusing one coordinate on many pictures. The other 789 of the 791: report first, decide later. | S / M | Wrong maps on the site |
| 1.2 | **Trip dates and the days it holds can disagree** (ch. 17, NEW) — nothing warns; Retire to Album then finds no pictures. A “Review the date range” check. | S | Silent until it bites |
| 1.3 | Read-Card dialog shrinks after Scan days but isn't re-centred (ch. 1). | S | Cosmetic |

## 2. Built, waiting for a live test

| # | What | Size |
|---|------|------|
| 2.1 | **Local names** — the laptop → BigBoy round trip (ch. 6). Planned late November. | S |
| 2.2 | **Trip/Album Overview parity** — GPS column, lock glyph, Publish/Reset buttons, Write All Blogs, shared blog editor (ch. 15). | S |
| 2.3 | **🛰️ row action on the Days panel** — double-click the cell; the review dialog should open on that day (ch. 17). | S |

## 3. Small features (S–M)

| # | What | Chapter |
|---|------|---------|
| 3.1 | **“Local name…” in the pipeline Species Editor** too (it only has Yes/No for an unknown name). | 6 |
| 3.2 | **“All N files from this card are verified present”** — one closing line; the checksums already exist. | 18 |
| 3.3 | **Master Species Viewer: search like the Biota Browser** (part of an NL/EN/scientific name, every match with its count). | 3 |
| 3.4 | **AVI lists: choose the sheet** of a workbook with several sheets. | 9 |
| 3.5 | Blog index rewritten when the trip banner changes, for a trip with no days yet (parked 27 Oct). | 7 |
| 3.6 | Filter a viewer by camera (wishlist; typing `img` in Find file works today). | 9 |

## 4. Bigger features and decisions (L)

| # | What | Chapter |
|---|------|---------|
| 4.1 | **Decide how capture time is stored** — A (camera's own, today), B (normalise to local), C (add a UTC column). Touches sidecars, data_files, website and master; the backlog leans to C. | 17 |
| 4.2 | **Create JPG size for a RAW** — 3200–8640 px depending on the body; resize like the others? NEEDS DECISION. | 7 |
| 4.3 | **Count Species for an Album** — the trip report (species × days, colours, the export) plus the no-JPG flag. | 2 |
| 4.4 | **Wikipedia beside the species** — a link (or summary) on the species page and in the app, EN and NL. Link free; quoting needs attribution (CC BY-SA). | 6 |
| 4.5 | **Aliases elsewhere** — reading a tour operator's list, the waarneming.nl import, search by a remembered name. | 6 |
| 4.6 | **Another language *instead of* Dutch** — English stays; the second language becomes the user's own (French, German, …), so the program suits every user. Design together with the species list (ch. 6). | 24 |

## 5. Refactors

| # | What | Size |
|---|------|------|
| 5.1 | **Split `gui/trip_process.py`** — 6,728 lines; first move the Read-SD-card dialog out. The only open item left in ch. 19. | M |
| 5.2 | Mac: the default PhotoLab path is a Windows `.exe`; `projectlib.py` lowercases whole paths for website output (wrong on a case-sensitive disk); relativise the master's stored paths. | M — waits for the Mac |
| 5.3 | `rating_editor.py` (4,756) and `map_view.py` (3,264) — leave unless working in them hurts. | watch only |

## 6. Documentation

- **Capabilities Added**: still no write-up for ch. 17–22.

---

### Suggested order after the trip
1. **2.1–2.3** — three quick live tests.
2. **1.1, 1.2** — wrong data and a silent trap; both small.
3. **3.1, 3.2** — quick wins.
4. **4.1** — the one big decision; everything about time builds on it.
5. **5.1** — the refactor that pays back most.

---

*Done since the first version of this list (27 Oct): the SFTP password is changed; the laptop
rehearsal ran (card → trip → export → BigBoy → published, the nutcracker); the garbled species
names are gone from the master (0 left in species.csv, speciescollection.csv, data_pictures.csv);
the taskbar icon stays. In the HTML: ch. 19 brought up to date (only the `trip_process.py` split
is open), and the ten fixed items still tagged BUG are now tagged FIXED.*
