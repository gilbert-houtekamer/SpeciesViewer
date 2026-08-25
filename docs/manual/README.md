README.md is the index. Then:

00-concepts.md — Day, Trip, Album, Master: what each owns, how long it lives, and the lifecycle diagram tying them together. Plus the Unfiled inbox and the main window (mode strip → left column → yellow panel).
Nine chapters, in menu order: File, Browser, Days, Trips, To Master, Master, Albums, Utilities, Help. Every item covered; length scaled to how much explanation each needs.
Appendix A — every gating rule in one table, plus a six-step "diagnose a dead menu" list.
Appendix B — menu item → sv* module, for reading the log.
I wrote it from gui/menuspec.py, using the displayed MenuSpec.title rather than the dict key where they differ (so it says Sync Lightroom & PhotoLab Metadata, not Sync Sidecars), and verified every batch module name against batch/.

Your "Add Species to Master" point landed — it's called out in both Concepts and the Master chapter as the door that can build a species website without a trip ever existing.

Four things I noticed while writing
These are the inconsistencies the exercise was meant to surface:

Two Album items aren't gated. Manage Album Publication… and Album Species Viewer stay clickable with no active album, unlike every other album item. Flagged inline in the Albums chapter.
Two Utilities items are labelled as sentences. "Create an Excel to keep track of birds seen." and "Create PhotoMechanic keyword files for next trip." — full stops and all, sitting beside terse labels everywhere else.
Ellipsis convention is inconsistent. Trip to Master Dashboard opens a dialog but has no …; Empty a Card and Create Trip likewise. Elsewhere the … is used faithfully.
"Make the Blogs Clickable" appears twice (Trips step 4, Albums) doing analogous-but-different things — correct, but the manual had to explain each separately, which is a hint the names could distinguish scope.
Where I'm likely wrong
I marked Species Settings as (inferred) — I described it from its dialog name, not its contents. Database Settings and Look & Feel are thin for the same reason. The Days chapter's claim that culling mode "writes as you go" with no separate commit is inferred from the docstring, not from watching it run.

I did not commit — per your rule, that's after you've read it.# SpeciesViewer — Reference Manual

A menu-by-menu reference. One chapter per top-level menu, in the order the
menus appear, and within each chapter the items in the order they appear.

This is a **reference**, not a tutorial: it answers *"what does this item do,
and when would I use it"*, not *"how do I publish my first trip"*. Use-case
walkthroughs are a separate document, still to be written.

> **Status: version 1, written from the code (`gui/menuspec.py`) on 2026-08-25.**
> Where a description is inferred rather than read off the implementation it is
> marked *(inferred)*. Corrections welcome — a wrong entry usually means either
> the manual is wrong or the menu item is misleading, and both are worth knowing.

## Read this first

| Chapter | Covers |
|---|---|
| [Concepts](00-concepts.md) | **Day, Trip, Album, Master** — the four scopes everything else hangs off, plus the Unfiled inbox and the main window. |

## The nine menus

| # | Chapter | The menu is about |
|---|---|---|
| 1 | [File](01-file.md) | Settings, caches, developer tools, quitting. |
| 2 | [Browser](02-browser.md) | Reading a collection — taxonomy tree or flat species index, over any scope. |
| 3 | [Days](03-days.md) | A single date's pictures with no trip and no website. |
| 4 | [Trips](04-trips.md) | The trip working repository and its per-day publishing pipeline. |
| 5 | [To Master](05-to-master.md) | Merging a finished trip into the master, then winding the trip down. |
| 6 | [Master](06-master.md) | The master as a collection: species list, viewers, and its upkeep. |
| 7 | [Albums](07-albums.md) | Date-range views of the master, and their websites. |
| 8 | [Utilities](08-utilities.md) | Card-to-inbox ingest, external-tool helpers, site-wide rebuilds. |
| 9 | [Help](09-help.md) | Connection status and version info. |

## Appendices

| Appendix | Answers |
|---|---|
| [A — Why is this greyed out?](appendix-a-greyed-out.md) | Every gating rule in the menu, in one table. |
| [B — The pipeline steps and their batch modules](appendix-b-batch-modules.md) | Which `sv*` script each menu item runs, for reading the log. |

## Related documents

These are not part of the manual but answer neighbouring questions:

- [../SpeciesViewer_DataFlow.md](../SpeciesViewer_DataFlow.md) — where pictures
  physically live and how they move. The architectural companion to
  [Concepts](00-concepts.md).
- [../data_structures.md](../data_structures.md) — the CSVs behind it all.
- [../viewer_keybindings.md](../viewer_keybindings.md) — every key in the
  picture viewers, which this manual does not repeat.
- [../configuration_inventory.md](../configuration_inventory.md) — which setting
  lives in which file.
- [../refresh_master_database.md](../refresh_master_database.md) — the detail
  behind Master ▸ Maintain Master Database.
- [../trip_index_publication.md](../trip_index_publication.md) — the detail
  behind the two "Manage … Publication" dialogs.
