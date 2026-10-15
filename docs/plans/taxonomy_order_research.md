# Ch. 6 — species order: step 1 research (15 Oct 2026)

Read-only. Nothing in the master or the code was changed. Scripts were run against
`E:/Dropbox/naturedb/metadata/active` (speciescollection.csv, species.csv).
Companion file: [taxonomy_bird_mapping_trial.csv](taxonomy_bird_mapping_trial.csv) —
every pictured bird with its proposed eBird match.

## 1. What the master holds

**2,506 species with pictures (82,937 pictures)** out of 18,699 in the species list.
118 orders, 548 families in the list.

| kingdom / class | species w/ pictures | pictures | orders | families |
|---|---:|---:|---:|---:|
| animalia / aves | 1,755 | 72,902 | 42 | 174 |
| animalia / insecta | 301 | 3,654 | 11 | 87 |
| plantae / magnoliopsida | 213 | 664 | 22 | 45 |
| animalia / mammalia | 101 | 4,478 | 9 | 34 |
| plantae / liliopsida | 56 | 451 | 6 | 12 |
| animalia / squamata, crocodylia, testudines | 26 | 537 | — | 18 |
| fungi (4 classes) | 19 | 29 | 9 | 15 |
| everything else (arachnida, gastropoda, amphibia, ferns, mosses…) | 35 | 186 | | |

Birds are 70 % of the species and 88 % of the pictures. Insects come next, then plants,
then mammals.

### What is wrong today

- **Only birds and two insect orders carry a number.** 42 orders (all bird orders +
  `003 odonata`, `022 lepidoptera`) and 310 families are numbered. Everything else —
  the rest of the insects, all plants, mammals, reptiles, fungi — has no number and sorts
  alphabetically.
- **The numbers are already per parent**, not global: `003 odonata` (under insecta)
  and `003 rheiformes` (under aves) share 003; families restart at 001 inside each order.
  That is the right meaning — it just lives in the wrong place (the name).
- **Doubled taxa: 9 orders and 18 families** appear both numbered and unnumbered
  (the backlog says eight orders; lepidoptera is the ninth). The unnumbered twin
  always holds the stragglers: `040 passeriformes` 885 species vs `passeriformes` 12;
  `022 lepidoptera` 67 vs `lepidoptera` 18; most other twins hold 1–2 species.
- **Gaps in the ranks.** GBIF has no class Reptilia: squamata, crocodylia and testudines are
  *classes*, and their species hang under a family with **no order** (shown as `?`).
  The new design must allow a missing rank, not invent one.
- Landscape topics (`2025`, `2026`) are in species.csv as species without a kingdom —
  expected, but the converter must leave them alone.

## 2. Sources for an order — what exists

| group | source | per | availability | verdict |
|---|---|---|---|---|
| **birds** | eBird/Clements `TAXON_ORDER` | species | `https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=csv` — **no key needed**, 17,891 rows (11,167 species), version 2025 is latest; 2026 due ~end Oct | ✅ use |
| birds (alt.) | AviList (June 2025, IOC + Clements merged) | species | download from avilist.org; **Observation.org follows AviList** | keep in mind |
| **mammals** | Mammal Diversity Database v2.5 (Zenodo 10.5281/zenodo.17033774) | species | CSV in a ZIP; whether rows carry a sequence column is **not verified yet** | order/family level by hand is enough (9 orders) |
| mammals (alt.) | Jones & Bartlett ch. 4 (your link) | order | PDF, reading only | fine to type the order list from |
| **insects** | Wikipedia list (based on Sasaki et al. 2013) / RES | order | groups in phylogenetic order (Apterygota → Palaeoptera → Polyneoptera → Paraneoptera → Holometabola), *alphabetical within each group* | ✅ hand table, ~30 rows |
| **plants** | APG (Linear APG numbering, Haston et al. 2009 for APG III) | order + family | published numbered sequence; APG IV equivalent **to confirm** | ✅ best non-bird source |
| fungi, molluscs, rest | none worth chasing | — | — | alphabetical below class |
| Observation.org / waarneming.nl | public API | — | `/api/v1/species/<id>/` returns name, group, status — **no sort field**; API is limited to approved apps. `/api/v1/species-groups/` gives 20 groups in a fixed, sensible order (Birds, Mammals, Reptiles & Amphibians, Butterflies, Moths, Dragonflies, …) | ❌ no species order; ✔ their **group** order is a nice idea for the top level |
| Catalogue of Life (ColDP) | `ordinal` field | sibling | “an integer to specify an optional custom sort order for sibling taxa sharing the same parentID” | ✅ **the model to copy** |

So your hunch was half right: waarneming.nl does show species in order on its site, but the
order isn't available to us. Their top-level *group* list is worth borrowing as the order of
the first screen.

