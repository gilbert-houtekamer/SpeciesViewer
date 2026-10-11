# Website optional — SpeciesViewer without publishing

*Opened 2026-10-11 from docs/plans/setup_checklist_plan.md. Status: QUESTION;
first findings below.*

## Why

Most birders do not publish to the web (Gilbert, talking to them). SpeciesViewer
today assumes a website: the upload settings (server, account, folder) are
effectively required — "without them SV does not work; a design choice". For a
new user they must be optional, and the setup checklist already shows them as
optional.

## The question

Which flows stop, or misbehave, when no website is set up? Per level:

| Level | Menus | Expected without a website |
|---|---|---|
| 1 | File, Browser, Days, Master, Help, Utilities | should all work |
| 2 | Trips, To Master | trip pipeline works; publishing steps unavailable |
| 3 | Albums | albums work; publishing unavailable |

Blogs exist only on the web, so blog *writing* without publishing is a separate
question.

## Next

An audit, report only: every place that reads the upload settings or uploads,
what it does when they are empty, and which level it belongs to. Then decide
per place: unavailable (greyed, with the reason), skipped, or local-only.

## Findings so far

1. **Create Trip / Create Master** (`batch/sv1MakeReposity.py`) — FIXED
   2026-10-11: the local site is always made; the upload only when a website is
   set up.
2. **Add Pictures to Master fails at the commit** (`batch/sv7WriteCommit.py`) —
   level 1, so it blocks the whole Days → Master route. Found by Gilbert,
   2026-10-11, in an empty test master with 154 pictures: everything up to the
   freeze works; `main()` then checks `connectivity.publish_reachable()` and
   stops with "Publishing is not set up". Safe — it stops BEFORE
   `commitProjectFiles`, so nothing reaches the master's CSVs. Same step serves
   To Master for trips.
