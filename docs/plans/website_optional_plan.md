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
   To Master for trips. FIXED 2026-10-11, retested by Gilbert in his test master: the
   reachability check and the upload run only when a website is set up; the
   CSV commit and the local copy of the day's pages always. Tested on a copy of
   his test master: 0 → 154 pictures, 507 files, 7 species.
   Note: the pictures reach the master's `biota/` in an EARLIER step; a failed
   commit leaves them there without rows until the commit is re-run (existing
   design, with or without a website).

## Idea: a Test button on the website settings (Gilbert, 2026-10-11)

In Global Settings ▸ Website and publishing: **Test** — log in with the account
and look for the website folder, then say what failed in plain words. Connection
status only checks that the server answers; a mistyped account or folder is what
actually fails, and today that shows up only at the first publish.

3. **Create Trip asks for a trip image and a web banner** (Gilbert, 2026-10-11,
   in a test master without a website): both show "✗ Missing path input"; the
   starter images in `dist/includes web/` (`tripimage.jpg`, `tripbanner.jpg`)
   are not offered as defaults. The trip is created anyway, with "please review
   the warning". Both are only used on the website — without one they should
   be optional, with the starter images as default.
4. **Create Trip accepts a trip without a web root.** Fine without a website;
   with one, publishing needs it. Decide: required only when a website is set up.

## The audit (2026-10-11, report only)

Every place that uploads, checks the server, or needs the website — by level.
"Today" = what happens when no website is set up.

### How it works now — three findings that shape the fix

1. **Everything is built locally first.** Blogs, species pages, indexes are all
   written to the repository's `web/` folder; uploading is a separate last step.
   So "local blogs" is not about *generating* them — they already exist on disk.
2. **There is an offline mode, but it is the wrong switch.** `Project.online`
   (= "is any network up") already skips the upload in Write Blog and the trip
   index. But it also switches **translation** on and off, so it cannot double as
   "no website". A second flag is needed: *publish* = website set up AND online.
3. **Pages link from the site root.** Templates use `%%90%%` = `/nature/` (master)
   or `/trips/<trip>/` (a trip): `/nature/includes web/animals.css`,
   `/trips/<trip>/biotablog/…`. Opened as a plain file, styling, pictures and links
   break. (The w3.css styling comes from w3schools.com — needs internet, not a
   website of your own.)

No menu item is greyed for a missing website: steps start and fail inside.

### Level 1 — File, Browser, Days, Master, Help, Utilities

| Place | Today | Proposed |
|---|---|---|
| Create Master / Create Trip (`sv1MakeReposity`) | works | FIXED 2026-10-11 |
| Add Pictures to Master, To Master commit (`sv7WriteCommit`) | works | FIXED 2026-10-11 |
| Species page regenerate (`engine/regenerate_species`, `upload=`) | stops: "Publishing is not set up" | upload only with a website |
| Master ▸ Rebuild Entire Website, Manage Site Publication | website-only by nature | grey, tooltip "Needs a website — Help ▸ Setup Checklist" |
| Trip registry status upload (`engine/trip_registry`) | skips, logs a warning | skip silently |

### Level 2 — Trips, To Master

| Place | Today | Proposed |
|---|---|---|
| 2 Write Blog (`sv4WriteBlog`) | stops at the server check, although it would write the blog locally | check the server only with a website; *publish* flag decides the upload |
| Write All Blogs / Redo Blog (`svr9RedoBlog`) | `online` → opens the upload connection → stops | same *publish* flag |
| Trip index (`Trip.openSFTP`, `triplib`) | same | same |
| 3 Publish Day (Freeze + `sv7`) | works since today's fix | — |
| Publish Trip (`publish_dialog`, `publish_index`) | website-only by nature | grey with the reason |
| Create Trip: trip image + web banner required | "✗ Missing path input" | optional without a website; starter images as default |
| Create Trip: no web root accepted | accepted | required only with a website |

### Level 3 — Albums

| Place | Today | Proposed |
|---|---|---|
| Album Write Blog / Build Website / Redo Blog (`svv3`, `svv4`, `svv6`) | stop at the server check | as level 2 |
| Clear album blogs (`virttrip_process`) | remote part skipped without a password | keep |
| Delete Album (`album_delete_dialog`) | tries to connect for the remote delete | local delete only without a website |
| Rebuild all album sites (`svrRebuildAllWebsites`) | website-only by nature | grey with the reason |

### Viewing blogs without a website — the larger piece

Gilbert: "blogs that we need to make local — maybe not terribly hard, but a lot
of work." Because of finding 3, two routes:

| Route | Work | Effect |
|---|---|---|
| **A. A local web server inside SpeciesViewer** (Python's `http.server` on a thread, `localhost` only) mapping `/nature/` → the master's `web/`, `/trips/<trip>/` → that trip's `web/`, `/albums…` likewise | one new module + "Open blog" points at `http://localhost:<port>/…` | the SAME pages work on the web and locally; no template or generator changes |
| B. Rewrite every link relative | every template and every generator that builds a link (`%%90%%`, breadcrumbs, `web_prefix`) | pages work as plain files; touches what publishes the live site |

**Recommendation: A.** It changes nothing in what is published — the risk the
`⚠ web slug IMMUTABLE` and URL-building rules exist for.

### Proposed order (each step stands alone)

1. The *publish* flag — one place decides "upload or not" (`site_config` +
   `online`); the gates in sv4 / svr9 / svv3 / svv4 / svv6 / triplib /
   regenerate_species / album delete use it. Level 2 and 3 then work without a
   website, writing locally.
2. Grey the website-only items with the reason.
3. Create Trip: image, banner and web root optional without a website.
4. The local viewer (route A), and "open the blog" using it.
5. Gilbert's **Test** button on the website settings.
