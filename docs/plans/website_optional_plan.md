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

## Direction change (Gilbert, 2026-10-11): always publish — to a server OR to this computer

Instead of teaching ~10 places to SKIP publishing, SpeciesViewer always
publishes: to the web server, or to a **local website** — a folder with exactly
the live site's layout, shown by a small web server inside SpeciesViewer on
`http://127.0.0.1:<port>/`. All publishing code stays as it is; only the
destination changes. This replaces steps 1–2 and 4 of the order above.

Decisions:
1. **Where:** per master in app-data — `<master folder>/website` (C: is far
   faster than D:/E:; each master its own site; out of Dropbox).
2. **The choice is required:** the user picks *this computer* or *my web server*
   in Global Settings ▸ Website and publishing; the Setup Checklist shows ✗ until
   chosen. An existing setup with server fields filled counts as "server".
3. **No upload cache for the local destination.** `admin/FTPcache.json` records
   what the SERVER has; local copies must never be written there (a later server
   publish would then skip files it never received). A local copy is cheap.
4. **Later — local first:** with a web server, publish locally first as a
   preview (first runs of a new trip), then to the server.

Corrections from Gilbert's test: blog pages opened as plain files mostly work —
even the link to the large picture; only the banner broke (its path did not
match). Offline mode is by design (culling without network; GBIF and place-name
lookups then do not work) — not touched.

Separate item: **GBIF server configurable** — there are more GBIF servers.

How it is built:
- `engine/localsite.py` — the 14 methods of `engine/sftplib.Sftp`, as file copies
  under the local root; `remotePrefix()` = that root; the FTP-cache methods do
  nothing.
- One place picks the destination (`open_site()`), used by the 9 places that now
  construct `Sftp(...)`.
- `site_config`: the choice (`publish_target`), "set up" = local, or server with
  its fields; the website address in local mode = the local server's address;
  the server check passes without a network in local mode.
- `utils/local_web.py` — the local web server (localhost only), started with the
  app in local mode.

**Built 2026-10-11, awaiting Gilbert's test:** `engine/localsite.py`,
`engine/sftplib.open_site()` (all 9 places), `site_config.publish_target /
is_local / local_root / local_port`, `utils/local_web.py`, the required choice
in Global Settings and the Setup Checklist. A new master collection gets web
root `/nature/` (the local site needs the live layout). Tested: the local
destination's semantics (copied/skipped, deletes refused outside the site,
FTPcache untouched); a real commit of Gilbert's test day publishing 26 files
locally, served over HTTP (intro, overview, stylesheet, a deep species page
→ 200); an old setup with server fields counts as "server".
Watch: the deepest local pages are ~200 characters — inside Windows' 260 limit,
but not by much. Open: a trip without a web root would publish into the site
root — Create Trip should always give one (`/trips/<trip>`).

⚠ **Local publishing is not offline** (Gilbert): GBIF names, place names and
translation still need the internet. The local destination changes only WHERE
pages go; it must not touch `Project.online` or any lookup's own reachability
check. Only the publish-server check is skipped in local mode.

## Gilbert's test of the local website (2026-10-11): it works

Blog, trip species overview and species pages served from
`http://127.0.0.1:8765/…`, with styling, pictures and links. Missing: the
banner (`/trips/najaar/includes web/keerkringvogel.jpg`) — the trip was created
before a destination was chosen, and only repository creation ever wrote the
shipped pictures in `includes web`. Same gap for ANY change of destination
(none → local, local → server).

Fix — self-healing instead of an "initialize" step: `putSharedWebAssets`
(sv7, now also called from Write Blog) lists the site's `includes web` once and
supplies the shipped pictures that are MISSING; a site that has them transfers
nothing extra (the reason they were never re-sent: ~14 MB). Tested on the local
destination: first publish supplies 13 pictures + 3 text files, second sends
nothing.

Next finding (same test): `/trips/najaar/tripindex-nl.html` → 404. The test trip
recorded `master_repository_web = ""` and no `trip_name`/`web_slug`: it was
created while SVv3's master web root was empty, and without a trip web root.
Root cause partly mine: the "core sections and keys always present" fix of this
morning made `[master] masterbiotawebroot` exist-but-empty, so the ~20 readers'
`fallback="/nature/"` never applied. Fixed in one place (`utils/config.py`: an
empty master web root reads as `/nature/`), and Create Trip now refuses an empty
web root ("the trip needs a web root — for example /trips/<folder>"). A trip
records what was true when it was made: the test trip keeps its empty fields —
make a new one to test the trip index.

Retest with a re-created najaar (Gilbert): trip.ini now correct, but the blog's
Links sidebar still pointed at `/trips/najaar/tripindex-nl.html` (its breadcrumb
correctly at `/nature/…`). `Trip.__init__` never set `master_repository_web`; it
was only copied from trip.ini when empty, and `masterWebRoot()` then fell back
to the TRIP's root. Fixed: `Trip` takes it from the run's settings
(`[master] MasterBiotaWebRoot`); trip.ini only as fallback. Also: Create Trip
"uploaded" an empty trip image/banner name = the trip folder itself (two
warnings, on the server route too) — empty names are now skipped.
The trip index itself is made by **Publish Trip** (and Manage Site Publication)
only — by design, a trip is listed once it is published.

