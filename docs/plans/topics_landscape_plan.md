# Topics / Landscape in the Master — architectural half

Front half shipped (3a47983): landscapes can be **declared + titled** in the app
(`is_blog_extra` marker, editable Headline, card = headline). Today they are still
trip-only — `sv6Freeze` **excludes** them from the master, so they are never backed
up and, after a trip retire, survive only as loose un-indexed files.

This plan gives a landscape (and any future non-species topic) a **home in the
master**: an on-disk tree, DB rows, counters, and daily backup — beside `biota/`.

## The one insight this plan rests on

The master's tree / `TargetPath` / `species.csv` / DB `species` table / counters are
**generic chain-walkers**, not taxonomy-specific:

- `TargetPath` = walk the `Up` chain to a root (Up=`""`), joining `ScientificName`s
  (`specieslib.getPathToSpecies`). Root today is `biota`.
- A picture earns a home only because its subject resolves into an `sc_dict` chain
  (`speciesclassification.addSpecies`, GBIF). A landscape resolves to `""` → excluded
  at freeze (`sv6Freeze.AppendChangedPictureData`, `:191`).

So a landscape needs a **parallel chain**, not new machinery: a `topics` root with
`topics/landscape/<leaf>/`. Feed that synthetic chain into `sc_dict`/`species_dict`
and every downstream consumer works unchanged.

**Corollary — "landscape first" vs "more topics" is a false choice.** The mechanism
is identical; only the number of registry rows you *validate against* differs.
Build the generic mechanism, seed it with ONE topic (landscape). Adding "planes"
later is a data edit + a test, never code.

## Two decisions to lock before coding

> **LOCKED 2026-07-24:** D1 = **(i) reuse `species.csv` + the DB `species` table**,
> contained behind `is_topic_node()`. Validation scope = **generic mechanism, seeded
> with the single `landscape` registry row** (adding a topic later is data + a test).

### D1 — where topic rows live (the real architectural fork)

- **(i) Reuse `species.csv` + the DB `species` table** (recommended). Topic leaves are
  just more rows under a `topics` root. Almost no new plumbing (checkpoint / health /
  backup already cover these 3 CSVs). Cost: every species-keyed query/page/map/counter
  must learn to **skip topic nodes** — contained behind ONE predicate
  `is_topic_node(name)` = root ancestor is `topics`.
- **(ii) Separate `topics.csv` + a new DB `topics` table.** Clean isolation, no ripple
  predicate. Cost: new writer, new table in the schema (bump `SCHEMA_VERSION`), new
  checkpoint + health + backup paths — roughly doubles the DB work.

Recommendation: **(i)**. The ripple is real but small and auditable; (ii) pays a large,
permanent plumbing tax to avoid a one-line predicate.

### D2 — the leaf rule per topic (what `<leaf>` is)

The registry maps a topic keyword → how to build its chain. For **landscape** the
natural leaf is the **year** of `DateTimeOriginal` → `topics/landscape/2026/`. A future
"planes" topic would take its leaf from a second subject token → `topics/planes/F16/`.
Registry row shape (declarative, data not code):

```
topic_keyword ; display_nl ; display_en ; leaf_rule ; on_website
landscape     ; landschap  ; landscape  ; year       ; no
```

`leaf_rule ∈ {year, none, token}`. `on_website=no` keeps landscapes off the species
site (their whole point) while still backing them up.

## Order of work (each phase independently testable)

### Phase 1 — topic registry + chain resolution (the mechanism)
- New declarative registry (`engine/topics.py` + a small CSV/ini in the master
  metadata). Replaces the hardcoded `LANDSCAPE_KEYWORDS` set as the source of truth
  (keep the constant as a fallback seed).
- Seed a `topics` root node (Up=`""`) beside `biota` in `sc_dict`/`species_dict`.
- `resolve_topic(subject, picture) -> leaf_scientificname | None`: for a blog-extra
  subject, synthesize the chain `topics → <topic> → <leaf>` and return the leaf, so
  `getSpecies`/`addSpeciesInformation` route it exactly like a species.
- **Test:** unit — a landscape UPN from 2026 yields `TargetPath == "topics/landscape/2026/"`
  and a leaf node with the right `Up` chain; a normal species is untouched;
  `['robin','landscape']` still resolves to robin.

### Phase 2 — dry-run scan + report (read-only, touches nothing)
- Reuse the parallel exiftool scan (159k files ≈ 5m25s @16w). Candidates need only
  `XMP:Subject`/`IPTC:Keywords`.
- Report per candidate: UPN, resolved `topics/…` target, rating+colour (**from the
  `.dop`, NOT the `.xmp`** — the trap), whether a rendered JPG exists (blog-extra needs
  `BestJPG`), and CONFLICTS (already in master / no leaf resolvable).
- **Test:** run against a real trip folder; eyeball the report; zero writes.
  ([[feedback_review_over_autofix]] — dry-run report → review → confirmed apply.)

### Phase 3 — reviewed ingest into `topics/` (coupled to Phase 4 — do together)
- `sv6Freeze`: stop dropping blog extras; route them via their `topics/…` `TargetPath`
  like any species (files + picture rows + species-table rows under `topics`).
- MasterDB: topic leaves become `species`-table rows (counters, JPGmap, best-pick).
  Backup already gzips the 3 CSVs → landscapes are now backed up.
- Backfill operator (album-scoped, with `.trash` + undo) for pre-fix landscapes.
- **Test:** freeze one real day with a landscape → row lands under `topics/landscape/<yr>/`;
  file physically at that path; DB `check()` green; backup includes it; undo restores.

### Phase 4 — contain the ripple (MANDATORY, same ship as Phase 3)
The moment topic rows enter `species.csv`, the FULL website build + species map +
counters see them. Gate every species-keyed consumer on `is_topic_node()` so topics
DON'T emit species pages, DON'T appear on the distribution map, DON'T count as species.
Audit call-sites: `makeSpeciesDirectory`, species-page loop, `webmap`, counters,
Master Species Viewer, master audits.
- **Test:** a FULL website build after Phase 3 produces the SAME species pages/map as
  before (topics invisible on the species site); blog still shows the landscape card.

### Phase 5 — topic pages on the website (OPTIONAL, later)
Only if wanted: a `topics/landscape/` gallery page distinct from species pages, or a
year album. Purely additive; gated by `on_website` in the registry. Not needed for the
safekeeping goal.

## Recovery / durability note (why this matters)
A landscape's master record rebuilds from files beside the RAW even with no trip repo:
subject+GPS from `.xmp`, **rating+colour from `.dop`**, date/EXIF from RAW. Once a
landscape lives under `topics/`, retire/backup/heal all cover it like any master file.

## Open questions parked for later
- What "best" means for a topic leaf (highest-rated of the year — probably fine).
- Whether topic leaves need qualifiers (no).
- Migration backfill scope (all historical landscapes vs. go-forward only).

See [[project_landscapes_topics]] [[project_blog_extras]] [[project_master_db]]
[[project_file_policy_dop_dng]] [[feedback_review_over_autofix]].
