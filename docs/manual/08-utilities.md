# 8. Utilities

Things that do not belong to a single scope: the card-to-inbox ingest lane,
helpers that produce files for other applications, the travelling-computer
setup, and one site-wide rebuild.

| Item | Needs |
|---|---|
| Read card into unfiled… | |
| Unfiled pictures | |
| Create an Excel to keep track of birds seen. | |
| Create PhotoMechanic keyword files for next trip. | |
| Create Small Master Database | A master |
| Install Small Master Database | |
| Rebuild all Albums and the Master website… | |

---

## The unfiled lane

These two are here, away from the trip pipeline, on purpose: **emptying a card is
not a trip action**, because which trip it belongs to is exactly what has not
been decided yet.

### Read card into unfiled…

Copies a card into `<CurrentPictures>\<year>\<year> unfiled\YYYY-MM-DD` and
**claims nothing** — no trip, no Days entry.

That is the point: a stack of cards can be emptied in one sitting without
answering "which trip?" once per card. Filing happens later, at the inbox.

The pictures-root question is asked once and then stored — never silently
guessed. A guess is fine for pre-filling a field you can see; it is not fine as
the thing that decides where a card is written.

### Unfiled pictures

Shows the inbox — which now means switching the mode strip to **Unfiled**, where
the inbox lives as a panel.

It stayed in the menu because it is still how you ask for it by name when the
mode strip is not where you are looking.

From the inbox, a day has three non-destructive exits:

| Exit | Result |
|---|---|
| **File under a trip** | The day moves into that trip's picture root |
| **Claim as a Day** | It joins the [Days](03-days.md) list and goes to the master |
| **Move it yourself** | Nothing stops you |

Trips whose date range covers the day are listed first and preselected — a
suggestion, not a decision. Overlapping ranges are normal (an album over the same
dates, say), so the whole list stays reachable.

> **SpeciesViewer never deletes an unfiled day.**

---

## Helpers for other applications

### Create an Excel to keep track of birds seen.

Builds an `.xlsx` checklist from a source species list — the paper-and-spreadsheet
companion for a trip.

The source list is chosen by browsing to it, with its columns mapped in the
dialog, and `.xlsx` files are read directly. The generated columns and formulas
are a settled design; they are meant to be used, not redesigned per trip.

### Create PhotoMechanic keyword files for next trip.

Generates PhotoMechanic "snap" keyword files from the species you expect on the
next trip, so that tagging in PhotoMechanic uses the same vocabulary
SpeciesViewer will later read back.

PhotoMechanic remains a supported parallel path to the built-in card ingest — it
has rich functionality that the SD-card lane has not reached parity with.

---

## The travelling computer

Two halves of one job: taking a usable copy of the collection on the road.

### Create Small Master Database

*Needs: a master.*

Copies the master's biota files — **JPG and XMP only**, no RAWs — into a compact
`smallmaster.zip`. Paths inside are relative, so it can be unpacked anywhere.

The snapshot records its date, and sidecars with no picture behind them are left
out.

Use it before a trip: the laptop then knows your whole collection well enough to
answer "have I had this before?" without carrying terabytes.

### Install Small Master Database

Unpacks and configures a Small Master on this machine.

> A SmallMaster install is **read-only-locked**. Actions that would write to the
> collection as a whole — Create Album, Retire Trip to Album — check for a *full*
> master and stay greyed. This is deliberate: a partial collection must never
> become the authority.

---

## Rebuild all Albums and the Master website…

Re-renders and re-uploads the album sites and/or the master site, so that a fix
to a page template or the rendering code reaches all of them.

This is the site-wide sweep. Its single-scope siblings are
[Master ▸ Maintain Master Database ▸ Rebuild Entire Website](06-master.md) and
[Albums ▸ Build Album Website](07-albums.md).

Reach for it when you have changed something shared — a stylesheet, a template, a
piece of rendering code — and need every published page to pick it up. A
per-album copy of a shared asset goes stale silently otherwise, and there are
many albums.
