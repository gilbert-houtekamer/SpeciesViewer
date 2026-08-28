# 3. Days

The light lane. A **Day** is a single `YYYY-MM-DD` folder of pictures with no
trip, no repository and no website — reviewed, and then added to the master.
Adding it to the master also updates the master website: new species appear, and
where a Day brings a better-rated picture it replaces the current one.

Use it for the local outing: an afternoon at the reserve, a garden moth trap, a
walk that produced four species worth keeping. Creating a trip for that would be
all ceremony and no payoff.

| Item | Needs | What it does |
|---|---|---|
| Register Existing Day… | | Point at a folder; nothing is copied |
| Copy Day from Card… | | Copy off a card into dated folders |
| Review & Rate Pictures… | An active day | Cull, rate, name, geotag |
| Add to Master | An active day | **The commit** — copies keepers into the master |
| Remove from List | An active day | Forget the folder; pictures untouched |

The whole lane in one line:

```
Register (or Copy from card)  →  Review & Rate  →  Add to Master
```

---

## Register Existing Day…

Point at an existing `YYYY-MM-DD` folder and add it to the Days list. **Nothing
is copied and nothing is moved** — this only tells the application the folder
exists so it can appear in the left column and the yellow panel.

Use it when the pictures are already on disk: you copied them yourself, or they
came out of the Unfiled inbox.

## Copy Day from Card…

Copies pictures off a card into one or more `YYYY-MM-DD` folders and registers
each created folder as a Day.

This is the same SD-card ingest dialog as [Trips ▸ Read SD-cards](04-trips.md),
running in "days mode": date-bucketing is on (so a card holding three dates
produces three day folders), and the folders it creates are registered when the
copy finishes.

The dialog's **View Pictures** button shows everything just imported, across all
the day folders it made; once you close the dialog you cull each day on its own.

## Review & Rate Pictures…

*Needs: an active day.*

Opens the review viewer on the active day's folder, in **culling mode**: your
edits — star ratings, colour labels, species names, GPS and location — are
written straight to the sidecars in that folder.

This is the same viewer used everywhere else in the application. Its filters and
controls are in [10. The viewers](10-viewers.md#the-culling-grid); its keys are
in [viewer_keybindings.md](../viewer_keybindings.md).

> Culling writes as you go — there is no *save* step for your ratings and names.
> The commit that matters is **Add to Master**: until you run it, nothing has
> left the day folder.


## Add to Master

*Needs: an active day.*

Points the **Add to Master** session at the active day and scans it. From there
the flow is identical to [Master ▸ Add Pictures to Master…](06-master.md): the
day is treated as an ephemeral micro-trip, its identified species are ingested,
and its pictures are copied into the master.

This is the Day lane's whole point. Once it has run, the day's keepers are in the
permanent collection and appear on the master website like anything else.

## Remove from List

*Needs: an active day.*

Takes the day off the Days list. **The pictures are left completely untouched** —
this only forgets the folder. Register it again at any time.

---

## When to use a Day instead of a Trip

| Use a **Day** when | Use a **Trip** when |
|---|---|
| One date, or a handful of unrelated dates | A run of consecutive days that belong together |
| You do not want a blog or a website | You want a written-up, published journey |
| The species just need to reach the master | The pictures need per-day blogs, a trip index entry, a cover picture |
| You want it done in three clicks | You are prepared to run a five-step pipeline per day |

Both routes end in the master, and the master cannot tell the difference
afterwards.
