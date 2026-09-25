# Appendix F — Page freshness: what a published page is built from

*Why a blog can be right while the species summary is stale, and what to re-run.*
Written 2026-09-25 after a real case on the 2026 Brazil trip.

---

## The one rule behind all of it

**Nothing that publishes a page looks at your picture folders.** Every step
builds its page from the CSVs under `metadata/`, never from a directory scan.
`readProject()` — the loader that Write Blog, Freeze and Publish Day all go
through — reads exactly this and nothing else:

| Reads | From |
|---|---|
| species list | `metadata/review/<day>/species.csv` |
| species collection | `metadata/active/speciescollection.csv` |
| files + pictures, accumulated | `metadata/active/data_files.csv`, `data_pictures.csv` |
| files + pictures, this day | `metadata/review/<day>/data_files.csv`, `data_pictures.csv` |

So a file that exists on disk but is not in a CSV **does not exist** as far as
publishing is concerned. That is the whole of this appendix; everything below is
a consequence.

**Read & Summarize is the only step that turns files on disk into CSV rows.**
Nothing else notices a new file.

## Two clocks, not one

Pages fall into two groups, and they go stale for different reasons.

| | Per-day pages | Trip-wide pages |
|---|---|---|
| Examples | A day's blog post and its picture pages | `summaries/all biota species-{nl,en}.html`, the trip species index |
| Built from | That **one day's** `review/<day>/` CSVs | The **accumulated** `active/` CSVs |
| Goes stale when | Read & Summarize has not run since you changed that day | Any published day was frozen from CSVs that were behind |
| Brought up to date by | Read & Summarize on that day, then Write Blog | Read & Summarize on the affected days, then Publish Day again |

A per-day page only needs *its own day* to be current, which is why blogs are
usually right. A trip-wide page needs *every day that has been published* to have
been current at the moment it was published — a much stronger condition, and the
one that quietly fails.

## What actually went wrong on the Brazil trip

The species summary listed 47 species when the trip had seen far more. The cause
was **not** stale renders — it is worth saying plainly, because that was the
first theory and it was wrong. Checked on the day in question, 2026-08-28:

```
264 file rows: xmp 83, orf 79, dop 86, heic 4, jpg 12
   jpg rows = 12, of which _DxO = 12
```

Every PhotoLab render was in the CSV, which is why that day's blog was complete.
The **JPEG Files** column in the trip overview shows the same 12 — that column is
the quickest way to confirm a day's renders have been picked up.

What had gone wrong was the accumulation. `active/species.csv` held **107**
species, and 2026-09-04's day list was **107 with none missing** — the trip-wide
list *was* one day's list, the last one published. The days before it had been
published from an `active/` that never carried them forward.

## The second trap: renders made after the last summarize

The same structure allows a different failure, which did not happen here but can:

1. You cull a day and export the JPGs from PhotoLab.
2. You skip Read & Summarize — it is slow, and the blog looks fine without it.
3. You publish the day.

Step 3 then freezes the day from CSVs that predate the renders. Its blog can
still look perfect, because Write Blog reads those same CSVs and only claims to
describe that one day. The symptom is distinctive: the species list is *plausible
but short*, and the missing species are the ones whose only pictures are renders
made after the last summarize. Check the **JPEG Files** column — if it is lower
than the number of renders you exported, Read & Summarize has not caught up.

## Publish Day replaces `active/`, it does not merge into it

Worth knowing, because it explains how far a stale day reaches:

```python
# batch/sv7WriteCommit.py — commitProjectFiles()
shutil.copy(frozendir/"species.csv",              activedir/"species.csv")
shutil.copy(frozendir/"frozen_data_files.csv",    activedir/"data_files.csv")
shutil.copy(frozendir/"frozen_data_pictures.csv", activedir/"data_pictures.csv")
```

`active/` becomes whatever the day being published froze. The accumulation is
done **earlier**, at Freeze, which reads `active/` and appends the day to it — so
each day builds on the one published before it. Publishing days out of order, or
from an `active/` that was emptied (**Reset Trip** does that deliberately), breaks
that chain, and every day published afterwards inherits the break.

**Publish Trip** is a bulk run of Publish Day, so it behaves the same way — it is
a convenience, not a different mechanism.

## What to do

**The rule of thumb:** *Read & Summarize is what makes new files real; Publish
Day is what carries a day into the trip-wide totals.* Run the first after
PhotoLab, and run the second in date order.

If a trip-wide page already looks short:

1. Run **Read & Summarize** on the days whose species are missing.
2. Run **Publish Day** on those days again, in date order.
3. Re-open the species summary and check the count is believable.

Nothing is lost while this is wrong. Every day's species list stays in
`metadata/review/<day>/species.csv`, so a trip-wide page can always be rebuilt —
it is a stale page, never a lost record.

## What this does *not* affect

- **Per-day blogs** already written stay correct; they describe their own day.
- **The master.** Merging into the master reads the pictures themselves, not
  these summaries.
- **Your sidecars.** Ratings, colours, species and crops live in the `.xmp` /
  `.dop` beside each picture and are untouched by any of this.

---

*(The `readProject()` call list and the `sv7WriteCommit` copy above are read off
the code. The claim that Freeze is where accumulation happens is **inferred**
from the observed behaviour — a published trip whose `active/species.csv` held
exactly one day's species — rather than traced line by line.)*
