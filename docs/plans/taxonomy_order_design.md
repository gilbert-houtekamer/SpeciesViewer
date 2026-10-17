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

---

## Open: a merge that should become subspecies (15 Oct)

GBIF folds *Caracara cheriway* into *Caracara plancus* — correct as a species, but the two birds are
the subspecies *C. p. cheriway* (Northern Crested Caracara) and *C. p. plancus* (Southern). Wanted:
keep both, saved as `caracara plancus cheriway` and `caracara plancus plancus`, not one lump.

To think through before building:
- A third choice in the review page for a GBIF synonym: *keep apart* · *merge* · **keep as
  subspecies of X** (old epithet becomes the third word; the nominate one gets the species
  epithet twice).
- That makes subspecies a real level under the species, not a species-level entry as today
  (*Motacilla flava feldegg*). Then today's default "fold subspecies into the species" may flip
  to "keep". Website/browser: the species page shows its subspecies together.
- Folder and XMP: does the picture's keyword become the trinomial, or stay the old name and
  resolve through an alias?

---

## Genus moves checked by AviList and MDD (16 Oct)

Reviewing the 83 pictured genus moves showed that GBIF also moves *backwards*: *Melaniparus
albiventris → Parus*, *Psittacara → Aratinga*. Of the 13 the author kept, the 10 birds are exactly
among the 16 where **AviList still uses today's name**. The mammal reference, the **Mammal Diversity
Database** v2.5 (July 2026, 6,904 species, Zenodo 10.5281/zenodo.21654811), keeps today's name for
all four mammal moves (*Tapirus*, *Tragelaphus*, *Herpestes*, *Nesotragus*).

`svr_taxonomy_build.py --mdd <MDD csv> --reference-check` builds a second list in which a GBIF genus
move is followed only when AviList (birds) or MDD (mammals) uses the new name too. Side by side:
`D:/code/taxonomy_trial` (GBIF) and `D:/code/taxonomy_trial_reference` (GBIF checked by AviList and
MDD). They differ in 161 species, 8 pictured — the 6 birds and 2 mammals the rule keeps that the
review had marked "follow".

With `--mdd`, mammals also get a species order: MDD's file is sorted by its 27 orders
(phylogenetic, `phylosort`) and then suborder … family, genus, species, so the row position is the
sequence — orders in evolutionary order, families grouped. MDD is ahead of GBIF on three of the
author's mammals (*Canis → Lupulella mesomelas*, *Cephalophus → Cephalophorus natalensis*,
*Cebus imitator* folded into *C. capucinus*); with GBIF deciding names these stay as they are.

---

## Decided 16 Oct: birds from AviList, mammals from MDD — never GBIF

Supersedes "GBIF decides" for these two groups. The species list ships AviList (birds) and MDD
(mammals) — both CC BY 4.0, credited in NOTICE.md — so no bird or mammal is ever looked up in GBIF.
GBIF stays for every other group. Today's names are matched to AviList without GBIF: same name
(9,229), same English name in the AviList, Clements or BirdLife column (1,146), the protonym (16),
an AviList subspecies (200, 24 pictured), the epithet within the family for review (42), no match
(47, 4 pictured — answered). Matching order matters: an AviList subspecies within the same genus
comes before the English name, and no rule takes a species another name already matches exactly.
List: `D:/code/taxonomy_trial_reference/birds_vs_avilist.csv`.

