# 3. Days

The light lane. A **Day** is a single `YYYY-MM-DD` folder of pictures with no
trip, no repository and no website — reviewed, and then added to the master.
Adding it to the master also updates the master website: new species appear, and
where a Day brings a better-rated picture it replaces the current one.

Use it for the local outing: an afternoon at the reserve, a garden moth trap, a
walk that produced four species worth keeping. A trip for that would only add
administration, for nothing in return.

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

## The Days panel

With the **Day** pill selected, the table on the right lists your days, one row
each. Double-click a cell to act on that day:

| Column | Double-click does |
|---|---|
| **Day** | — (hover for the folder; grey when the folder is gone) |
| **Pictures** | Review & rate this day's pictures |
| **🛰️** | Geotag from a tracklog — fills missing GPS and altitude from the day's GPS log |
| **Master** | Add this day to the master. 🔘 ready · ✅ already added (double-click again to add new or re-rated pictures) · ⚪ folder gone |
| **✖** | Remove from the list; the pictures stay |
| **Note** | Your own line about the day ("Zwin, high tide, spoonbills") |

Everything about GPS — the 🛰️ column, placing pictures by hand, place names —
is brought together in [How-to: GPS and place names](howto_gps_and_places.md).

---

## Register Existing Day…

Point at an existing `YYYY-MM-DD` folder and add it to the Days list. **Nothing
is copied and nothing is moved** — this only tells the application the folder
exists so it can appear in the left column and the Days panel.

Use it when the pictures are already on disk: you copied them yourself, or they
came out of the Unfiled inbox.

## Copy Day from Card…

Copies pictures off a card into one or more `YYYY-MM-DD` folders and registers
each created folder as a Day.

This is the same SD-card ingest dialog as [Trips ▸ Read SD-card or Folder](04-trips.md),
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
permanent collection and appear on the master website like anything else, and
the day shows ✅ in the Days panel.

Only pictures with a species (or marked as a landscape) are copied; the rest
stay where they are. Before copying, it checks that the master's drive has room,
and stops with nothing copied if it has not.

**Delete originals after commit** — tick this box to remove the day's files once
they are in the master. Only files the master holds an exact copy of are
deleted; anything else is kept and listed in the log.

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
