# Uncertain identifications — plan

Status: **draft, 16 Nov 2026**. Backlog 4.7, together with the certainty design
(`D:\code\design consideration\SV – Certainty field for species IDs.docx`, 6 Nov).

## Why

Today a picture has a firm species name or a `? name` placeholder. Both lose
information. A lizard that is surely an *Oplurus* but not which one, or a darter that
is probably *Sympetrum fonscolombii*, has nowhere honest to go. The `? name`
placeholder joins nothing: no family, no place in the species order, no species page.
It is a kludge, and it retires with this work.

## What a picture's identification becomes

Three separate things, each with one job:

1. **What the name is**
   - a species: *Chalarodon madagascariensis*
   - a group: **"Oplurus sp."** (genus) or **"Libellulidae sp."** (family)
   - a complex: **"Bombus terrestris complex"**, a named set of species that a photo
     cannot tell apart.

   Every one of these is an ordinary entry in the species list, filed under its family,
   with a place in the species order and its own species page. Groups sort after their
   named relatives.
2. **How sure you are**: *certain* (the default), *probable* or *possible*. This is
   stored beside the name, never inside it. It is shown as *Sympetrum cf. fonscolombii*
   (probable) and *Crocothemis erythraea ?* (possible).
3. **One counting rule**: only a **certain species** counts. A group, an uncertain ID
   and a complex's members never add to trip totals, lifers, the master list or the
   website. A complex counts once, as itself.

**INVARIANT:** one function answers "does this picture count as a species?". Every
place that counts species asks it, and no place decides for itself. This works the
same way as `engine.topics.is_topic_node` for landscapes.

## The phases

Each phase can ship and be tested on its own.

### Phase 0: Move a master picture that has JPGs to another species

*Done 16 Nov 2026 (5bb4dd1), tested in the QA master. It also covers a target the master has never had,
"Master ▸ Undo Last Species Move…", and a keyword that keeps its sex/age/plumage words.
Tests: `tests/test_master_move_jpg.py`.*

Today "Move to existing species…" refuses any master picture with a JPG, because
moving the best-JPG pick isn't built. Splits were re-filed by hand: move the files,
then Refresh, then Recount. Resolving "Oplurus sp." to a species needs the real
thing.

**It works on the master's database, not on files or CSVs directly.** Most of it
exists: `db/master_mutate.move_picture_to_species` already moves **every** file of a
picture (RAW, .xmp, .dop, video such as .mov/.mp4, …). It also rewrites the sidecar
keyword and keeps a snapshot for undo (`undo_move`). It goes through the master's
durable write path (`MasterDB.apply_move_species`): a write-ahead note first, then
SQLite, then a CSV save shortly after. The CSVs stay the one truth, and SQLite is only
how we get there quickly and safely. It refuses only a picture that has a JPG. What
phase 0 adds:

- **Lift the JPG refusal.** The JPGs and -small.jpg move with the rest.
- **Redo the best-JPG pick for both species, old and new**, in the same durable
  write. The species' JPG map, the picture's best JPG and the species and ancestor
  best-picks are re-derived. `apply_delete_jpg` already does exactly this
  re-derivation for one species, so it gets reused, not rewritten.