**The ColDP `ordinal` is exactly what we already do implicitly**: a sort number per parent,
not a global index. Adopting that meaning means a number is only ever compared with its
siblings, so birds, insects and plants can each come from a different source without
colliding.

## 3. Trial: matching the pictured birds to eBird

| method | birds | share | safe? |
|---|---:|---:|---|
| exact scientific name | 1,590 | 90.6 % | ✅ |
| exact match on an eBird *subspecies* row (we hold 3 trinomials) | 3 | 0.2 % | ✅ sort as the subspecies row |
| GBIF says our name is a synonym → accepted name is in eBird (*Anas penelope → Mareca penelope*) | 54 | 3.1 % | ✅ |
| same species epithet **inside the same family** (*Accipiter gentilis → Astur gentilis*, *Sylvia communis → Curruca communis*) | 74 | 4.2 % | ⚠ propose, you review |
| left over — lumps, Latin endings, spelling | 34 | 1.9 % | ✋ hand list |

**⚠ The pitfall, measured.** Matching the epithet alone (without the family) filed
*Bubulcus coromandus* (Eastern Cattle Egret) under *Clamator coromandus*, a cuckoo, and
*Buphagus erythrorhynchus* (Red-billed Oxpecker) under a hornbill. Any automatic step
must stay inside the family, and anything not certain is a proposal, never an
apply — the same review-over-autofix rule as elsewhere.

The 34 for a hand check include real lumps (*Upupa africana*, *Lanius pallidirostris*,
*Milvus aegyptius*, *Bubulcus coromandus*, *Sylvia althaea*), Latin ending changes
(*Microcarbo pygmeus/pygmaeus*, *Philydor rufum/rufus*) and genuine ambiguity
(*Accipiter melanoleucus* → three candidates). An unmatched species can still sort
sensibly: next to its genus-mates, or at the end of its family — never in another family.

**Placement differs too**, not just the sequence: 68 pictured birds sit in a different
family in eBird (biggest: 43 *emberizidae → passerellidae*, 6 *psittacidae →
psittaculidae*, 6 *cardinalidae → thraupidae*) and 6 in a different order
(5 New World vultures *accipitriformes → cathartiformes*, 1 potoo). This is the
"placement" part of the design: whose parent chain wins — GBIF's or eBird's — is a
decision to make, separate from the sort order.

Whole bird list (10,680 in the species list, most without pictures): 9,243 exact,
1,434 not — the same ~13 % drift, ready-made for the starter list too.

## 4. What this means for the design (step 2)

1. **Sequence = an `ordinal` per parent, with its source**, in its own table — never in
   the name. Today's numbers become the first values (source "legacy"), so nothing
   reorders the day it lands.
2. **Birds per species from eBird**; orders and families get the lowest `TAXON_ORDER`
   of their members. Yearly refresh = re-run the match; new splits get decimals already.
3. **Everything else at order (and for plants family) level** from a small hand-kept
   table: ~30 insect orders, ~10 mammal orders, plant orders/families from APG.
   Below that: alphabetical. Measured size of the hand work: 70 distinct non-bird orders
   in the species list (40 distinct bird orders come from eBird).
4. **Allow missing ranks** (reptiles have no order). *Already partly there:* the parent
   walk in `specieslib.py:126` and `projectlib.py:972` skips levels — that's how crocodiles
   and turtles work today.
5. **Placement is a separate decision** from order (68 birds in another family by eBird).
6. **The doubled taxa vanish by construction** once the number is out of the name:
   `040 passeriformes` and `passeriformes` are the same key.
7. **Storage — superseded, see §6:** the folder tree stays Kingdom → … → species.
   Species folders don't change; only order/family folders are renamed (with collision
   handling), so the data movement is small.

## 5. Open questions for you (answered 15 Oct — see §6)

- Birds: **eBird/Clements** (downloadable now, yearly) or **AviList** (what
  Observation.org follows)? Recommendation: eBird now — it's one HTTP call; AviList
  can be a second source in the same table later.
- Placement: keep **GBIF** as the parent chain and use eBird only for the sort order, or
  let **eBird** decide bird families too? Recommendation: GBIF for placement (all groups
  alike), eBird for order — fewer moving parts; reconsider if the 68 cause odd pages.
- Top level: borrow Observation.org's **group order** (Birds, Mammals, Reptiles &
  Amphibians, Butterflies, Dragonflies, …) for the first screen instead of
  Kingdom → Phylum → Class?

## 6. Your answers (15 Oct) and what followed from them

**Decisions**
- The doubled orders/families are **splits added after the initial IOC list** — known
  and visible.
- **Birds: AviList** — Observation.org following it is the deciding argument. Order is
  only used *within* a class/order/family.
