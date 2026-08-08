# Plan — #2: Pick picture properties (juv / male / female / plumage)

Backlog topic 7 / rating. Companion to #3 (JPGSize fix, done) and #1 (regenerate one
species page, next). This is the *properties* picker only — full re-classification
(change the species) is a separate, larger topic and explicitly out of scope here.

## The mechanism (already in place — we're adding a UI, not new pipeline)

The "suffix" is **not a separate stored field the user edits directly**. It is a
**qualifier token inside the species keyword** (`XMP:Subject`), e.g. `aasgier, juv`
or `neophron percnopterus male`. The pipeline derives everything from that string:

- `specieslib.getBirdInfo(subject)` (specieslib.py:73) tokenises the subject and
  recognises a fixed vocabulary, storing three single-valued fields on the picture:
  - `malefemale`  ← male/man · female/vrouw · couple/paar/paartje
  - `ageintext`   ← juvenile/jong/juv. · immature/onvolwassen/imm. · adult/volwassen/ad.
  - `summerwinter`← breeding/summer/zomer (`br.`) · non-breeding/winter (`non-br.`)
- Set at read time in `addSpeciesInformation` (projectlib.py:823) from the JPG/XMP subject.
- Consumed in two places:
  - **Caption** — token 101 (websupport.py:772) → "De foto toont een mannetje / vrouwtje /
    jonge <soort> …" and plumage sentence.
  - **Selection** — `getPictureAgeSexePlumage` (projectlib.py:988) builds a "diversity"
    key so `addOneSpeciesPage` (websupport.py:1352) shows the best pic **per** age/sex/
    plumage group (adult + juv + male + female can all appear), instead of N near-dupes.

**Consequence:** if the picker writes the qualifier token into `XMP:Subject`, the whole
chain (getBirdInfo → caption + selection) already works. No new pipeline plumbing. The
change only lands on the website after a re-read/recompute of that species → ties to #1.

## Design

A small **qualifier picker** attached to the species/rating editor, that toggles a
single value in each of the three categories on the selected picture(s) and rewrites
the qualifier tokens in the subject, reusing the existing species-subject XMP write.

- **Form:** an inline toggle-button row grouped by category, single-select within each
  (they're single-valued): `♂ male · ♀ female · pair` | `juv · imm · ad` | `summer · winter`.
  Empty = unset. Menu + key both is the ideal end state ([[feedback_prefer_context_menus]]);
  start with the button row.
- **Write:** parse the current subject with the same vocabulary getBirdInfo uses, replace
  the token(s) in the touched category, re-emit `"<species>, <tok1>, <tok2>…"`. Idempotent:
  toggling off removes the token; never duplicates. Write ONE canonical token per category
  (English: `male`/`female`/`couple`, `juvenile`/`immature`/`adult`, `breeding`/`winter`)
  so round-trips are stable — but keep getBirdInfo's alias list as the accepted input.
- **Label:** surface the current qualifier on the card (memory notes `species, juv` is
  not shown today) so the state is visible after setting it.

## The scope decision (needs your call)

The motivating example (aasgier) is **master** pictures — read-only in the Master Species
Viewer today. So:

- **Option A — trip-first.** Picker lives in the culling / Read&Summarize / Rating editor
  on TRIP pictures (already a writable XMP path). Flows to master on the next commit. Small,
  reuses everything. Does NOT fix already-committed master pics.
- **Option B — master-edit too.** Extend the Master Species Viewer to write the qualifier
  back to the master picture's sidecar + master DB. Directly serves the aasgier case, but
  it's a master mutation (topic 3, master read-mostly) + needs a recompute of that species.

Recommendation: **A now** (get the picker + grammar right on trip pics, low risk), then B
as a deliberate master-edit follow-on once #1 gives us the per-species recompute that both
B and #3's stale-data cleanup need anyway.

## Code sites

- Vocabulary / parse: `specieslib.getBirdInfo` (specieslib.py:73) — single source of truth
  for accepted tokens; reuse for the picker's parse, don't fork it.
- Write path: the species-subject writer behind the culling/rating species editors
  (`culling_species.py`, `image_viewer._open_culling_species_dialog`,
  `large_image_view` species section) — the picker appends qualifier tokens through the
  same XMP:Subject write.
- Caption: token 101 (websupport.py:772). Selection: `getPictureAgeSexePlumage`
  (projectlib.py:988) + `addOneSpeciesPage` (websupport.py:1352). No change needed —
  they already consume the derived fields.

## Open questions

1. Scope A vs B (above) — the one real decision.
2. Which categories to expose first — sex + age are the clear wins; plumage
   (summer/winter) optional.
3. Multi-select on a selection of pictures (set juv on N at once) — reuse the existing
   "commit to selection" batch model from the species editor.
