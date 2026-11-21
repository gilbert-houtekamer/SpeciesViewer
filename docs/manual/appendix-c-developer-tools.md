# Appendix C — Developer tools

**Only present when "Enable debug functionality" is on** (Settings ▸ Global), and
only after a restart. These are diagnostics, written to find faults in the program: they report, they
do not repair. You will not need them in normal use.
The repair tools live in [Master ▸ Check the Master…](06-master.md#check-the-master).

## Trip Status Console…

*Needs: a configured master.*

Scans **this machine's** trips and albums, scores each against the master
(which pictures are already integrated, which would be lost if the trip were
deleted now), writes a per-machine CSV shard, renders a private HTML console and
opens it in your browser.

It asks two questions first:

- *Generate picture thumbnails for the drill-down?* — slower, because it extracts
  RAW previews, but makes the console browsable.
- *Upload this machine's status shard to the web?* — so the other machine can
  merge it in later.

The "is it safe to delete this trip?" question is now mostly answered
automatically during retire; this console is the manual fallback, mainly for the
two-machine (laptop + master) case and for development.

## Merged Status (all machines)…

*Needs: a configured master.*

Pulls every machine's uploaded shard down from the web and renders the
integrated overview — one row per trip, one column per machine. No local scan, so
it is fast. Useful when a trip was worked on the laptop and you are sitting at
the desktop.

## Legacy Steps ▸

The individual pipeline steps, from before they were merged into
*Read & Summarize* and *Publish Day*. They remain for two narrow cases: a
read-only CSV refresh, and the freeze-only edge case. (They used to sit under
*Trips ▸ Advanced ▸ Legacy*.)

| Item | Was |
|---|---|
| **Read Directory** | The first half of Read & Summarize (`sv2`) |
| **Write Summary** | The second half of Read & Summarize (`sv3`) |
| **Freeze Data** | The first half of Publish Day (`sv6`) |
| **Write Website Commit** | The second half of Publish Day (`sv7`) |

If you are not sure you need one of these, you do not need it.