**Subspecies** (the author, 16 Oct): pictures of a subspecies sit in the **same folder as the
species** — no folder of their own — and a subspecies is kept apart only when it has a clear common
name of its own (Hooded Crow, Yellow-billed Kite, Northern Crested Caracara, Hume's Whitethroat).
The species page links to its subspecies, on the website and in the app.

New actions in `tools/taxonomy_overrides.csv` (read by the AviList builder, next step):
`avilist` — today's name is this AviList species (an alias); `subspecies` — today's name is this
AviList subspecies.

**Built 16 Oct — the third list.** `svr_taxonomy_build.py --authority` with the AviList *extended*
xlsx, `--mdd` and `--mdd-syn` (MDD's Species_Syn file) takes every bird from AviList and every mammal
from MDD; GBIF is asked only about the other groups. Today's names resolve in this order: the
author's overrides (`merge`, `subspecies`, `avilist`, `keep`), the same name, today's subspecies
entry, an AviList subspecies (folded into its species), the English name (AviList, Clements and
BirdLife columns; MDD main and other names), a synonym (AviList protonym; MDD's 66,616 old
combinations), the epithet within the family (review), and otherwise kept as its own species
(review). The list's English name is shown; today's English name stays searchable; today's Dutch
names carry over. Decided subspecies get rows of their own under the species. A name the list uses
for a species of its own (the extinct aurochs) gets no alias.

Result for the main master: 25,982 species (all of AviList and MDD), 0 checker failures.
Birds: 9,229 same, 1,146 by English name, 16 by protonym, 19 subspecies by choice, 176 AviList
subspecies folded, 42 + 43 for review (4 pictured). Mammals: all 106 resolved without GBIF.
Side by side: `D:/code/taxonomy_trial` (GBIF), `…_reference` (GBIF checked by AviList/MDD),
`…_authority` (AviList/MDD) — each with its own review page.

Still to do before the next conversion: the converter must put subspecies pictures in the folder of
their species (today it would make a subfolder), and Dutch names for the ~14,800 species of AviList
and MDD that the author's list never had.

---

## Second conversion test — Taxonomy 3 (0.4.2, Spain + Brazil), 17 Oct

Birds from AviList, mammals from MDD (`--authority`), subspecies beside their species.
449 pictured species, 28 new names, 4 subspecies; 17,602 files moved in 19 s, 1,272 web pages to
`.trash`, 493 empty folders removed. Undo log:
`C:\SpeciesViewer\Taxonomy 3\nature\admin\taxonomy_convert_20261017-093057.csv`.

- Totals unchanged: 6,459 pictures, 6,549 picture rows, 20,546 file rows. No numbered folder left.
- Admin check: REPOINT 0, ADD 0; REMOVE 407 = landscape pictures (the check doesn't scan `topics/`);
  **SPECIES 42 = the four subspecies** — their pictures sit in the species folder by design.
  ⚠ The Master menu's heal would "reclassify" them back to the species: the admin check must learn
  `SubspeciesOf` before anyone runs heal on a converted master.
- The app's readers load the result; Hooded Crow is a species-level entry beside *Corvus corone*,
  in its folder.
- Local website: 430 pages per language, 63,602 links, **0 broken** apart from the blog index (this
  master has none yet) — after copying 842 full-size JPGs to their new paths by hand (known gap 3).

**New for the code step**
- Numbered names are hard-coded: `HTMLtemplates/intro-en.html` / `intro-nl.html` (the dragonfly and
  butterfly tiles link to `003 odonata/` and `022 lepidoptera/`), `batch/sv1MakeReposity.py:213-214`
  (a new master's link pages), and comments/logic in `db/master_db.py:726, 1636` and
  `gui/biota_source.py:293` that rely on the numbers for sorting.
- The admin check / heal must treat a picture whose species is a `SubspeciesOf` its folder's species
  as in place.

**17 Oct — subspecies names and a second run.** A subspecies never keeps a name its species also
has; without a name of its own it is called "*species name* (ssp *epithet*)" in English and Dutch,
as the author did for the Western Yellow Wagtail and the Turkey Vulture. Green Jay and Long-legged
Buzzard are subspecies again (regional): "green jay (ssp luxuosus)" / "groene gaai",
"long-legged buzzard (ssp cirtensis)" / "arendbuizerd (ssp cirtensis)".
**Running the conversion twice changes nothing:** tables built from the converted Taxonomy 3
give 444 same, 4 "subspecies — already converted", 0 files to move, 0 new names; the alias table is
merged, never replaced (a second run knows only today's names).

**17 Oct — after the second test (code).** Taxa sorted by Sequence (website, Biota Browser);
aliases (`speciesnames.csv`) checked before GBIF; GBIF never adds a bird or mammal on a converted
list; the intro tiles follow the list; the admin check accepts subspecies and now also walks
`topics/` (it reported every landscape picture as dead — on every master); `svMasterRebuildWeb
--pictures` sends every picture to its current place once. **Guard:** a trip made before its
master's conversion (Taxonomy 3's `spaindb`) is refused at Publish Day, at the species-list merge and
when adding learned species to the master — it would bring the numbered names back. Converting a
trip along with its master is the next design question (its species list, `species.csv`, review
days); until then such a trip can be viewed, not published.
