# Per-day backup of the WORK to a spare card

Backlog ch. 18 item 3. Agreed with Gilbert 2026-08-19; no code written yet.

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

**1. One ZIP per day** — the day's work, keyed to the day folder:

```
2026 Goeree - 2026-08-14.zip
  manifest.json              2 KB
  *.xmp        (152 files)  1.2 MB   deflated
  *.dop        ( 48 files)  0.3 MB   deflated
  *.jpg        ( 41 files)   82 MB   stored
  blog.ini                   1 KB
```

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

## The prefix trap — must fail loudly

Files are named `2026 Goeree - N8140001.ORF`. On a restore the cards are re-read,
and if the prefix is typed even slightly differently **every XMP orphans and it
looks exactly like "I deleted everything"**.

The manifest catches it: *0 of 202 RAWs matched — the prefix differs*. This must
refuse, not proceed. (See memory `feedback_qa_as_guardrails`: an unexpected value
is a bug signal, and the invariant should be visible.)

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

1. `backup_day(day_dir, dest) -> summary` — plain function, zips one day, writes
   the manifest. Runnable from a script, no GUI.
2. "Back up the day's work" action, plus a per-trip sweep that skips unchanged
   days by manifest comparison.
3. `restore_report(zip, day_dir)` — **read-only**, prints what a restore would
   do. This is the prefix guardrail's home.
4. The restore itself, driven by that report.

Steps 1 and 3 are both non-destructive, and together they are enough for the
pre-departure rehearsal.
