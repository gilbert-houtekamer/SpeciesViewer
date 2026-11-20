# Backlog overview — what is still open

*Made 27 Oct 2026 from `SpeciesViewer_Backlog.html` (ch. 1–24), checked against the code
on SpeciesViewer04; completed items removed 19 Nov 2026. The HTML stays the full record;
this is the short list to choose from. Item numbers are kept, so old references still fit.*

Size: **S** = an hour or two · **M** = a day · **L** = several days or a design decision first.

---

## 1. Found while writing the manual (19 Nov)

Writing the manual means describing the program as a user meets it; these are the places
where the description came out awkward. None loses data; each makes the program harder
to understand than it needs to be.

| # | What | Size |
|---|------|------|
| 1.1 | **A local name can't be seen or removed.** Edit Species List doesn't show local names, so a wrong one stays for good. | S–M |
| 1.2 | **Double-clicking a row number in the Trip panel re-runs that day's completed steps.** Nothing on screen hints at it — hidden power, easy to trigger by accident. | S |
| 1.3 | **Restoring or permanently deleting master pictures sits in Settings ▸ Database**, besides the Master menu. Settings is an odd place for a step that deletes pictures for good. | S |
| 1.4 | **Look-alike names:** *Delete Trip…* vs *Delete Trip Completely…*; *Count Species* vs *Recount Species Counters*. | S |
| 1.5 | **Uneven menu wording:** "Manage caches…" next to "Manage Site Publication…"; two Utilities items end in a full stop. | S |
| 1.6 | **Ungated album items:** Manage Album Publication and Album Species Viewer can be clicked with no album open (open since August). | S |
| 1.7 | **Keep landscapes on this computer.** Delete Local Files deletes a local landscape once the master holds it; for a photobook you want them beside the day's holiday pictures. **Decided 20 Nov:** a tick *Keep landscapes*, **on by default** — the master keeps its copy, so nothing is at risk. | S |
| 1.8 | **Rescan master EXIF cache fills a cache nothing reads.** Only Manage caches shows its size. Remove the menu item and the cache file. | S |
| 1.9 | **Maintenance is scattered** — over Master ▸ Maintain Master Database, the Master menu, Settings ▸ Database, Utilities and Developer Tools. Most items are not maintenance but **repairs for something that crept in**. Proposal (with 1.3 and 1.8), sorted by what the user is doing: **(a) normal work → into the normal menus:** Geotag from Tracklog, Add Missing Place Names, Undo Last Species Move, Empty Master Trash, Species Without a Website Picture, Manage Site Publication. **(b) after an update → one item:** Rebuild Entire Website + Rebuild all Albums and the Master website become one *Rebuild websites…* with its choices. **(c) repairs → one window, *Check the master…*:** runs every check as a report (Refresh Review, Recount, Fill GPS from Files, Audit Species Counters, orphan sidecars, album placed GPS, the daily health check), then offers each fix with review-then-apply; restoring a backup lives here too, not in Settings. **(d) developer only → Appendix G:** the status consoles, testbed; Rescan EXIF removed (1.8). Each repair in (c) is also a question: which write path let this happen, and can an invariant stop it at the source? Plan: `docs/plans/maintenance_regroup_plan.md`. | M–L |
| 1.10 | **One way in: Import Trip.** *Read Metadata from SV INDEX Card* is step 2 of Import Trip; drop it from the menu. | S |
| 1.11 | **Export Trip as the mirror of Import:** one window with three steps — 1 the trip ZIP, 2 the metadata to the SV INDEX card, 3 the RAWs to spare cards — instead of today's ZIP-only export plus the two backup columns. | M |
| 1.12 | **Geotag 🛰️ cell:** bright means a log was found for the day, dimmed means none — in the dark theme the two look the same. Use a visibly different mark (or the log name in the cell). | S |
| 1.13 | **Reset Trip next to the pipeline in the menu** (now under Advanced); it is a normal step, as the panel button already shows. | S |

## 2. Built, waiting for a live test

| # | What | Size |
|---|------|------|
| 2.1 | **Local names** — the laptop → BigBoy round trip (ch. 6). Planned late November. | S |

## 3. Small features (S–M)

| # | What | Chapter |
|---|------|---------|
| 3.3 | Master Species Viewer search: show the **photo count next to each match**. | 3 |
| 3.4 | **AVI lists: choose the sheet** of a workbook with several sheets. *Skipped for now (15 Nov).* | 9 |
| 3.5 | **Trip banner, leftover:** a trip still on its own banner name needs its pages written once after switching to `tripbanner.jpg`; a banner reaches the server only at Publish / Rebuild website. Until a trip runs into it. | 7 |
| 3.6 | Filter a viewer by camera (wishlist; typing `img` in Find file works today). *Skipped for now (15 Nov).* | 9 |

## 4. Bigger features and decisions (L)

| # | What | Chapter |
|---|------|---------|
| 4.1 | **Decide how capture time is stored** — A (camera's own, today), B (normalise to local), C (add a UTC column). Touches sidecars, data_files, website and master; the backlog leans to C. | 17 |
| 4.2 | **Create JPG size for a RAW** — 3200–8640 px depending on the body; resize like the others? NEEDS DECISION. | 7 |
| 4.4 | **Wikipedia beside the species** — a link (or summary) on the species page and in the app, EN and NL. Link free; quoting needs attribution (CC BY-SA). | 6 |
| 4.5 | **Aliases elsewhere** — reading a tour operator's list, the waarneming.nl import, search by a remembered name. | 6 |
| 4.6 | **Another language *instead of* Dutch** — English stays; the second language becomes the user's own (French, German, …), so the program suits every user. Design together with the species list (ch. 6). | 24 |
| 4.8 | **Complexes, defined in SV** — species a photo can't separate, with their own Dutch and English names and members. Parked until a real case; needs a short design first (phase 5 of `docs/plans/uncertain_ids_plan.md`). | 6 |
| 4.9 | **SQLite as the working store, CSVs as the record** — keep a transaction log of changes instead of rewriting the master CSVs on every edit. To discuss after the manual review; it touches the "CSVs are canonical" rule and the backup/restore story. | — |

## 5. Refactors

| # | What | Size |
|---|------|------|
| 5.2 | Mac: the default PhotoLab path is a Windows `.exe`; `projectlib.py` lowercases whole paths for website output (wrong on a case-sensitive disk); relativise the master's stored paths. | M — waits for a Mac |
| 5.3 | `rating_editor.py` (4,756) and `map_view.py` (3,264) — leave unless working in them hurts. | watch only |

## 6. Documentation

- **Manual v2** — your review (chapters 5–10 never reviewed); `GH:` notes, then `tools/svr_manual_build.py --feedback`.
- **Capabilities Added**: still no write-up for ch. 17–22.

---

*Done and removed 19 Nov: 1.1–1.5 (wrong coordinates, trip dates vs days, Read-Card
re-centre, master sidecar colours, XMP repair tool retired), 2.2–2.3 (Overview parity,
🛰️ row action), 3.1–3.2 (Local name… in the Species Editor, "all N files are here"),
4.3 (album Count Species), 4.7 (uncertain identifications), 5.1 (`trip_process.py` split).
Since then also: free-space check before Freeze (d29f030), unreadable `.hif/.pef/.srw`
named on card read and blocking Empty a Card (927bad7), manual v2 (b30b9aa).*
