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
| 1.18 | **Help in every window.** A *Help* button (or F1) that opens the manual at the section for that window. Builds on Help ▸ Reference Manual (done 21 Nov). | M |
| 1.20 | **Sex / age / plumage is weak.** Fine for most birds, but no age classes for large birds (1st–4th year) and nothing for insects (larva, pupa, adult…). Settle the vocabulary per group, then document it. | M |
| 1.23 | **Temp and debug files pile up in the master's `admin` folder** — 2.4 GB in Dropbox (21 Nov). **Later, and with great care:** `admin` also holds files that exist nowhere else (`trip_admin.csv`, `master_commits.csv`, `blog_admin.csv`, `TransCache.csv`, and `status/backup/` (1.5 GB) — the master CSV backups that saved us on 12 Nov). First make an inventory that sorts every file into *only copy / keep* vs *rebuildable / debug*; nothing is touched before that is reviewed. Candidates: old `review_*`/`preview_*` dry-run folders in `status/` (~160 MB each), `display_status.csv`, `ghost_files.csv`, `console/`, the 46 MB `taxonomy_convert_*.csv`, `trip_admin - Copy*.csv`, `temp/`. Then: a retention rule for backups (keep the last N), a dry-run → review → move to `.trash`, and tools that write debug output to app-data instead of Dropbox. | S–M |
| 1.24 | **Dead links in the shipped manual.** Help ▸ Reference Manual opens the copy shipped with the program, but its few links into the technical manual (`docs/technical/`) go nowhere there, because the technical manual is not shipped. Either point them to the online copy (GitHub) or leave them out of the built page. | S |
| 1.25 | **7 species lost their pictures before July, and nobody knows how** (investigated 21 Nov). The master list has 11 species with 0 pictures, all names from the shipped starter list. **4 are explained:** old names of split species, their pictures re-filed in Oct to the new name (*Rallus longirostris* 70 → *R. obsoletus*, *Cecropis daurica* 50 → *C. rufula*, *Cistothorus platensis* 6 → *C. stellaris*, *Gymnopithys leucaspis* 7 → *G. bicolor*). **7 are not:** sheep *Ovis aries* (5), red junglefowl *Gallus gallus* (3), Spanish imperial eagle *Aquila adalberti* (3), marsh owl *Asio capensis* (5), yellow canary *Crithagra flaviventris* (2), *Empis opaca* (6), *Sympetrum meridionale* (2) — (n) = the count they claimed until the recount. No master picture list since July (13 Jul, 24 Jul, 24 Sep backups), no current trip and no folder on disk holds them. Deleted before July when deletes did not lower counters — or lost (ghost-row incident?). Older backups or retired trips might tell. Keep the rows: they carry the Dutch and English names. | S |

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
