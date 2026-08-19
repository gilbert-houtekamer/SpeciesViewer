# Per-day backup of the WORK to a spare card

Backlog ch. 18 item 3. Agreed with Gilbert 2026-08-19.

**Status: the BACKUP half is built and working; RESTORE is not written yet.**
Steps 0-2 of the build order below are done. Real run on 2026 North Greece:
3217 files, 309 MB of ZIPs, about two minutes to a 16 GB card; the second run
skipped everything in seconds.

## Why

The cards keep the pictures. On a long trip there are plenty of them and no
expectation of filling them, so **RAW loss is not the risk and no
mirror-everything backup is wanted** — that was settled when the chapter was
written and is not to be re-raised.

What is exposed is **the work**: culling colours, species identifications,
locations, crops and blog text, all of which exist only on the laptop. A
fortnight of evenings, on one machine, a long way from home.

## What the numbers say

Measured on `2026 Goeree/2026-08-14`, a real day:

| | |
|---|---|
| 202 × `.ORF` | **4.1 GB** |
| 202 × `.xmp` | **1.6 MB** |

**2500 : 1.** The metadata backup is effectively free, which removes the only
argument for making it optional or occasional. Add the day's JPGs at ~2 MB each
and a fortnight still fits several times over on a 16 GB card.

## Shape: two artifacts, not two levels

The earlier write-up asked whether to have a thin nightly and a fat weekly.
The ratio above makes that choice moot — always take everything. But a second
artifact *is* needed, and it already exists.

**1. One ZIP per day** — the day's work, keyed to the day folder, under a
folder per trip:

```
SV BACKUP (H:)
  2026 North Greece\
      2026 North Greece - 2026-05-04.zip
        manifest.json              2 KB
        *.xmp        (152 files)  1.2 MB   deflated
        *.dop        ( 48 files)  0.3 MB   deflated
        *.jpg        ( 41 files)   82 MB   stored
        blog.ini                   1 KB
      … 11 more days
      settings\  sv_gui_cache-bigboy.json
  2026 Goeree\
      2026 Goeree - 2026-08-14.zip …
```

A card collects several trips over time and a flat root of forty ZIPs is not
something you can read, so each trip gets a folder and a restore points at one.
The ZIP names stay trip-qualified anyway: a ZIP copied out of its folder should
still say what it is. Backups written before this layout are **moved** into
their folder on the next run — a rename on the same card, instant, verified
byte-for-byte — never re-zipped.

The settings copy is named by **machine**, not by trip: the GUI cache is
per-machine state, so two trips from one laptop should not make two copies of
the same thing, while the laptop and BigBoy must not overwrite each other.
`svconfig.ini` is deliberately excluded — it holds `Sftppwd`, and a website
password does not belong on a card that travels in a camera bag.

**2. One trip ZIP** — `svr6ExportTrip`, which already writes trip.ini, the
species collection, the **lifer baseline** and the translation cache, in
kilobytes. Rewritten every run.

`svr6ExportTrip`'s own docstring settles the division:

> The picture data never travels: copy CurrentPictures with the OS, and Read &
> Summarize rebuilds the per-day CSVs on the other machine from the sidecars.

So the per-day CSVs in the trip repo (`metadata/review/<day>/`) are **derived**
and are deliberately not backed up. Day ZIPs alone would restore the culling and
lose every lifer verdict, which is why both artifacts are required for a real
restore.

Fold `D:\gpslogs` (1.3 MB, 4 files) into the trip ZIP: tracklogs exist only on
the laptop and geotagging depends on them.

## The deletion signal (Gilbert's insight)

**A RAW with no XMP was deleted during culling.** This works because
`copy_card` writes an XMP per RAW at ingest, so *every surviving RAW has an
XMP* is true from the moment the card is read — it is an invariant we inherit,
not one we have to maintain.

Edge case to name, not to solve: a RAW that never got an XMP (ingest with
sidecars off) is indistinguishable from a deleted one.

## The manifest is what makes both halves work

A small `manifest.json` inside each ZIP: every file's name, size and mtime,
**plus the list of RAW filenames that existed**. Two jobs:

- **"Skip unchanged days" becomes exact.** Comparing a directory scan against
  the previous ZIP's manifest catches *deletions*, which a newest-mtime check
  silently misses — and deletions are the entire point of culling.
- **Restore knows which RAWs should be there**, which is the reconcile below.

## The prefix — currently unrecoverable, must be saved

Files are named `2026 Goeree - N8140001.ORF`. `target_name` builds that as
`f"{prefix} - {src_name}"`, so the separator is a literal `" - "` and the prefix
is recoverable from any filename by splitting on the first one.

**Where the prefix lives today: `sv_gui_cache.json` only.**

```json
"sdcard_ingest": { "prefix": "2026 Goeree", "source": "H:/DCIM/101OMSYS", ... }
```

