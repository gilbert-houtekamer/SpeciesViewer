Design plan

- we want a method that allows the product to follow the new vision from the biologists
- the species is the key concept, and the species name is stored in a metadata in the pictures
- the "hierarchy" is based on biota, and based on levels currently used by SV, from Kingdom to Species.
- the sort-order at any level can be influence with an order field, as used in a particular field, birds and mammals.  
- it would be to allow multiple common names. We can possibly do this with Python lists in a CSV, we have done that before.Not so much for output, but to search in (the English and Dutch lists that will get extra entries)
- maybe we need more tables, but we can also provide more functions. eg species that were renamed can be a "species forward" indicator compared a full-function species.  This may be a way to handle both old names (like family sylvia birds) and aliases where multiple names are used for the same species. See the Backlog:

Aliases for common names — the collection settled on its own names (IOC 10.5 with GBIF), and every list from outside disagrees somewhere. Comparing the two Brazil lists put a number on it: 86 of the 576 birds they share are named differently, and merging all four lists in the trip found 98. Three different problems wear the same face:
Spelling convention — Gray/Grey, Golden-Plover against Golden Plover. Mechanical, and the widest slice of the 98.
A genuinely different name for the same bird — Bank Swallow against Sand Martin, Mealy Amazon against Mealy Parrot. No rule derives one from the other; it has to be recorded.
A split or lump, where an old name does not mean one species any more. This is the dangerous one: an alias that guesses here silently files a photo under the wrong bird.

In the conversion, changes in families are normal, and automatically approved provided the have the same order (for birds). Probably more generic the grouping that observation does, eg bees can ove a different but no wasp or butterfly.

the "conversion" can ideally be done by changing all higher levels, and rename of species that are now in new families.

It feels that we can make and new tables indenpendent of an actual conversions.

---

## Step 2a — the new tables for birds, beside today's list (Claude, 15 Oct)

Read-only on the master; writes only to an output folder. Tool: `tools/svr_taxonomy_build.py`.

**In:** today's species list (birds only, 10,680), species.csv (picture counts), AviList v2025,
GBIF species match (cached, so a re-run costs nothing).

**Out:**
- `taxa.csv` — one row per taxon, Kingdom → Species: scientific name (GBIF), rank, parent,
  sort number + where it came from, Dutch and English names as lists (first = shown).
- `names.csv` — the alias table: a name, its language, the scientific name it points to,
  and what kind it is (old scientific name, merged species, other English name…).
- `report.md` — what would change against today's list, pictured species first.
- `preview.txt` — the bird tree in the new order, to read through.

**Rules** (from the answers above):
- GBIF decides name and placement; where GBIF leaves a level empty, today's value stays.
- DOUBTFUL in GBIF = same name.
- Sort number: AviList `Sequence` for the GBIF name, else for today's name, else the
  same epithet inside the same family (marked for review), else right after its genus.
  Families and orders sort at their lowest member.
- A GBIF merge is never followed — see "Decided 15 Oct (after the whole list)" below.
  Only today's subspecies entries fold into their species (an alias row, marked for
  review when it has pictures).
- Names: today's Dutch and English first; AviList's English added as an alias when it differs.



**Decided 15 Oct (after the whole list):**
- **GBIF merges are never followed.** A species GBIF calls a synonym of another species stays as it
  is — own name, own row, GBIF's placement — with the note "GBIF: synonym of X" (423 species, 29 with
  pictures). Too many of GBIF's merges are wrong: recent Asian damselflies lumped into a relative,
  *Polites mardon* → *P. sabuleti*, the European Large Skipper → the Asian *Ochlodes venata*.
  Later choice per species: delete it, or keep it. Genus moves (*Anas → Spatula*) are still followed.
- **A common name that finds two species** (a split, or a duplicate in today's list): detect it when
  a species is assigned by name and ask which one is meant. Workaround: type the scientific name.
  Low priority.

---

## First conversion test — Taxonomy master (TaxoGreece), 15 Oct

`tools/svr_taxonomy_convert.py` (dry run by default, `--apply`, `--undo <log>`; refuses Dropbox and
the main master). Tables built for this master in `D:/code/taxonomy_trial_greece`.
Undo log: `C:\SpeciesViewer\Taxonomy\nature\admin\taxonomy_convert_20261015-214809.csv`.

**What worked**
- 125 species folders, 3,571 files moved (same disk: renames), 436 web pages to `.trash`, 146 empty
  folders removed. No numbered folder left; `022 lepidoptera` + `lepidoptera` are one folder now.
- 5 names changed: 2 genus moves (*Clanga → Aquila pomarina*, *Anisantha → Bromus tectorum*),
  2 subspecies folded, 1 Latin ending (*Colias crocea → croceus*).
- Admin check against disk (`admin_rebuild.plan_rebuild`): REPOINT 0, ADD 0, SPECIES 0 — the same
  75 REMOVE as before (landscape pictures: the check only scans `biota/`).
- The app's readers load the four CSVs without a broken link; totals unchanged (1,435 pictures).
- Website rebuilt (svMasterRebuildWeb, local): 432 pages, **0 broken links** in 13,070 — after the
  full-size JPGs were copied by hand (below).

**What a real conversion still needs**
1. **Order on screen = alphabetical now.** The Biota Browser sorts children by path
   (`gui/biota_source.py:89`), the website by `sorted(Leaves)` (`engine/websupport.py:863, 1490`);
   with the numbers gone both are A–Z. Code step: sort by Sequence.
2. **Sequence is dropped** the first time the app writes the species list
   (`speciesclassification._csvRow` knows six columns).
3. **Full-size pictures are not republished.** A rebuild republishes HTML and small JPGs only; the big
   JPGs reach the site only in the run that adds them. A conversion needs a "republish all pictures"
   step (for the test: copied by hand into the local site).
4. **The old numbered pages stay on the website** (120 folders locally): publishing never deletes.
   Needs a cleanup step — and redirects on a real server.
5. **Old scientific names in pictures** (XMP) don't resolve until aliases are in the lookups.
6. Trips and albums that point into the master: not converted (low priority, automatic later).

**Found on the way, not ch.6**
- `data_pictures.csv` Kingdom…Family are filled one level too high (`projectlib.py:973`: Family holds
  the order, Kingdom holds "biota"). Only `trip_registry.py` reads them. The converter writes them the
  same way, on purpose.
- The admin check reports landscape pictures as REMOVE (it doesn't scan `topics/`).