- **A target the master has never had:** today it refuses ("file a picture there via
  the pipeline first"). It should create the species folder and row from the species
  list instead, as the hand procedure already allows.
- **Videos:** check that a .mov moves with its picture and never becomes a best pick.
- Show the moves first, then apply. This is re-point code, where the bugs historically
  cluster, so the tests come first: move a JPG picture there and back again, and the
  master must be byte-identical; undo must restore both species' best picks.
- The website follows at the next rebuild. Every master rebuild already sends each
  full-size picture to its current place.

Delete shares the same no-JPG gate. Once the move works, the JPG case for delete is a
small step, but it is a separate decision.

Phase 0 is useful on its own: the next split needs no hand-moving. **Recommended
first.** It can also follow phase 1, because the trip side doesn't need it: in a trip,
changing a name is just a keyword edit.

### Phase 1: Group names ("Oplurus sp.")

*Done 16 Nov 2026 (d084fdc), tested in the QA master. Tests: `tests/test_species_groups.py` and the
two group tests in `tests/test_master_move_jpg.py`.*

- **The rule:** `engine/species_count.py`, `counts_as_species(name)`. A group
  and a "? name" placeholder never count.
- **Stored without the dot:** the row is `oplurus sp`, its Dutch and English
  names read `oplurus sp.`. The name is also a folder, and Windows drops a
  trailing dot from a folder while macOS and the web server keep it. You can
  type sp, sp., spec., spp.
- **Filing:** `SpeciesClassification.addGroup`. The family comes from a
  relative in the list (offline), else from GBIF. No group above a family.
  On a converted list it gets a Sequence halfway to the next genus.
- **The audit came out smaller than feared:** most counters already pass through
  `engine/subspecies.py` (`fold_species_flags`, `species_names`). Teaching those
  two the rule covers the trip rollup, Freeze's totals, master Recount and the
  website's numbers. Switched by hand: Count Species (trip and album: listed,
  not numbered, never New), the lifer baseline, the master's live counts on a
  move or delete, the trip registry's species set, and the "only new species"
  blog filters.
- **Visible change:** an existing "? name" placeholder no longer counts either,
  so a master Recount may show families one species lower. That is the rule,
  not a bug.

- A name ending in " sp." is recognised as a group. The genus or family is looked up
  **locally** from a relative already in the species list, so it works offline. GBIF is
  only the fallback when no relative is known.
- It is filed as an ordinary species-level entry under that family, sorted last in its
  genus or family.
- Sex / age / plumage follow the family's rules, like any species.
- The counting function arrives in this phase. Then comes the audit: every place that
  counts species is switched to it. That is roughly 40 places: totals, lifers, Count
  Species, Freeze snapshot, trip registry, master recount and the website. **This is
  the bulk of the work and the risk.** A test runs a trip with an "Oplurus sp." picture
  through Count Species, Freeze and the master merge, and checks every number.

### Phase 2: Certainty

*Done 16 Nov 2026 (6b9c616), tested in the QA master. Tests: `tests/test_certainty.py`.*

- **Decided (option A):** a species row's `iSpeciesCollectionCount` means
  "counts as a species": 1 with at least one picture that counts, else 0, the
  sum above. "Has a page" is the picture count (`websupport.has_page`).
- Certainty is a fourth qualifier group (probable / possible; also cf., ?,
  waarschijnlijk, mogelijk) and a `certainty` picture column before Headline.
  Only those two words make a picture uncertain, so a stray value never
  un-counts a species.
- Counted in: the trip's daily counts (new this run), Freeze (the day's counting
  pictures), master Recount, the master's live counts on move, delete AND
  certainty change (same journal entry, no file moves), Count Species (new
  `#Sure` number in the day summary), album Count Species, lifer baseline,
  trip registry.
- Shown as "cf." / "?" after the name in the viewers and on website cards, plus
  one sentence in the picture text. An uncertain picture is its own group on
  the species page, so it shows beside the certain ones.
- The master's cache rebuilds once (schema 5).

- **Storage:** a keyword token beside the name in `XMP:Subject`, the same way
  sex/age/plumage are stored ("oplurus cuvieri, probable"). The sidecar stays the one
  truth. A new `certainty` column goes at the **end** of `data_pictures.csv`, and it is
  empty for certain, so existing rows need no rewrite.
- **Editor:** a Certainty row under Plumage with the buttons *probable* and *possible*.
  Clicking the selected button again returns to certain. A new species resets it to
  certain, and "Apply to selected" copies it.
- **Display:** the panel title shows the cf. / ? form. Grid thumbnails get a small ?
  badge.
- **Counting:** the counting function from phase 1 adds "certain only".
- **GBIF "Check online"** always checks the clean name.

### Phase 3: The "Uncertain" review view

