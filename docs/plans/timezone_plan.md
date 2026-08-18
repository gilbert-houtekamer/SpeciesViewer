# Capture time and time zones — design plan

Status: **findings settled, representation NOT yet decided** (2026-08-18,
ClaudeSpecies). The correctness fixes are shipped; what remains is a data-model
choice about how capture time is *stored*, plus one optional future capability.

A photo's capture time is two different facts that SpeciesViewer had been
treating as one:

- **the instant** — a point on the world's timeline, which decides where a
  tracklog says you were standing;
- **the local reading** — the wall clock where you stood, which decides which
  `YYYY-MM-DD` day folder the picture belongs to and what a caption should say.

A camera records the local reading. The instant is only recoverable if the
camera *also* recorded its UTC offset. Every bug below came from conflating the
two.

## What went wrong (fixed, for the record)

Two independent faults put 24 pictures of the 2026 Goeree trip **8.6 km** from
where they were taken, silently — both coordinates looked like plausible places
on the island.

1. **NMEA times were read as local.** `$GPGGA`/`$GPRMC` times are UTC by
   specification; the `@OM Digital Solutions/+0200/+0200` header is the
   *phone's* zone, not the meaning of the timestamps. The parser applied the
   header offset and moved the whole track two hours earlier. Fixed in
   `091072e`; the UTC date now comes from `$GPRMC` field 9, with the filename as
   fallback only (it is the phone's *local* day, so a log crossing UTC midnight
   used to stamp its later fixes a day early).

2. **The camera's offset was correlation-searched, and could not be.** 202
   pictures spanning 34 minutes against a 9.5-hour track made **22 different
   offsets score a perfect 202/202** — a 9-hour plateau — so the winner was
   decided by scan order, returning the bottom edge (`-06:14`). The true value
   sat in the same tie set at the other end. `estimate_camera_offset` is
   deleted; the offset now comes from the camera itself.

The lesson worth keeping: *a search that returns an answer is not the same as a
search that has an answer.* The gate now refuses rather than guesses.

## What the archive actually contains

Surveyed on one folder that happens to span every era —
`biota/.../pandion haliaetus` — reading **RAWs, not sidecars** (see the rule
below). Effective offset for Nikon = `Nikon:TimeZone` + 1 h when
`DaylightSavings` is on.

| trip | camera | what the RAW carries | instant recoverable? |
|---|---|---|---|
| 2013 Florida | D7100 | `TimeZone +01:00`, `DST Yes` | yes, via maker note |
| 2014 Florida | D7100 | `TimeZone +01:00`, `DST Yes` | yes, via maker note |
| 2015 Texas | D7100 | `TimeZone -06:00`, `DST No` | yes, but the DST flag is stale |
| 2019 Boston | D500 | maker note **+** `GPSDateTime …Z` | yes, two independent ways |
| 2024 Orlando | OM-1 II | `OffsetTimeOriginal`, `Olympus:DateTimeUTC` | yes, directly |
| 2024 Costa Rica | OM-1 II | same | yes, directly |
| 2025 Oman | OM-1 II | same | yes, directly |

**The maker-note route is proven, not assumed.** The D500 carries both sources,
and they agree:

```
maker note   -05:00 + DST  ->  UTC 21:14:53
GPSDateTime                 ->  UTC 21:13:56      (57 s apart — the GPS fix is
                                                   slightly older than the shutter)
```

So UTC is computable for effectively the whole archive. The weak point is the
Nikon **DaylightSavings flag**, which goes stale exactly like the zone: the 2015
Texas file says `-06:00 / DST No` while Texas was still on CDT, an hour out.
Where a GPS fix exists (D500) that ambiguity is resolved; on the D7100 it is not.

## Two rules this established

**1. Read the RAW, not the XMP.** The Orlando sidecar is *wrong* and the camera
is right:

```
ORF   DateTimeOriginal 19:28:30  OffsetTimeOriginal +01:00
      Olympus:DateTimeUTC 18:28:30      -> instant 18:28:30 UTC, self-consistent
XMP   DateTimeOriginal 14:28:30+01:00   -> instant 13:28:30 UTC, five hours wrong
```

Some tool converted the clock to Orlando local and left the `+01:00` label
behind. The sidecar's own `xmp:CreateDate` still holds the camera's original
reading, so the file disagrees with itself. Any future archive work must take
the RAW as the source.

**2. A stale zone does not corrupt the instant — but it can move the day.**
Changing a camera's time zone moves the displayed clock and the recorded offset
*together*, so UTC survives. Confirmed on a real trip: the Oman card still
carried Costa Rica's `-06:00`, and its UTC was correct — a tracklog geotag would
have placed it properly. What did break is the calendar day:
`DateTimeOriginal` says **28 January** where the true Oman local date was the
**29th**. Days are folders, so a stale zone is a filing bug even when it is not
a positioning bug.

## Decision: no bulk rewrite of existing timestamps

