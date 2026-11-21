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
| 1.3 | **Restoring or permanently deleting master pictures sits in Settings ▸ Database**, besides the Master menu. Settings is an odd place for a step that deletes pictures for good. | S |
| 1.5 | **Menu names: look-alikes and uneven wording** (was 1.4 + 1.5). *Count Species* vs *Recount Species Counters*; *Delete Trip…* vs *Delete Trip Completely…*; "Manage caches…" next to "Manage Site Publication…"; Utilities items that are sentences with a full stop; items that open a window without "…". Proposed names per item in `docs/plans/menu_regroup.xlsx`. | S |
| 1.9 | **By design vs. repair — regroup the menus.** Maintenance is scattered over Master ▸ Maintain Master Database, the Master menu, Settings ▸ Database, Utilities and Developer Tools, and mixes tools needed by design (geotag, trash, rebuild after an update) with **repairs for something that drifted**. Rule: by-design tools in the normal menus; every repair only in one window, *Master ▸ Check the Master…* (report first, then review → apply); diagnostics in Developer Tools. Includes 1.3 and 1.17. Every item with its kind, old and new place: `docs/plans/menu_regroup.xlsx`; plan: `docs/plans/maintenance_regroup_plan.md`. | M–L |
| 1.17 | **Sync Placed GPS is a master repair in the Albums menu**, and its question counts every picture with a coordinate ("8,599 missing the flag") while only hand-placed ones change. Count what will change; move it into *Check the master…* (1.9), master-wide. | S |
| 1.18 | **Help in every window.** A *Help* button (or F1) that opens the manual at the section for that window. Builds on Help ▸ Reference Manual (done 21 Nov). | M |
| 1.19 | **Standard Windows selection as well.** Click, Ctrl+click and Shift+click in the grids, next to the `[` `]` keys and *Add to selection* — as an extra, never the only way, and only where it does not clash with existing clicks. | S–M |
| 1.20 | **Sex / age / plumage is weak.** Fine for most birds, but no age classes for large birds (1st–4th year) and nothing for insects (larva, pupa, adult…). Settle the vocabulary per group, then document it. | M |
| 1.21 | **The map as a viewer of its own.** Beside a grid or the Editor it does what Lightroom and PhotoLab cannot; give it a proper place in the menus and its own manual section (bubbles, moving, travel path). | M |
| 1.22 | **No Trash window for Days.** A trip has *Trips ▸ Trash (deleted pictures)…*; pictures deleted in a day outside a trip go to a `.trash` folder inside that day folder, but there is no window to restore them (only Explorer). | S |
| 1.23 | **Temp and debug files pile up in the master's `admin` folder** — 2.4 GB in Dropbox (21 Nov). **Later, and with great care:** `admin` also holds files that exist nowhere else (`trip_admin.csv`, `master_commits.csv`, `blog_admin.csv`, `TransCache.csv`, and `status/backup/` (1.5 GB) — the master CSV backups that saved us on 12 Nov). First make an inventory that sorts every file into *only copy / keep* vs *rebuildable / debug*; nothing is touched before that is reviewed. Candidates: old `review_*`/`preview_*` dry-run folders in `status/` (~160 MB each), `display_status.csv`, `ghost_files.csv`, `console/`, the 46 MB `taxonomy_convert_*.csv`, `trip_admin - Copy*.csv`, `temp/`. Then: a retention rule for backups (keep the last N), a dry-run → review → move to `.trash`, and tools that write debug output to app-data instead of Dropbox. | S–M |
| 1.24 | **Dead links in the shipped manual.** Help ▸ Reference Manual opens the copy shipped with the program, but its few links into the technical manual (`docs/technical/`) go nowhere there, because the technical manual is not shipped. Either point them to the online copy (GitHub) or leave them out of the built page. | S |
| 1.25 | **11 species in the master list have no real pictures** (2,507 species rows, 2,496 with pictures; Full report in *Check the Master*, 21 Nov). They claim no pictures either, so the counters are right — but what are they? Candidates: subspecies parents, uncertain-ID groups, names learned from a trip whose pictures went elsewhere, placeholders. List them first, then decide per kind. | S |
| 1.26 | **Orphan sidecars appeared** — 15 `.dop` + 15 `.xmp` in the master whose picture is gone (cleaned 21 Nov via *Check the Master*). A sign of a bug: some delete or move takes the picture but not its sidecars. Keep an eye on the count; if it comes back, find that write path. | S |

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
