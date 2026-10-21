# 0.5.0 — what must not ship: review list

*Made 2026-10-21 from a read-only sweep of the code (the full list, 111 lines, is in
[no_ship_text_sweep.csv](no_ship_text_sweep.csv) — open it in Excel). Nothing has been
changed yet. Tick or strike each rule; I then do them as one commit per rule.*

## Already fine

- **The installer** takes only the program packages, `assets/`, `HTMLtemplates/`, the
  site defaults, LICENSE and NOTICE. `one-off/`, `batch-future/`, `tests/`, `docs/`,
  `bug.png` and the test masters never reach a user.
- **Developer menu items** (Call Testbed, Audit Species Counters (dry-run), the status
  consoles) are under *File ▸ Developer Tools*, which exists only with debug on.
- **Your name / "his request"** appear only in code comments, never in a text on screen.

## Proposed rules

### Rule 1 — the log names a step the way the menu does (49 lines)

Every pipeline step starts and ends its log lines with its **program name**:
`sv6Freeze: Freeze data for project …`, `svv4BuildWebsite succeeded.`,
`✅ svMasterRebuildWeb complete in 222.1s.`

- Replace the program name by the **menu name**: *Freeze*, *Build album website*,
  *Rebuild master website* …
- Drop the `… succeeded.` lines that follow a `✅ … complete` line — they say it twice.
  Keep `… failed: <reason>` (in the menu's words).

### Rule 2 — no internal file names in what you read (41 places)

Three kinds:

| Kind | Example | Proposal |
|---|---|---|
| Buttons / tooltips | button **Save blog.ini**; tooltip *…to trip.ini — WITHOUT rebuilding…* | plain words: **Save blog settings**; *…saves the trip's settings* |
| Routine log lines | *Reading master data_files.csv from …*, *Copied speciescollection.csv from master*, *Master trip_admin.csv updated…*, *Recounted species.csv: …* | → debug, or plain words where the line matters (*Recounted the species counters: …*) |
| Errors | *Failed to write data_pictures.csv: …* | **keep the file name** — it is what you (or I) need to fix it — but lead with plain words: *Could not save the master's picture list (data_pictures.csv): …* |

Dialog texts that EXPLAIN a file on purpose stay (Export Trip: *a small ZIP holding the
trip's settings (trip.ini)…*; Manage Site Publication's admin option about trip_admin.csv).

### Rule 3 — internal terms (10 places)

- **UPN** (4): *Deleted file … from master UPN …* → *from master picture …*; tooltip
  *folder + UPN search* → *folder + picture-name search*.
- **frozen / statemachine** (6): *Step '…' is disabled: workflow frozen after WriteCommit*
  → *…this day is published*; *day_is_frozen: could not read statemachine…* → debug.
  *Freeze* as a step name stays.

### Rule 4 — small dev words (4)

*Seeded … kingdom stub page(s)* → debug. *debugging* in two step-dialog help texts → read
them and reword. *Enable debug functionality* stays (it is the switch).

## Not in this list — decide separately

- Texts that are not Python strings: `HTMLtemplates/` (website pages) and the manual.
- Error messages that come from Python itself (`{e}` holes) — they stay; they are the clue.
