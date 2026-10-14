# Appendix A — Why is this greyed out?

Every gating rule in the menu, in one place. The menu re-evaluates these each
time it opens, so an item ungreys as soon as the condition it wants becomes
true — no restart needed.

## The eight conditions

| Condition | True when |
|---|---|
| **a master** | A master database path is configured |
| **a full master** | A master is configured **and** it is not a read-only-locked SmallMaster install |
| **an active trip** | A trip is selected in the left column |
| **an active album** | An album is selected in the left column |
| **an active day** | A day is selected in the Days list |
| **a trip and a master** | Both of the above |
| **the step's turn** | The statemachine says this day has completed the previous pipeline step |
| **a lifecycle milestone** | Merge done / trip retired / days published — see below |

## The lifecycle milestones

These four are checked against real evidence on disk, not a remembered flag in
the GUI. That is why they survive a restart, and why they are correct even when
the work was done on another machine.

| Milestone | Proof it looks for |
|---|---|
| **days published** | At least one day folder whose `WriteCommit` step is done |
| **merge ready** | At least one day published **and** every day holding JPGs published |
| **merge done** | A row for this trip's picture root in `<master>\admin\master_commits.csv` |
| **trip retired** | The `retired_to_album` flag in the trip's `trip.ini` |

> Only folders named `YYYY-MM-DD` count as trip days. Auxiliary folders such as
> `iphone` carry JPGs but are not part of the day pipeline — counting them would
> keep Merge greyed forever.

---

## The full table

### File

| Item | Needs |
|---|---|
| All Settings dialogs | — |
| Manage caches… | — |
| Rescan master EXIF cache | a master |
| **Developer Tools submenu** | *visible only when debug is enabled — restart to apply* |
| Trip Status Console… | a master |
| Merged Status (all machines)… | a master |
| Audit Species Counters (dry-run)… | a master |
| Clean Orphan Sidecars… | a master |
| Call Testbed for trip day | — |
| Quit | — |

### Browser

| Item | Needs |
|---|---|
| Trip Browser… / All Species in the Trip… | an active trip, or a configured standalone repository |
| Album Browser… / All Species in the Album… | an active album |
| Master Browser… / All Species in the Master… | a master |

### Days

| Item | Needs |
|---|---|
| Register Existing Day… | — |
| Copy Day from Card… | — |
| Review & Rate Pictures… | an active day |
| Add to Master | an active day |
| Remove from List | an active day |

### Trips

| Item | Needs |
|---|---|
| Select or Update Trip… | — |
| Create Trip | — |
| Export Trip | an active trip |
| Import Trip | — |
| Restore Work from Card | an active trip |
| Read SD-card or Folder | an active trip (without one: Days ▸ Copy day from card) |
| Empty a Card | an active trip |
| Sync Lightroom & PhotoLab Metadata | an active trip |
| 1. Read & Summarize | an active trip **and** the step's turn |
| 2. Write Blog | an active trip **and** the step's turn |
| 3. Publish Day | an active trip **and** the step's turn |
| 4. Make the Blogs Clickable | days published |
| 5. Manage Trip Publication… | an active trip |
| Trip Species Viewer | an active trip |
| Edit Species List | an active trip |
| Publish Species Names | a trip and a master |
| Count Species | an active trip |
| Advanced ▸ Manage Blogs | — |
| Advanced ▸ Reset Trip | an active trip |
| Advanced ▸ Recreate Trip Repository | an active trip |
| Advanced ▸ Legacy ▸ Read Directory | an active trip **and** the step's turn |
| Advanced ▸ Legacy ▸ Write Summary | an active trip **and** the step's turn |
| Advanced ▸ Legacy ▸ Freeze Data | an active trip **and** the step's turn |
| Advanced ▸ Legacy ▸ Write Website Commit | an active trip **and** the step's turn |

### To Master

| Item | Needs |
|---|---|
| Trip to Master Dashboard | an active trip |
| Publish Species Names | a trip and a master |
| Merge Pictures into Master | merge ready |
| Retire Trip to Album | a full master **and** an active trip **and** merge done |
| Delete Local Files Backed Up in Master… | merge done |
| Delete Trip… | merge done **and** trip retired |

### Master

| Item | Needs |
|---|---|
| Edit Species List | a master |
| Add Pictures to Master… | a master |
| Species Viewer | a master |
| Species Map | a master |
| Species Without a Website Picture… | a master |
| Maintain Master Database ▸ *every item* | a master |

### Albums

| Item | Needs |
|---|---|
| Find Trips in the Master… | a full master |
| Create Album | a full master |
| Write Album Blogs | an active album |
| Make the Blogs Clickable | an active album |
| Build Album Website | an active album |
| Manage Album Publication… | — |
| Album Species Viewer | — |
| Sync Placed GPS into the Index… | an active album |
| Export Album | an active album |
| Import Album | a master |
| Delete Album… | an active album |

### Utilities

| Item | Needs |
|---|---|
| Read card into unfiled… | — |
| Unfiled pictures | — |
| Create an Excel… | — |
| Create PhotoMechanic keyword files… | — |
| Create Small Master Database | a master |
| Install Small Master Database | — |
| Rebuild all Albums and the Master website… | — |

### Help

| Item | Needs |
|---|---|
| Connection status… | — |
| About | — |

---

## Diagnosing a dead menu

Work down this list; the first "no" is almost always the answer.

1. **Is anything active?** Look at the mode strip and the left column. Most
   greying is simply nothing selected in that scope.
2. **Is a master configured?** *Master ▸ Masters ▸ Repository Settings…*. An empty
   master path greys out the Master menu, half of To Master, and both Album
   creation items.
3. **Is this a SmallMaster install?** Then Create Album and Retire Trip stay
   greyed by design.
4. **For a pipeline step: has the previous step run for this day?** Hover the
   item — the tooltip says what is missing. Or look at the day's row in the
   yellow panel.
5. **For a To Master step: has the previous milestone happened?** Open the
   *Trip to Master Dashboard*, which shows the whole arc at once.
6. **For Developer Tools: is debug enabled, and have you restarted?** The flag is
   read when the menu is built.

## Two notes on freshness

- **The published-days scan is memoised for a few seconds**, so opening a menu
  does not re-walk the day folders every time. A Publish Day run rebuilds the
  whole menu, so this is invisible in practice.
- **Some panel state needs a click away and back.** Selecting the already-active
  trip or album is a no-op, so if a value looks stuck, select something else and
  come back — or restart.
