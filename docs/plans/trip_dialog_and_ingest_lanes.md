# Trip dialog modes + SD-card ingest lanes

*Raised 2026-08-10, after Create Trip overwrote greecedb's identity with
"Nederland 2026". Two proposals from the user, plus the small fixes that already
went in. Written to be reacted to, not to be built silently.*

## What actually went wrong (the evidence, so the design answers the real thing)

`TripCreationDialog` opens with the Trip Root folder pre-filled from
`_config["trip"]["tripbiotadatabase"]` — **the trip you were working on** — but the
initial value goes in through the field's constructor, *before*
`textChanged → on_trip_root_changed` is connected. So the handler never ran for it:

- the folder shown was `greecedb`
- every other field (web root, name, dates, texts) came from `_config` / whatever
  was active before → `/trips/Nederland 2026`
- `trip_name` written into `trip.ini` is **derived from the Trip Web root field**
  (`Path(web).name`), not from a name field of its own

→ Saving wrote B's identity into A's folder. And re-picking the same folder could
not repair it, because `textChanged` only fires on a *change* — which is exactly
the "the Trip Web root cannot be changed" symptom.

**Fixed already (small, uncontroversial):**
1. `_sync_fields_to_trip_root()` at the end of `__init__` — the form is always
   loaded from the folder it is showing.
2. `on_trip_root_changed` reads with `.get()` so a partial trip.ini cannot crash
   the dialog on open.
3. A confirm when the target folder already holds a **different** `trip_name`,
   naming both trips, default No.

Those three make the dialog *honest*. They do not make it *unambiguous* — which is
the user's point below.

## Proposal 1 — the dialog is overloaded; ask for the mode first

Today one screen does three jobs with three buttons of equal weight (**Create
Trip**, **Update Trip Settings**, **Select**), all sharing one set of fields whose
defaults come from the previously active trip. Nothing on screen says which job
you are doing, so the defaults are wrong for two of the three.

**Proposed:** a short first question — *"What do you want to do?"*

| Mode | Defaults come from | Writes |
|---|---|---|
| **Work on an existing trip** (Select) | the trip you pick | nothing |
| **Change this trip's settings** (Update) | the trip you pick | its own `trip.ini` |
| **Start a new trip** (Create) | **empty**, + a suggested folder name and web slug from the new trip's name | a NEW folder's `trip.ini` |

The win is in the third row: a new trip currently inherits a random previous
trip's dates, texts, banner and web root, and the user has to notice and clear
each one. With the mode known up front, "new" can mean new.

Create mode also folds in the two-step flow that nothing enforces today (type a
folder name → **Create Folder** → *then* the root field points somewhere new):
in a dedicated Create screen the folder is made as part of saving, so there is no
window in which the root still points at a live trip.

**Cost:** a mode chooser + splitting the current tab's defaults three ways. The
tabs (Images & Dates, Trip Text) and `generate_trip_ini` stay as they are.

## Proposal 2 — two ingest lanes: "this trip" and "unfiled"

Today Read SD-cards asks for a target folder every time, and getting it wrong is
silent. The user shoots first and decides which trip it was later — the dialog
asks a question he often cannot answer yet.

**Lane A — into the current trip.** No target picking at all: the destination is
the active trip's picture root, shown read-only ("Reading into: 2026 North
Greece"). This is the common case and it cannot be aimed at the wrong trip.

**Lane B — unfiled.** Destination is a fixed, boring place derived from the date:
`<CurrentPictures>/<year>/<year> unfiled/YYYY-MM-DD/`. No trip is chosen, nothing
is claimed. The day then shows up in the **Days** lane (the Working Days machinery
already handles ad-hoc days), where a later step **moves the day folder into a
trip** — a directory move plus a registry update, optionally with SV suggesting
the trip whose date range contains the day.

**Why this is the right shape:** it separates *getting the pictures off the card*
(urgent, must not fail, must not ask questions) from *deciding what they belong
to* (can wait, needs the map/species/dates to be visible). It also gives the
"which trip?" decision a screen where the answer is knowable.

**Open questions for the move step:**
- What follows the pictures — the `.xmp` sidecars go along, but does anything in
  the master/trip registry already point at the old path? (For an un-ingested day:
  no. That is why moving BEFORE Read & Summarize is cheap and moving after is not.)
- Does the move refuse once the day has been frozen/committed? (It must.)
- One day per move, or a multi-select?

## Filing a day: rename, merge, and what it costs the caches

*Raised by the user 2026-08-10, after lane B shipped: "moving a day into a trip
can be effectively a file copy, but the name conflict is real — so the export for
a day must offer the rename. Which raises questions about the metadata caches."*

**Two conflicts, and they are not the same conflict.**

1. **The day folder already exists in the trip.** v1 refuses. But two shoots on
   the same date in the same trip genuinely belong in the same day folder, so
   refusing is only right while file names might collide. The better rule is to
   **merge file by file with the rules `copy_card` already has**: identical
   content (sha256) is skipped, same name + different content is auto-renamed to
   the next free name. That logic is written, proven on every re-read, and should
   be reused rather than restated here.
2. **The file names do not match the trip's convention.** An unfiled day is named
   by the camera (`N5240222.ORF`); the trip wants `2026 NL - N5240222.ORF`. So
   filing should OFFER "rename to this trip's prefix" — the trip's prefix is
   derivable from its existing day folders, so it can be proposed, not typed.

**⚠ The sharp edge: a rename must move the whole STEM GROUP.**
`N5240222.ORF` never travels alone — `.xmp` (species, rating, GPS, headline —
every culling decision), `.dop` (PhotoLab's recipe: colour, crop) and any
`_DxO.jpg` share its stem and are matched to it BY NAME. Rename the RAW and leave
the sidecars behind and the pictures survive while all the work on them is
silently orphaned. This is the same rule as
[the derived-file policy](../../CLAUDE.md): a derived file follows its source, and
the group is the unit of every move. Any rename step must be group-wise and must
verify the group is complete before it moves anything.

**What the caches actually cost after a move or rename** — much less than feared,
because they are all keyed by path and none of them is a source of truth:

| Cache | Keyed by | After a move/rename | Cost |
|---|---|---|---|
| Sidecar cache (`.sidecar_cache`, per folder) | path + mtime_ns | misses → re-reads via exiftool | seconds per day |
| Preview cache (per-user app dir) | source path | misses → re-extracts embedded previews | one slow grid open |
| Global EXIF cache | path | stale entries, never wrong answers | nothing |

None of them needs invalidating by hand: a miss is a re-read, and a stale entry
for a path that no longer exists is simply never consulted.

**What DOES cost, and sets the rule:** once a day has been through **Read &
Summarize**, its filenames are written into the day CSVs (`data_files.csv`,
`data_pictures.csv`), and after **Freeze** they are in the trip's active CSVs and
the master. A rename then is a data migration, not a file operation.

> **Rule: rename while the day is unfiled. After Read & Summarize, never.**

Which is an argument for doing the rename AT filing time, as part of the move —
the one moment when it is still free.

## Recommendation

Do **Proposal 2 first**. It is the one that bites on every card read, it is
additive (a second menu entry + a move action; nothing existing has to change
shape), and it needs no decisions about the trip dialog. Proposal 1 is the deeper
correction but touches a dialog that three lanes already depend on, so it wants
its own session and its own test pass.

Neither should start before the current SD-card/culling fixes are confirmed live.
