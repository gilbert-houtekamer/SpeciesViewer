# SpeciesViewer — Reference Manual

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
| [C — Dataflow and architecture](appendix-c-data-architecture.md) | Where pictures physically live and how they move between the four repositories. Tells the same story as [Concepts](00-concepts.md) at a lower level. |
| [D — Camera support](appendix-d-camera_support.md) | Which camera bodies have been **measured**, and what works on each. Generated — regenerate with `tools/svr_camera_checklist.py`. |
| [E — Developer reference: Data structures](appendix-e-data_structures.md) | **Developer-facing.** The four CSVs that hold the project's biological knowledge and picture metadata, and the in-memory dictionaries they map to. |
| [F — Page freshness](appendix-f-page-freshness.md) | Why a blog can be right while the trip's species summary is short, what each page is actually built from, and when Read & Summarize is not optional. |

> **On the appendices.** These were written separately, before the manual, and
> gathered here because they are the material that already existed. They are not
> a designed set. C deliberately overlaps [Concepts](00-concepts.md) — it is the
> lower-level telling of the same story, and useful as a check on whether the
> user-level version got it right. E is developer-facing and sits here because
> there is nowhere better, not because a user needs it.

## How to's

Task-shaped rather than reference-shaped: *do this, in this order, or PhotoLab
ignores your crop*. That makes them the closest thing here to the use-case
document that is still to be written.

| How to | What is described |
|---|---|
| [Refresh Master Database (admin reconcile)](howto_refresh_master_database.md) | When the master's CSVs drift from what is on disk, and how Review/Apply reconcile them. Detail behind [Master ▸ Maintain Master Database](06-master.md). |
| [How a trip gets listed on the website](howto_trip_index_publication.md) | The web slug's seven homes, why the live index is always the base, and the one mistake no button can undo. Detail behind the two *Manage … Publication* dialogs ([Trips](04-trips.md), [Albums](07-albums.md)). |
| [Create a render that the master picks up (DxO PhotoLab)](howto_create_render_procedure.md) | Getting a JPG render as far as the website — the order matters. Explains the `+ JPG` badge in [The viewers](10-viewers.md). |

> **These three are provisional.** They may move to a separate task-oriented
> manual, or be dropped if the application turns out to explain itself well
> enough. They are here for now because the text exists and is accurate.


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

- [Appendix C](appendix-c-data-architecture.md) — where pictures physically live
  and how they move. The architectural companion to [Concepts](00-concepts.md).
- [Appendix E](appendix-e-data_structures.md) — the CSVs behind it all.
- [../viewer_keybindings.md](../viewer_keybindings.md) — every key in the
  picture viewers, which this manual does not repeat.
- [../configuration_inventory.md](../configuration_inventory.md) — which setting
  lives in which file.
- [The Refresh how-to](howto_refresh_master_database.md) — the detail behind
  Master ▸ Maintain Master Database.
- [The trip-index how-to](howto_trip_index_publication.md) — the detail behind
  the two "Manage … Publication" dialogs.