## End-to-end run, headless (2026-10-11) — instead of more retests on a broken test trip

Gilbert: "we are now debugging a situation that cannot exist" — the test trip
and SVv3 carried records from before today's fixes. So: a fresh new user,
website on this computer: Create Master Collection → trip (trip.ini as the
dialog writes it + sv1) → Read & Summarize (10 ORF + 14 JPG of 2026-10-10) →
Write Blog → Publish Day → list the trip (svr4, Trips ▸ 5. Manage Trip
Publication) → local server → crawl every local link from the main pages.
All steps OK; `/nature/tripindex-nl.html` exists.

Fixed on the way:
- **Site skeletons were only ever published by Create** (`/nature/intro-nl.html`
  etc. missing whenever the destination came later). `sv1.publish_skeleton` /
  `ensure_skeleton` + `sv7.ensureSiteSkeletons` — called by the commit, Write
  Blog and the trip listing — supply the trip's AND the master's skeleton when
  missing (one listing decides).
- **New master's starter pages linked to the site's top level** (built with an
  empty web root). Now built with `/nature/` but not published at creation
  (`[utilities] build_only`): New Master makes a collection for a master that is
  not open. Crawl: 37 broken → 13.

Remaining 13, none caused by the new flow:
- **Template bugs, also on the live site:** `tripindex-nl/en.html` → `trip
  index-nl.html` (space); `allspecies-en.html` Insects →
  `insecta-en/insecta.html`; `intro-nl/en.html` → `022 %20lepidoptera` (double
  space); `vogelsoortpage-nl/en.html` carries `d:\code\pictureEXIF\…` in an
  HTML comment (invisible, but a personal path in every species page).
- **Master pages a new collection lacks:** `/nature/biota/biota-nl.html`,
  `/nature/biotablog/blog index-nl.html` (linked from the trip index).
- Crawler false positives: a JavaScript snippet, the commented-out path.
- **Look-alike:** the trip overview's "Publish Trip" button publishes all DAYS;
  listing the trip in the trip index is Trips ▸ 5. Manage Trip Publication.

Template bugs fixed (Gilbert agreed): trip index "trip index" link, allspecies-en
Insects link, intro lepidoptera double space, the d:\code\pictureEXIF comment.
Crawl now: 119 local URLs, 5 "broken" = the two master pages a new collection
lacks (biota overview, master blog index — later) + one JavaScript false
positive.

## Gilbert's final check (2026-10-11): a crash, then no start

New master "SVlocal" with a collection, no website choice made. Write Blog
stopped — correctly — with "Choose where SpeciesViewer publishes … File ▸
Settings ▸ …". Then the app crashed, and every start after that crashed on
`NoOptionError: sv4writeblogmessage`.

Cause (old): `StateMachine.writeState` wrote statemachine.ini in the platform
encoding (cp1252); "▸" raised halfway through the write → truncated file → app
down; `readState` then crashed on the missing option at every start.
Fixed: written UTF-8 and atomically (tmp + replace); read through
`ini_io.read_ini`; a missing option reads as empty/False. Tested on a copy of
his damaged file (reads), a "▸" round trip, and a simulated failed write (old
file intact).

Also: the checklist now opens at start while ANY required row is ✗ — master
collection, website choice, exiftool — not only the collection. He found the
missing website choice from a failing step instead.

## Where we stopped (2026-10-11, late) — start here next session

Gilbert's test in master **SVlocal** (collection C:/temp/SVlocal, website "on
this computer", trip C:\SVtest\Trips
ajaar): SpeciesViewer starts again, Write
Blog works, the blog shows in the browser. Two things still wrong:

1. **"Trips" link → /nature/tripindex-nl.html 404.** First check whether
   Trips ▸ **5. Manage Trip Publication** was run — only that step makes the trip
   index (the overview's "Publish Trip" button publishes DAYS; look-alike, to be
   renamed). If it was run and the page is still missing, reproduce with the
   headless end-to-end (fresh LOCALAPPDATA, website=local, collection → trip →
   sv2/sv3 → sv4 → sv6/sv7 → svr4 → crawl): in that run the page DID exist.
2. **Banner broken in that blog** ("banner" alt text). The blog page asks for
   `/trips/najaar/includes web/keerkringvogel.jpg`; check that
   `putSharedWebAssets` ran (log: "Supplied … missing picture(s)") for SVlocal's
   website folder (%LOCALAPPDATA%\SpeciesViewer\masters\SVlocal\website).

Test data is messy (najaar was re-created several times across masters SVv3 and
SVlocal). Prefer a FRESH master + trip over debugging old records.

Waiting on Gilbert: may the stray `D:\code\SpeciesViewer
ajaar\` (two
placeholder JPGs, made by Create Folder before its fix) be deleted?