- **GBIF is the master** for placement — it's the one source that covers all species.
  (He'll contact the Dutch organisation about their GBIF servers; independent of this.)
- **Use the new full list**, not the author's current list.
- **The tree stays Kingdom → Phylum → Class** (database and folders). Observation.org's
  friendlier groups can sit *on top* as a view.
- **Storage stays in the tree.** Species folders don't change; only order/family folders
  are renamed. Collisions have to be handled. Dropbox catching up is fine.
- **An alias mechanism is needed** — for scientific names too, not just common names.
  *Sylvia communis* is the name in the 1993 and 2023 books; Observation.org says
  *Curruca communis*. Common names also change, sometimes for non-scientific reasons
  (Vlaamse gaai → Gaai), with no other names involved.

**Made-up levels in the current list.** 78 order/family names carry an extra level fused
into the name, because GBIF had no slot for it:
- **Odonata, a suborder *above* the family** — `001 zygoptera hemiphlebiidae`,
  `004 zygoptera lestidae`, … (~47).
- **Butterflies, a subfamily *below* the family** — `026 nymphalidae satyrinae`,
  `015 lycaenidae polyommatinae`, … (~26).
- **5 × `incertae sedis N`** families under passeriformes.

Books use more groupings for insects. The design needs **optional intermediate ranks**
(suborder, superfamily, subfamily) as real levels with their own `ordinal`, rather than
fused names.

**AviList v2025 (11 Jun 2025, CC BY 4.0)** — `AviList-v2025-11Jun-short.xlsx` from
avilist.org: 46 orders, 252 families, 2,376 genera, 11,131 species, 19,879 subspecies.
Columns include **`Sequence`** (on every row, so order, family, genus and species all have
one), `Order`, `Family`, `Scientific_name`, `English_name_AviList`, `Decision_summary`
(splits/lumps explained) and **`AvibaseID`**. No 2026 edition yet.
An Avibase ID is a *concept* ID — it stays when the scientific name changes, which makes
it a natural anchor for the bird half of the alias mechanism.

**Does current GBIF differ from AviList? Yes, materially.** All 11,131 AviList species run
through the GBIF species match (strict, class Aves):

| GBIF says | species | share |
|---|---:|---:|
| same accepted name | 9,760 | 87.7 % |
| **a synonym of another species** (split not recognised, or genus move not followed) | 1,061 | 9.5 % |
| unknown | 257 | 2.3 % |
| other / errors | 53 | 0.5 % |

Even where the name is the same, **388 species sit in a different family** (155
*psittaculidae → psittacidae*, 33 *tityridae → cotingidae*, 21 *paradoxornithidae →
sylviidae*, …) and **103 in a different order** (55 *galbuliformes → piciformes*, 16
sandgrouse, 16 frogmouths, 9 owlet-nightjars, 7 New World vultures).

⚠ **The wrong-bird pitfall is built in.** For a split that AviList recognises and GBIF
doesn't (*Lagopus scotica* → GBIF: *Lagopus lagopus*; *Guttera edouardi* → *Guttera
pucherani*), a GBIF lookup files the photo under the *old, lumped* species. ~1,300
AviList birds can't be represented faithfully if GBIF decides bird names.

**So "GBIF is the master" needs one exception to decide:**
- **A. GBIF decides everything; AviList only gives the order.** Simple, one rule for all
  groups, but ~1,300 current bird species are lumped back or missing, and families/orders
  follow GBIF (Psittacidae, Piciformes for jacamars).
- **B. GBIF for everything above class and for all non-birds; AviList decides bird
  species, families and orders (recommended).** AviList is complete for birds, has the
  sequence, the IDs and the split/lump notes, and is what Observation.org shows. GBIF
  still supplies Animalia → Chordata → Aves. Cost: two sources, with a seam at class level.

(GBIF is also moving its backbone towards the Catalogue of Life; whether that closes the
gap for birds is not checked.)

**Decided 15 Oct: A.** GBIF decides names and placement for every group, birds included;
AviList supplies only the sort order. Accepted consequences: splits GBIF doesn't recognise
stay lumped, and families/orders follow GBIF (Psittacidae, Piciformes for jacamars).

What A asks of the design:
- **A bridge from GBIF name → AviList `Sequence`.** Direct for ~88 % of species. For GBIF
  names AviList doesn't use (old genus, e.g. *Sylvia communis*), go via the alias table
  (AvibaseID / GBIF synonyms). What still doesn't match sorts next to its genus-mates,
  or at the end of its family — never guessed across families.
- **Family and order sequence derived from members:** a GBIF family sorts at the lowest
  AviList `Sequence` of its species. That works even where GBIF and AviList draw families
  differently.
- **Yearly refresh** (new GBIF backbone, new AviList) = re-run the bridge and review the
  differences, never auto-apply.
