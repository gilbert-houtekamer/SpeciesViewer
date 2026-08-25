# Documentation index

Start here when you are looking for "which file explains X".

## For using the application

| Document | Answers |
|---|---|
| [manual/](manual/) | **The reference manual.** One chapter per menu, every item explained, plus the Day/Trip/Album/Master concepts, the viewers and their filters, and a "why is this greyed out" table. Build it into one browsable/printable page with `python tools/svr_manual_build.py --open`. |
| [SpeciesViewer_Capabilities.html](SpeciesViewer_Capabilities.html) | What the application can do, chapter by chapter — the capability history. |
| [SpeciesViewer_Backlog.html](SpeciesViewer_Backlog.html) | What is planned, in progress, or parked, and why. |
| [camera_support.md](camera_support.md) | Which cameras have actually been tested, and what works on each — measured, not asserted. Regenerate with `tools/svr_camera_checklist.py`. |
| [viewer_keybindings.md](viewer_keybindings.md) | Every key that works in the three picture viewers. |
| [create_render_procedure.md](create_render_procedure.md) | Getting a DxO PhotoLab render as far as the website — the order of steps matters. |
| [refresh_master_database.md](refresh_master_database.md) | The admin reconcile: when the master's CSVs drift from what is on disk. |

## How the pieces fit together

| Document | Answers |
|---|---|
| [SpeciesViewer_DataFlow.md](SpeciesViewer_DataFlow.md) | Where pictures live, how a trip flows into the master, and why the delete step is the last arrow. **Read this first.** |
| [data_structures.md](data_structures.md) | The four CSVs that hold all biological knowledge and picture metadata, and how they reference each other. |
| [configuration_inventory.md](configuration_inventory.md) | Which `.ini`, `.json` and `.csv` files exist, which are per-machine, which travel with a trip, which are throwaway — and where a new setting belongs. |
| [trip_index_publication.md](trip_index_publication.md) | How a trip gets *listed* on the website — the web slug's seven homes, why the live index is always the base, and how to repair a listing by hand. |
| [SpeciesViewer architecture pictures.pptx](SpeciesViewer%20architecture%20pictures.pptx) | The hand-drawn architecture slides (PDF alongside). |

## Working documents

| Folder | Holds |
|---|---|
| [plans/](plans/) | Design plans, one per feature arc. Committed deliberately, so they travel between machines. |
| [design-docs/](design-docs/) | Rendered style and layout studies for the GUI. |
| `Specs GUI 2024-05-23.txt`, `bugs en ideeen.txt` | Older free-form notes, kept for reference. |
| [install_linux.md](install_linux.md) | Flet on Linux — historical, from the pre-Qt experiment. |
