# Appendix C — Developer tools

**Only present when "Enable debug functionality" is on** (Settings ▸ Global), and
only after a restart. These are diagnostics, written to find faults in the program: they report, they
do not repair. You will not need them in normal use.
The repair tools live in [Master ▸ Maintain Master Database](06-master.md).

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

## Audit Species Counters (dry-run)…

*Needs: a configured master.*

Compares the species counts in `species.csv` against the pictures actually
present, and lists ghosts (species whose pictures have vanished, with recovered
last-known filenames), count drift, and orphaned best-picks.

**Report only — it never modifies anything.** The report is also written to
`<master>\admin\status\species_counter_audit.txt`.

## Clean Orphan Sidecars (move to trash)…

*Needs: a configured master.*

Finds `.xmp` / `.dop` sidecars whose image is gone and moves them to a
recoverable trash folder. It shows the count and extension breakdown, and asks
before moving anything.

Their now-dangling rows in the master admin are cleared by the next
**Refresh Master Database — Apply**, so the natural order is: clean orphans,
then refresh-apply.

## Call Testbed for trip day

A developer hook that runs the testbed batch against a chosen trip day. Not
useful in normal operation.