*Built 16 Nov 2026, awaiting test. Tests: the phase 3 part of `tests/test_certainty.py`.*

- **Trip day:** a purple **?** button in the Image Viewer beside 📍 (No GPS):
  groups, probable / possible IDs and old "? names" (`is_uncertain_id`).
- **Master:** **Uncertain only** in the Master Species Viewer. The species list
  narrows to species with an open ID, labelled and sorted by family
  ("chamaeleonidae · furcifer sp."); the cards to those pictures
  (`MasterDB.uncertain_pictures`). It turns JPG only off, since an open ID
  often has no JPG yet. Resolve from the card menu: Move to another species…,
  or Sex / age / plumage / certainty…. A resolved picture drops out on the next
  redraw.
- **The visible invariant:** the master consistency check warns when a species
  row counts without a certain picture (or the other way round), and names the fix: Master ▸ Maintain Master
  Database ▸ Recount Species Counters….
  No count of open IDs. A mixed tally of groups, cf. and "?" tells you nothing
  (decided 16 Nov).
- Not built: a count column in the trip overview.

Like the No-GPS filter: a place to work through the hard cases with a field guide, or
once clear pictures turn up.

- **In a trip day:** an *Uncertain* filter button beside All / Species / No GPS in the
  picture grid. It shows groups, complexes, probable and possible IDs.
- **Across the master:** one view listing every uncertain master picture, grouped by
  family. From there you change the species (which needs phase 0 for pictures that have
  a JPG) or set it to certain.
- Possibly a count in the trip overview, like the GPS column, so a day with open
  questions is visible.

**Changing pictures in the master feels like throwing sand in the engine.** It has
to happen, because trips go away and the master is where an ID gets settled years
later. Four guards keep it safe:

- **One road in.** Every species change in the master goes through the phase 0 move.
  No second way edits names in the master.
- **Certainty is not a move.** Setting probable → certain changes one token in the
  sidecar and one column. Files stay where they are, and nothing is re-pointed. Only
  changing the *name* moves files.
- **Always reversible.** Every move has an undo, and it's tested both ways.
- **A visible invariant.** Master health checks that no group, complex or uncertain
  picture counts, and that every certain one does. A wrong count is a bug signal.

### Phase 4: Retire "?"

A repair tool, run as dry-run → review → apply over the master and every trip:

| Today | Becomes |
|---|---|
| `? oplurus` (a known genus or family) | `oplurus sp.` |
| `? phacelia`-style, part not recognised | listed for you to decide by hand |
| a name containing `cf.` | the clean name + *probable* |
| a name ending in `?` | the clean name + *possible* |

After that, the special cases for a leading "?" go
(`engine/speciesclassification.py` merge skip, `engine/trip_registry.py`), and typing
a new "? name" is no longer offered.

### Phase 5: Complexes, defined in SV

There's no import from Waarneming.nl. You define a complex in SV:

- a scientific name, a Dutch name and an English name;
- its member species;
- its family, taken from the members.

It appears in species search and as a recent-species chip with a small *complex* tag.
Certainty applies to it as well. Changing a picture from a complex to one member is a
normal species change (phase 0 in the master). This needs its own small design: where
the member list lives in the species list (a new column at the end), and how a complex
looks on its species page.

## Decided (16 Nov)

1. **ID note:** postponed. When it comes, it uses the picture's existing
   **description** field, not a new one. The note then shows in blogs and on the
   picture: full disclosure is a good thing.
2. **Website:** uncertain pictures appear in **blogs** for sure, captioned with the
   cf. / ? form. They probably appear on the master's species pages too, with the
   same caption.
3. **One counting rule** for trips and the master.
4. **Export:** not important. SV shows its own values, as it does for every CSV entry
   and metadata field.

## Later, not now

- Certainty per animal when one picture shows several species.
- A link to an iNaturalist observation, so an expert's confirmation can upgrade the
  level. Not likely soon: an integration needs iNaturalist to open its API for
  third-party apps like this one. Wait for that.
- Making a note required whenever the level isn't certain.
