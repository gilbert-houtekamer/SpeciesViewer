# SpeciesViewer — Technical manual

*For people and coding agents who work on the code.* The user's manual is
[docs/manual/](../manual/README.md); this one explains how the program works
inside and what must never break.

Start with chapter 1. Before touching anything that copies, moves or deletes
pictures, read its section *What you must never break*.

| # | Chapter | Answers |
|---|---|---|
| 1 | [Architecture and dataflow](01-architecture.md) | Where pictures live, how a trip reaches the master, what must never break. **Read first.** |
| 2 | [Data structures — the four CSVs](02-data-structures.md) | The four CSVs, the dictionaries they map to, and how they reference each other. |
| 3 | [Menu items and their batch modules](03-batch-modules.md) | Which `sv*` module each menu item runs — for reading the log. |
| 4 | [Page freshness](04-page-freshness.md) | What a published page is built from, and the Brazil case that taught it. |

Build it into one browsable page (offline, printable to PDF) with
`python tools/svr_manual_build.py --technical --open`.

Related: [configuration inventory](../configuration_inventory.md) (where a new
setting belongs), [plans](../plans/), [design docs](../design-docs/),
[CLAUDE.md](../../CLAUDE.md) (commands and hard rules).
