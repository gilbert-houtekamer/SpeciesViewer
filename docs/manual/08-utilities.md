# 8. Utilities

Things that do not belong to a single scope: the card-to-inbox ingest lane,
helpers that produce files for other applications, the travelling-computer
setup, and one site-wide rebuild.

| Item | Needs |
|---|---|
| Read Card into Unfiled… | |
| Unfiled Pictures… | |
| Bird Checklist (Excel)… | |
| PhotoMechanic Keywords… | |
| Create Small Master… | A master |
| Install Small Master… | |

> Two items here end in a full stop and read as sentences ("Create an Excel to
> keep track of birds seen."), unlike every other menu item. Noticed while writing
> this manual.

---

## The unfiled lane

These two are here, away from the trip pipeline, on purpose: **emptying a card is
not a trip action**, because which trip it belongs to is exactly what has not
been decided yet.

### Read Card into Unfiled…

Copies a card into `<CurrentPictures>\<year>\<year> unfiled\YYYY-MM-DD` and
**claims nothing** — no trip, no Days entry.

That is the point: a stack of cards can be emptied in one sitting without
answering "which trip?" once per card. Filing happens later, at the inbox.

The pictures-root question is asked once and then stored — never silently
guessed. A guess is fine for pre-filling a field you can see; it is not fine as
the thing that decides where a card is written.

### Unfiled Pictures…

Shows the inbox: it switches the mode strip to **Unfiled**, where the inbox
lives as a panel. The menu item is there for when the mode strip is not where
you are looking.

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

### Bird Checklist (Excel)…

Builds an `.xlsx` checklist from a source species list — the paper-and-spreadsheet
companion for a trip.

The source list is chosen by browsing to it, with its columns mapped in the
dialog, and `.xlsx` files are read directly. The generated columns and formulas
are a settled design; they are meant to be used, not redesigned per trip.

### PhotoMechanic Keywords…

Generates PhotoMechanic "snap" keyword files from the species you expect on the
next trip, so that tagging in PhotoMechanic uses the same vocabulary
SpeciesViewer will later read back.

You do not need Photo Mechanic: *Trips ▸ Read SD-card* copies and culls the
card. This item is for when you still tag in Photo Mechanic, on the road or out
of habit.

---

## The travelling computer

Two halves of one job: taking a usable copy of the collection on the road.

### Create Small Master…

*Needs: a master.*

Copies the master's biota files — **JPG and XMP only**, no RAWs — into a compact
`smallmaster.zip`. Paths inside are relative, so it can be unpacked anywhere.

The snapshot records its date, and sidecars with no picture behind them are left
out.

Use it before a trip: the laptop then knows your whole collection well enough to
answer "have I had this before?" without carrying terabytes.

### Install Small Master…

Unpacks and configures a Small Master on this machine.

> A SmallMaster install is **read-only-locked**: it is a copy for viewing,
> publishing and importing albums and trips. What changes the collection as a
> whole — Create Album, Retire Trip to Album — you do on the computer with the full
> master, so there is always one master that is the original.

---

## Rebuilding the websites

Moved to [Master ▸ Rebuild Websites…](06-master.md#rebuild-websites): one window
for the master site and all albums.
