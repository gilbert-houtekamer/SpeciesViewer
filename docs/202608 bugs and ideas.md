# ideas

## from manual review, chapters 0–4 (2026-08-27)

- **Rename `virttrip` → `album` everywhere in code** — config keys (`[virttrip]`),
  folder names (`virttrips\`), internal identifiers. The GUI already says "Album";
  the manual currently has to explain the mismatch. (Concepts ch.0; user wants it
  in ch.19.)
- **Retire the term "Yellow Panel"** in the GUI and the manual — user suggests
  "Main Window" or similar. Cross-chapter + GUI label sweep.
- **Left column should follow the yellow panel's mode.** Today the left column
  leads and the panel follows; when you switch mode on the panel (Days/Trips/
  Album) the left column should switch with it. (Concepts ch.0.)
- **Multi-master support** → real `File ▸ Select Master` / `File ▸ Create Master`
  entries. Part of making SV installable rather than grown. (File ch.1; ch.20.)
- **Repository Settings — add a diagram** of the four repo paths (master, trips
  root, albums root, pictures root) and how they relate. Do after the ch.20
  install flow exists. (File ch.1.)
- **Species Settings — remove the "whole trip" scope** for a species-name change.
  Probably never tested; the viewers make a wrong name unlikely anyway. Leaves
  per-picture and per-day. (File ch.1.)
- **Species Settings — move "name display / language order" to Look & Feel.**
  It is a display preference, not a name-handling rule. (File ch.1.)
- **Consolidate master-maintenance actions.** `File ▸ Rescan master EXIF cache`,
  `Master ▸ Maintain Master Repository`, and `Rebuild all Albums and the master
  website` overlap in the user's mind. Decide how to present them as one group,
  and **gate the write-ish ones off for a read-only (small) master** — including
  `Clean Orphan Sidecars`, which still leaves many image-less XMPs on the
  smallmaster. (File ch.1.)
- **Remove `Developer Tools ▸ Call Testbed for trip day`** from the code —
  superseded by standalone tests. (File ch.1.)
- **Browser menu — add a "Master + Trip" combined scope**, matching the option
  the Trip Species Viewer already has. (Browser ch.2.)
- **Review keyboard shortcuts across SV.** Currently only Ctrl+D / Ctrl+G /
  Ctrl+Q. Decide which actions earn a key. (Trips ch.4.)


# refactor
## r

# bugs
## trips index
There must to two trip overview generators; one as used from trips, and one as in nature.
