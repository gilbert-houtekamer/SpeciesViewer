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

## Across the menus

| Chapter | Covers |
|---|---|
| [10. The viewers](10-viewers.md) | The four picture surfaces — Biota Browser, Species Viewers, culling grid, Large Image View — and what every filter means. |

## Appendices

| Appendix | Answers |
|---|---|
| [A — Why is this greyed out?](appendix-a-greyed-out.md) | Every gating rule in the menu, in one table. |
| [B — The pipeline steps and their batch modules](appendix-b-batch-modules.md) | Which `sv*` script each menu item runs, for reading the log. |

## Giving feedback on it

Write notes **in the chapter files, where the problem is**. A note in a separate
document has to describe *where* it applies, which is slow to write and easy to
get wrong; a note in place costs nothing to aim.

Mark each one with `GH:` at the start of a blockquote:

```markdown
> GH: Wrong — Species Settings is only the GBIF timeout and the
> placeholder prefix. The name languages are in Look & Feel.
```

That is the whole convention. `> **GH:**` works too if you prefer it bold while
typing. Notes may run several lines; keep the `>` on each.

| It gets you | |
|---|---|
| **Loud in the built page** | An orange callout with a `GH` tag — impossible to mistake for manual prose |
| **Listed in the sidebar** | A note count and a jump-link to each one, so you can review your own notes afterwards |
| **Extractable** | `python tools/svr_manual_build.py --feedback` prints every note with `file:line` |

Two kinds of note are worth making, and they are different:

- **"This is wrong"** — say what is actually true, if you know. That is a
  correction and I can just apply it.
- **"This confused me"** — no fix needed. If the manual is unclear the
  application may be too, which is the more valuable finding of the two.

Where you are confident and it is a small factual fix, **just edit the prose
directly** — no marker needed. Save `GH:` for what needs discussing.

> Notes are ordinary Markdown, so nothing breaks if you leave one in. The
> `--feedback` list is how they get found again.

## Reading it as one document (and as a PDF)

The chapters are separate files so they stay mergeable and quick to edit. To read
the manual as a **single page** instead:

```
python tools/svr_manual_build.py --open
```

That writes `SpeciesViewer_Manual.html` next to these files and opens it. It is
one self-contained page — a sticky chapter/section sidebar, a filter box that
narrows the sidebar as you type, and the current section highlighted as you
scroll. Lighter than an editor, and it renders the tables and diagrams.

**For a PDF:** open that page and print to PDF (`Ctrl+P` → *Save as PDF*). The
print stylesheet drops the sidebar, starts each chapter on a new page, and keeps
tables and diagrams from splitting across pages.

The HTML is not committed — it is a one-command rebuild from the Markdown, which
stays the source of truth. Run the build again after editing a chapter.

| Option | Does |
|---|---|
| `--open` | Open the result in your browser |
| `--out PATH` | Write somewhere other than the default |
| `--vendor-mermaid` | Download mermaid.js once, so later builds need no network |
| `--no-mermaid` | Omit the diagram script (diagrams stay as code blocks) |
| `--feedback` | List the `GH:` review notes with `file:line`, and exit |

### Reading it offline

**Building never needs internet** — the script only reads Markdown and writes
HTML. The diagrams are the only thing that might: by default the page loads
mermaid.js from a CDN when you *open* it.

Run this once, while you have a connection:

```
python tools/svr_manual_build.py --vendor-mermaid
```

It saves mermaid.js to `docs/manual/_assets/` (3 MB, MIT, not tracked in git),
and from then on every build **inlines** it. The page becomes one genuinely
self-contained file — no network, on a plane or anywhere else. The build says
which mode it used:

```
Diagrams: inlined — works offline.
```

The trade is size: ~155 KB with the CDN, ~3.4 MB inlined. Irrelevant locally,
and the file is gitignored either way.

> Without the vendored copy and without a network, the diagrams degrade to
> readable code blocks rather than breaking the page.

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