75,050 master rows carry bare local time (`YYYY:MM:DD HH:MM:SS`, no zone —
checked, not one exception). Those values are correct *as local readings*, which
is what they have always meant and what the website shows. Rewriting them would
convert a faithful record into an inference, and the one migration we can
imagine needing (recovering instants) is a re-read of RAWs that can be done
later, on demand, for whatever subset actually needs it.

New data is different: sidecars written since `091072e` carry the camera's own
offset, and that is additive — every existing reader takes `date[:19]` and sees
exactly what it saw before.

## Open choice: how capture time is stored going forward

Not decided. Three shapes, and the trade is between fidelity and explicitness.

**A — camera's own reading, with its own offset** (what ships today)
`2026:08:14 10:30:55+01:00`. Faithful to the camera, identical to the RAW so a
RAW↔sidecar diff stays clean, instant correct. Cost: the local reading is the
*camera's* wall clock, which may not be the wall clock you stood under.

**B — normalise to true local, with the true offset**
`2026:08:14 11:30:55+02:00`. Same instant, and the displayed time matches
reality. Costs: it writes an inference into a per-picture field; the sidecar
then disagrees with the RAW forever (PhotoLab reads both); and if no tracklog
covers the day we would be guessing the zone again — the failure this plan
exists to remove.

**C — store the instant explicitly, keep local as it is**
Add a UTC column (or `DateTimeUTC`) alongside the untouched local
`DateTimeOriginal`. Nothing is reinterpreted, both facts are first-class, and
old rows simply have the column empty. Costs a column in `data_files.csv` /
`data_pictures.csv` and a pass through `sv2ReadDirExif.py`.

Gilbert's stated preference is for the instant to be **explicit** rather than
implied — which is A or C, not B. C is the only one that loses nothing, at the
price of schema.

Whatever is chosen, two full-string parsers must tolerate a zone before a zoned
value can reach the CSVs: `sv2ReadDirExif._parse_dt` and
`engine/websupport.tryJPGtime`. Both already catch `ValueError`, so today they
degrade quietly rather than crash — a silent skip is worse than a loud one and
should be fixed as part of the same change.

## Open choice: where the day's *true* zone lives

The camera's offset answers "what did the camera think", not "where was I". The
true zone is known at Read time (the tracklog header, else the laptop) and may
be unrecoverable later. Candidates: the day folder's own metadata, the trip's
`trip.ini`, or "we keep the tracklog, so it is derivable". It is a property of
the **day**, not of each picture, so it costs one value per day rather than a
per-file field.

## Future capability: location → time zone

Given a coordinate and a date, the true zone is determinable offline
(`timezonefinder` + `zoneinfo`, both pure-Python, no network). That would:

- settle the Nikon `DaylightSavings` ambiguity for any picture with GPS;
- give the true local reading for any archive picture with a coordinate, which
  is most of them;
- remove the dependence on the laptop's own zone in the Read-time warning.

Not proposed for now — it is a new dependency, and nothing today is blocked on
it. Recorded because it is the clean answer to the one class of picture that
otherwise cannot be resolved.

## What is already shipped

| | |
|---|---|
| `091072e` | NMEA parsed as UTC; camera's own offset read from the pictures; correlation guess deleted |
| `9c2ebb6` | Dropbox lock retry on master CSV rewrites; no journal entry for a batch matching no master row |
| `42f1dc3` | Read-time warning when the camera's zone disagrees with the tracklog/laptop, and the Timezone offset field fills itself from the difference |

### How the fix was verified — and how it must be measured

The obvious measurement is wrong. Comparing *every* picture's GPS against the
track gives a flattering ~2 m median, because on this trip **185 of 202 pictures
already carried camera GPS** and those match the track whether or not the geotag
works. They drown the ones that matter.

The measurement that means something isolates pictures whose RAW has **no** GPS
but whose sidecar does — the ones we actually placed. Same card, same track,
pre-fix folder versus post-fix folder:

| 2026-08-14 folder | sidecars zoned | camera GPS | **tracklog GPS** |
|---|---|---|---|
| pre-fix (16:26) | 0 / 202 | 2.0 m median | **8,585.6 m** |
| post-fix (20:54) | 202 / 202 | 2.0 m median | **0.2 m** |

Note the camera-GPS column is identical in both. Any future check of a geotag
must split these two populations, or it will report success it hasn't earned.

Separately confirmed: nothing in SpeciesViewer strips the offset once written.
Every sidecar writer was run against a freshly zoned sidecar — place names (all
three overwrite/protect branches), rating, colour label, species subject — and
all preserve it.

Operational note: the app must be **restarted** to pick up code changes. A Read
performed in a session started before an edit silently runs the old code, which
is how two pre-fix day folders came to exist alongside the corrected ones.

## Decided: GPSProcessingMethod stays MANUAL on tracklog GPS

Tracklog-derived coordinates are stamped `MANUAL`, which lets the map drag them.
Kept deliberately: it preserves flexibility and does not disable functions when a
tracklog turns out to be less perfect than hoped. **Revisit Q1 2027**, after two
big trips have exercised it.