That is a per-machine GUI cache. It is **not** in `trip.ini` (which holds
`trip_name`, `picture_root`, `web_slug`, the dates and the blog text, but no
prefix), not in the trip repository, and not in anything `svr6ExportTrip`
carries. **On a fresh machine the prefix is gone** — reconstructable only by
reading it off a filename by eye. If the cards are then re-read with a slightly
different prefix, every XMP orphans and it looks exactly like *"I deleted
everything"*.

So the prefix gets recorded in three places, each with a different job:

1. **`trip.ini` → `picture_prefix`.** It is trip identity, the same class as
   `trip_name` and `picture_root` which are already there. Being in trip.ini
   means `svr6ExportTrip` carries it **for free** — the export already ships
   trip.ini, so there is no new plumbing in the export at all.
2. **The day manifest — the *observed* prefix**, derived from the filenames
   actually present rather than from a setting. This is the authoritative copy:
   it stays true if trip.ini is edited later, and it covers the case no setting
   can describe — a day read with a *different* prefix, which happens with a
   second body or a correction mid-trip.
3. **Pre-fill on the Read dialog** from `trip.ini` when a trip is selected,
   falling back to the GUI cache as now. This is what makes a restore work
   without remembering anything: select the restored trip and the prefix is
   already right.

**trip.ini is convenience; the manifest is truth.** If they disagree the
manifest wins and the restore says so.

The reconcile then has a real check to make rather than a guess: *0 of 202 RAWs
matched — the backup was written with prefix `2026 Goeree`, these files use
`2026 Goeree trip`.* That must refuse, not proceed. (See memory
`feedback_qa_as_guardrails`: an unexpected value is a bug signal, and the
invariant should be visible.)

## Restore: report first, never silent

1. Read the cards normally → all RAWs land in day folders, including the
   culled ones.
2. Unzip each day ZIP into its matching day folder.
3. **Reconcile and report:** *"202 RAWs on disk, 152 in the backup — 50 were
   deleted during culling."* With the list, and one button to remove them.

Gilbert's alternative — filter during the card read so the deleted ones are
never copied — is cleaner in theory but ties restore to the moment of ingest and
gives no second look. What actually cost data in this project was software
deleting from CurrentPictures, so a restore that deletes silently is the wrong
shape whatever the reasoning.

**Restore gets tested before departure, not after.** A backup nobody has
restored from is a belief rather than a backup.

## Learned while building

**The manifest is written LAST, from the files that actually went in.** The
first version wrote it up front, so a backup in which every file failed to be
added produced a ZIP holding nothing but a manifest *claiming all of them* — and
replaced the good ZIP with it. Now a file that could not be read is simply
absent from the manifest, which makes the day read as stale on the next run and
retry itself. If nothing at all gets in, the write raises and the previous ZIP
is left untouched.

That is the load-bearing rule of the module: **the manifest is the truth about
what the ZIP contains.** Staleness detection then does the rest, with no extra
bookkeeping and nothing to go out of date.

**A gap to close before restore can work.** The backup currently writes day ZIPs
and the settings copy, but *not* the trip ZIP (`svr6ExportTrip`). Without it a
restore has the culling but no trip.ini, no species collection and no lifer
baseline. Folding the export into the same run is small and belongs with the
backup, not with the restore.

## Decided

- **One ZIP per day, overwritten** via `.tmp` + rename, so an interrupted
  nightly write never destroys the previous good ZIP. No generations — the cards
  hold every RAW, so nothing irreplaceable rides on an older copy.
- **Always everything** in the day ZIP (XMP + DOP + JPG + blog.ini). No modes,
  no nightly decision, one thing to restore.
- **Deflate the text, store the JPGs.** XMP is XML and compresses ~5:1; JPGs do
  not shrink and compressing them only costs time.
- **Destination is a plain folder path**, defaulting to a card. No card-specific
  logic — it must work to any drive, on Windows and macOS alike.

## Build order

Smallest independent piece first; nothing in the daily flow calls any of it.

0. **DONE** — `picture_prefix` persisted into `trip.ini`, Read dialog pre-fills
   from it. `trip_transfer` copies trip.ini verbatim (`zf.write(abs_path, rel)`),
   so the trip export carries the prefix with no change to the export at all.
   Side benefit: switching trips used to offer the PREVIOUS trip's prefix.
1. **DONE** — `engine/work_backup.py`. `backup_day` / `backup_days` /
   `day_status` / `observed_prefix` / `copy_settings_snapshot`, no GUI.
2. **DONE** — a 💾 column in the Trip overview beside Geotag, one cell per day,
   the glyph reporting state (backed up / changed / never / nothing to back up)
   rather than only offering an action. Clicking opens a picker with that day
   ticked and the others one tick away; days needing a backup are pre-ticked, so
   "back up whatever changed today" is one click. Worker thread, and the same
   free-space pre-flight the card read has.
2b. **TODO** — fold `svr6ExportTrip` into the same run (see the gap above).
3. `restore_report(zip, day_dir)` — **read-only**, prints what a restore would
   do. This is the prefix guardrail's home.
4. The restore itself, driven by that report.

Steps 1 and 3 are both non-destructive, and together they are enough for the
pre-departure rehearsal.
